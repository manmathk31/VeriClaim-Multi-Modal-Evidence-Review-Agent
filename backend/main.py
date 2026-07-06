from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import os
from typing import List, Optional
import asyncio
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from fastapi import Query, HTTPException
import re
import time
import uuid
from contextlib import asynccontextmanager

# ==========================================
# SECURE ADMIN CREDENTIALS
# ==========================================
ACTIVE_ADMIN_TOKENS = set()

# Load environment variables (like GEMINI_API_KEY, SUPABASE_URL) from the root .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

# Ensure GOOGLE_API_KEY is populated so LangChain's Google integration doesn't mistakenly fall back to OpenAI
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(cleanup_old_uploads())
    asyncio.create_task(cleanup_rate_limit_history())
    asyncio.create_task(cleanup_old_logs())
    yield

app = FastAPI(title="VeriClaim Multi-Agent Platform API", lifespan=lifespan)

# Initialize Supabase Admin Client
# For production security:
# - SUPABASE_SERVICE_KEY is used internally on the backend to bypass RLS (Service Role Key)
# - SUPABASE_ANON_KEY is exposed to the frontend only (Anon Public Key)
supabase_url = os.environ.get("SUPABASE_URL", "")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
supabase: Client = None
if supabase_url and supabase_service_key:
    supabase = create_client(supabase_url, supabase_service_key)

# Inject Supabase client into agent tools (fixes circular import)
from agents.tools import init_tools
init_tools(supabase)

# Initialize SessionManager for persistent conversation storage
# Replaces the old in-memory session_store dict.
# DB is the source of truth; in-memory cache keeps active WebSocket sessions fast.
from database.session_manager import SessionManager
session_manager = SessionManager(supabase)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi import Request
import html

# Simple In-Memory Rate Limiting
rate_limit_history = {} # (client_ip, path) -> list of timestamps

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # We only rate limit API routes, not static assets, web page HTMLs, or websockets
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.time()
        path = request.url.path

        # Rules:
        # - Sensitive endpoints: /api/v1/admin/auth and /api/v1/upload (limit 5 requests per minute)
        # - Other endpoints: limit 100 requests per minute
        is_sensitive = path in ["/api/v1/admin/auth", "/api/v1/upload"]
        limit = 5 if is_sensitive else 100
        window = 60 # 60 seconds

        key = (client_ip, path)
        if key not in rate_limit_history:
            rate_limit_history[key] = []

        # Filter out timestamps older than the window
        rate_limit_history[key] = [t for t in rate_limit_history[key] if now - t < window]

        if len(rate_limit_history[key]) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."}
            )

        rate_limit_history[key].append(now)
        return await call_next(request)

# Add middlewares (FastAPI processes them in reverse order, so CORSMiddleware runs first, then RateLimitMiddleware)
app.add_middleware(RateLimitMiddleware)

# CORS configuration restricted to specific origins
allowed_origins_raw = os.environ.get("ALLOWED_ORIGINS", "")
if allowed_origins_raw:
    origins = [origin.strip() for origin in allowed_origins_raw.split(",") if origin.strip()]
else:
    origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload directory exists
os.makedirs("uploads", exist_ok=True)


def sanitize_filename(filename: str) -> str:
    """Sanitizes a filename to prevent path traversal attacks."""
    # Remove any path components and dangerous characters
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\-_. ]', '', filename)
    return filename or "unnamed_file"


def verify_user_token(token: str) -> Optional[str]:
    """Verifies a Supabase JWT token and returns user_id or None."""
    if not supabase or not token:
        return None
    try:
        auth_response = supabase.auth.get_user(token)
        if auth_response and auth_response.user:
            return str(auth_response.user.id)
    except Exception as e:
        print(f"JWT Verification failed: {e}")
    return None


def translate_text(text: str, target_lang: str) -> str:
    """
    Translates text to either English, Marathi, or Hindi using Gemini.
    target_lang can be: 'en', 'mr', 'hi'.
    """
    if not text.strip() or (target_lang == "en" and all(ord(c) < 128 for c in text)):
        # Already English/ASCII, no need to translate
        return text
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        model = genai.GenerativeModel("gemini-3.1-flash-lite")
        
        lang_names = {'en': 'English', 'mr': 'Marathi', 'hi': 'Hindi'}
        target_lang_name = lang_names.get(target_lang, 'English')
        
        prompt = (
            f"You are a professional, high-fidelity translator. Translate the following text to {target_lang_name}. "
            "Only return the direct translation. Do not write explanations, greetings, or notes. Keep formatting, context, names, and numbers exactly the same.\n\n"
            f"Text to translate:\n{text}"
        )
        response = model.generate_content(prompt)
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        print(f"[TRANSLATE] Error translating to {target_lang}: {e}")
    return text


def translate_to_english_if_needed(text: str) -> str:
    """Detects language and translates to English if the text is not English."""
    if not text.strip() or all(ord(c) < 128 for c in text):
        # Already ASCII (likely English), skip
        return text
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        model = genai.GenerativeModel("gemini-3.1-flash-lite")
        prompt = (
            "Detect the language of the following text. If it is already in English, return it exactly as-is without any modification. "
            "If it is in another language (such as Marathi or Hindi), translate it professionally to English. "
            "Only return the final English text (or the original text if it was already English). Do not include any explanations or extra words.\n\n"
            f"Text:\n{text}"
        )
        response = model.generate_content(prompt)
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        print(f"[TRANSLATE-EN] Error: {e}")
    return text



# ==========================================
# CONNECTION MANAGER
# ==========================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.admin_connections: List[WebSocket] = []
        self.user_notifications: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, is_admin: bool = False, user_id: str = None):
        await websocket.accept()
        if is_admin:
            self.admin_connections.append(websocket)
        elif user_id:
            self.user_notifications[user_id] = websocket
        else:
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket, is_admin: bool = False, user_id: str = None):
        if is_admin and websocket in self.admin_connections:
            self.admin_connections.remove(websocket)
        elif user_id and user_id in self.user_notifications:
            del self.user_notifications[user_id]
        elif websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_to_admins(self, message: str):
        disconnected = []
        for connection in self.admin_connections:
            try:
                await connection.send_text(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.admin_connections.remove(conn)
            
    async def notify_user(self, user_id: str, message: dict):
        if user_id in self.user_notifications:
            try:
                await self.user_notifications[user_id].send_text(json.dumps(message))
            except Exception:
                del self.user_notifications[user_id]

manager = ConnectionManager()


# ==========================================
# BACKGROUND TASKS
# ==========================================

async def cleanup_old_uploads():
    """Background task: Deletes evidence files older than 24 hours to prevent disk exhaustion."""
    upload_dir = "uploads"
    while True:
        try:
            now = time.time()
            if os.path.exists(upload_dir):
                for filename in os.listdir(upload_dir):
                    file_path = os.path.join(upload_dir, filename)
                    if os.path.isfile(file_path) and os.stat(file_path).st_mtime < now - 86400:
                        os.remove(file_path)
                        print(f"[CLEANUP] Deleted old evidence file: {filename}")
        except Exception as e:
            print(f"[CLEANUP] Error during cleanup: {e}")
        # Sleep for 1 hour before checking again
        await asyncio.sleep(3600)

async def cleanup_rate_limit_history():
    """Background task: clears stale IPs from the rate limit dict every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            now = time.time()
            stale_keys = [
                key for key, timestamps in list(rate_limit_history.items())
                if not any(now - t < 60 for t in timestamps)
            ]
            for key in stale_keys:
                del rate_limit_history[key]
            if stale_keys:
                print(f"[RATE LIMIT] Cleared {len(stale_keys)} stale entries.")
        except Exception as e:
            print(f"[RATE LIMIT] Cleanup error: {e}")

async def cleanup_old_logs():
    """Background task: deletes admin action logs older than 30 days weekly."""
    while True:
        await asyncio.sleep(604800)  # 7 days
        try:
            session_manager.cleanup_old_logs(days=30)
        except Exception as e:
            print(f"[CLEANUP] Log cleanup error: {e}")


# ==========================================
# HTTP ROUTES
# ==========================================

@app.get("/api/v1/config")
async def get_config():
    """Provides public config to the frontend (anon key only, never service key)."""
    return {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
        "SUPABASE_KEY": os.environ.get("SUPABASE_ANON_KEY", "")
    }

@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {"status": "ok", "service": "vericlaim"}


@app.post("/api/v1/upload")
async def upload_evidence(
    file: UploadFile = File(...),
    token: str = Query(default="")
):
    """
    Handle image uploads from the frontend.
    Saves the file to disk and associates it with the user's session.
    """
    # Authenticate the uploader strictly
    user_id = verify_user_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication failed. Invalid or missing token.")

    # Validate MIME type
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="Unsupported file type. Only JPEG, PNG, and WebP are allowed.")

    # Validate file size (3MB max)
    content = await file.read()
    if len(content) > 3 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 3MB.")

    # Sanitize filename to prevent path traversal
    safe_name = sanitize_filename(file.filename)
    # Add timestamp to prevent overwrites
    timestamp = int(time.time() * 1000)
    safe_name = f"{timestamp}_{safe_name}"

    storage_path = f"{user_id}/{safe_name}"

    try:
        supabase.storage.from_("evidence").upload(
            path=storage_path,
            file=content,
            file_options={"content-type": file.content_type}
        )
    except Exception as e:
        print(f"[UPLOAD] Supabase Storage Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file to cloud storage.")

    public_url = supabase.storage.from_("evidence").get_public_url(storage_path)

    # Track this image in the user's session (persisted to DB)
    session_manager.add_evidence(user_id, safe_name, storage_path=storage_path, public_url=public_url)

    print(f"[UPLOAD] File '{safe_name}' saved for user {user_id} → Supabase: {storage_path}")

    # Notify admins that evidence was uploaded
    await manager.broadcast_to_admins(json.dumps({
        "type": "log",
        "agent": "System",
        "message": f"Evidence '{safe_name}' uploaded by user {user_id} to secure storage."
    }))

    return {"status": "success", "filename": safe_name, "user_id": user_id}


# ==========================================
# DASHBOARD API ENDPOINTS
# ==========================================

def _auth_user(authorization: str = Header(None)) -> str:
    """Extracts and verifies user from Authorization: Bearer <token> header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1]
    user_id = verify_user_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    return user_id

def _auth_admin(authorization: str = Header(None)) -> str:
    """Strictly authenticates an admin using the isolated secret token system."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1]
    
    if token not in ACTIVE_ADMIN_TOKENS:
        raise HTTPException(status_code=403, detail="Admin access denied. Invalid or expired token.")
    
    return "admin"


@app.get("/api/v1/profile")
async def get_profile(authorization: str = Header(None)):
    """Returns the user's profile info and policy details."""
    user_id = _auth_user(authorization)
    try:
        # Get user info from Supabase auth
        auth_response = supabase.auth.admin.get_user_by_id(user_id)
        user_data = auth_response.user if auth_response else None

        # Get policy info
        policy_response = supabase.table("policies").select("*").eq("id", user_id).execute()
        policy = policy_response.data[0] if policy_response.data else None

        # Count claims
        claims_response = supabase.table("claims").select("id, final_verdict").eq("user_id", user_id).execute()
        claims = claims_response.data or []

        return {
            "user": {
                "id": user_id,
                "email": user_data.email if user_data else "",
                "name": user_data.user_metadata.get("full_name", "") if user_data else "",
                "created_at": str(user_data.created_at) if user_data else ""
            },
            "policy": policy,
            "stats": {
                "total_claims": len(claims),
                "approved": sum(1 for c in claims if c.get("final_verdict") == "AUTO_APPROVE"),
                "flagged": sum(1 for c in claims if c.get("final_verdict") == "FLAGGED"),
                "denied": sum(1 for c in claims if c.get("final_verdict") == "DENIED"),
                "pending": sum(1 for c in claims if not c.get("final_verdict"))
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/conversations")
async def list_conversations(authorization: str = Header(None)):
    """Returns all conversations for the authenticated user."""
    user_id = _auth_user(authorization)
    try:
        conversations = session_manager.get_user_conversations(user_id)
        return {"conversations": conversations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, authorization: str = Header(None)):
    """Returns a single conversation with all messages and evidence."""
    user_id = _auth_user(authorization)
    try:
        conversation = session_manager.get_conversation_with_messages(conversation_id, user_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"conversation": conversation}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/conversations")
async def create_conversation(authorization: str = Header(None)):
    """Creates a new conversation for the user (used by 'New Claim' button)."""
    user_id = _auth_user(authorization)
    try:
        session = session_manager.start_new_conversation(user_id)
        return {
            "status": "created",
            "conversation_id": session.get("conversation_id")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# ADMIN API ENDPOINTS (Phase 5)
# ==========================================

class AdminLoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/v1/admin/auth")
async def admin_login(payload: AdminLoginRequest):
    """Authenticates the master admin and issues a secure in-memory session token."""
    if payload.username == ADMIN_USERNAME and payload.password == ADMIN_PASSWORD:
        token = str(uuid.uuid4())
        ACTIVE_ADMIN_TOKENS.add(token)
        return {"token": token}
    raise HTTPException(status_code=401, detail="Invalid admin credentials.")

@app.post("/api/v1/admin/logout")
async def admin_logout(authorization: str = Header(None)):
    """Logs out the admin by invalidating the token."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        ACTIVE_ADMIN_TOKENS.discard(token)
    return {"status": "success"}

@app.get("/api/v1/admin/stats")
async def get_admin_stats(authorization: str = Header(None)):
    _auth_admin(authorization)
    return session_manager.get_admin_stats()

@app.get("/api/v1/admin/claims")
async def get_admin_claims(authorization: str = Header(None)):
    _auth_admin(authorization)
    return {"claims": session_manager.get_all_claims_admin()}

@app.get("/api/v1/admin/users")
async def get_admin_users(authorization: str = Header(None)):
    _auth_admin(authorization)
    return {"users": session_manager.get_all_users_admin()}

@app.get("/api/v1/admin/conversations/{conversation_id}")
async def get_admin_conversation(conversation_id: str, authorization: str = Header(None)):
    """Admin endpoint to fetch a single conversation without user ownership checks."""
    _auth_admin(authorization)
    try:
        conversation = session_manager.get_conversation_admin(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"conversation": conversation}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class VerdictRequest(BaseModel):
    verdict: str
    reasoning: str = "Admin Override"

@app.post("/api/v1/admin/claims/{claim_id}/verdict")
async def update_claim_verdict(claim_id: str, payload: VerdictRequest, authorization: str = Header(None)):
    _auth_admin(authorization)
    
    # Fetch claim to get user_id for notification
    try:
        claim_res = supabase.table("claims").select("user_id").eq("id", claim_id).execute()
        target_user_id = claim_res.data[0]["user_id"] if claim_res.data else None
    except Exception:
        target_user_id = None

    payload.reasoning = html.escape(payload.reasoning.strip())
    success = session_manager.update_claim_status_admin(claim_id, payload.verdict, payload.reasoning)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update claim verdict.")
    
    # Notify the user via WebSocket if we found them
    if target_user_id:
        await manager.notify_user(target_user_id, {
            "type": "notification",
            "title": f"Claim {payload.verdict.capitalize()}",
            "message": f"Your claim has been {payload.verdict} by administration.",
            "claim_id": claim_id,
            "status": payload.verdict
        })
    
    return {"status": "success"}

class BanRequest(BaseModel):
    reason: str = "Policy Violation"
    hard_ban: bool = False

@app.post("/api/v1/admin/users/{user_id}/ban")
async def ban_user(user_id: str, payload: BanRequest, authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        reason_sanitized = html.escape(payload.reason.strip())
        session_manager.ban_user(user_id, reason_sanitized, payload.hard_ban)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/admin/users/{user_id}/unban")
async def unban_user(user_id: str, authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        session_manager.unban_user(user_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/admin/users/{user_id}")
async def delete_user(user_id: str, authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        session_manager.delete_user(user_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/admin/users/{user_id}/data")
async def clear_user_data(user_id: str, authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        session_manager.clear_user_data(user_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/admin/system/memory")
async def admin_clear_memory(authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        # Clear Hugging Face local persistent memory if configured
        hf_memory_dir = os.environ.get("CREWAI_STORAGE_DIR", "./crewai_memory")
        if os.path.exists(hf_memory_dir):
            import shutil
            shutil.rmtree(hf_memory_dir)
            print(f"[MEMORY] Cleared local storage volume: {hf_memory_dir}")
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/admin/system/logs")
async def admin_clear_logs(authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        success = session_manager.clear_all_logs()
        if not success:
            raise HTTPException(status_code=500, detail="Failed to clear system logs.")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/admin/system/ratelimit")
async def admin_clear_ratelimit(authorization: str = Header(None)):
    _auth_admin(authorization)
    try:
        rate_limit_history.clear()
        print("[RATE LIMIT] Manually cleared all rate limits.")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# WEBSOCKET: VOICE AGENT
# ==========================================

@app.websocket("/ws/voice_agent")
async def websocket_voice_agent(websocket: WebSocket, token: str = Query(None)):
    """
    Handles the live conversation stream from the user.
    Session data is persisted to Supabase via SessionManager.
    On reconnect or server restart, conversation history loads from DB.
    """
    await manager.connect(websocket)

    # Authenticate user via JWT strictly
    user_id = None
    if supabase and token:
        user_id = verify_user_token(token)
        
    if not user_id:
        await websocket.send_text(json.dumps({
            "text": "Authentication failed or token missing. Please log in again."
        }))
        await websocket.close(code=1008)
        return

    # Reject unconfirmed email accounts
    try:
        user_info = supabase.auth.admin.get_user_by_id(user_id)
        if user_info and user_info.user and not user_info.user.email_confirmed_at:
            await websocket.send_text(json.dumps({
                "text": "Please verify your email address before using VeriClaim."
            }))
            await websocket.close(code=1008)
            return
    except Exception:
        pass  # Non-blocking — if check fails, allow through

    # Get or create the user's session (loads from DB if exists, creates if new)
    session = session_manager.get_or_create_session(user_id)

    # Rebuild parallel English conversation cache if not present
    if "english_conversation" not in session:
        session["english_conversation"] = []
        for msg in session.get("conversation", []):
            role = msg["role"]
            content = msg["content"]
            # Translate message content to English if it's not English
            english_content = translate_to_english_if_needed(content)
            session["english_conversation"].append({"role": role, "content": english_content})

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            user_text = message.get("text", "")
            lang = message.get("lang", "en")  # 'en' | 'mr' | 'hi'

            if not user_text.strip():
                continue

            user_text = html.escape(user_text.strip())

            # ── Persist original language user message to cache + DB (for UI) ──
            session_manager.append_message(user_id, "user", user_text)

            # Translate user message to English for backend agents if lang is not English
            english_user_text = user_text
            if lang != "en":
                english_user_text = translate_text(user_text, "en")

            # Append English user message to parallel English cache
            session["english_conversation"].append({"role": "user", "content": english_user_text})

            # Broadcast original message to admin dashboard
            await manager.broadcast_to_admins(json.dumps({
                "type": "log",
                "agent": "User",
                "message": f"[{lang.upper()}] {user_text}" if lang != "en" else user_text
            }))

            # Broadcast pipeline initialization to admins
            await manager.broadcast_to_admins(json.dumps({
                "type": "log",
                "agent": "Orchestrator",
                "message": f"Processing message #{len(session['conversation'])} with {len(session['images'])} evidence image(s)..."
            }))

            try:
                from agents.crew_orchestrator import VeriClaimCrew
                loop = asyncio.get_event_loop()
                
                def send_telemetry(agent_name, step_info):
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast_to_admins(json.dumps({
                            "type": "log",
                            "agent": agent_name,
                            "message": step_info
                        })),
                        loop
                    )
                    
                crew_system = VeriClaimCrew(telemetry_callback=send_telemetry)

                if session.get("claim_submitted", False):
                    # Route to the new Database-Backed Support flow
                    response_text = await loop.run_in_executor(
                        None,
                        crew_system.process_post_claim,
                        user_id,
                        list(session["english_conversation"])  # Pass English conversation history
                    )
                else:
                    # Standard Interviewer flow
                    is_complete, payload = await loop.run_in_executor(
                        None,
                        crew_system.process_live_claim,
                        user_id,
                        list(session["english_conversation"]),  # Pass English conversation history
                        list(session["images"])
                    )

                    if not is_complete:
                        response_text = payload
                    else:
                        summary, response_text = payload
                        session_manager.mark_claim_submitted(user_id)  # Switch modes + persist to DB
                        # Start backend processing asynchronously (English summary and images)
                        loop.run_in_executor(
                            None,
                            crew_system.run_backend_processing,
                            user_id,
                            session.get("conversation_id"),
                            summary,
                            list(session["images"])
                        )

            except Exception as e:
                import traceback
                traceback.print_exc()
                error_msg = str(e)
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or "rate limit" in error_msg.lower():
                    response_text = "I apologize, but we are experiencing a high volume of requests right now and I need a quick breather! Please give me about 60 seconds and then ask me again."
                else:
                    response_text = f"I encountered an issue processing your claim. Please try again. (Error: {error_msg[:100]})"

            # Translate final response back to user's selected language if not English
            response_text_target = response_text
            if lang != "en":
                response_text_target = translate_text(response_text, lang)

            # ── Persist target language AI response to cache + DB (for UI) ──
            session_manager.append_message(user_id, "ai", response_text_target)

            # Append English response to parallel English cache
            session["english_conversation"].append({"role": "ai", "content": response_text})

            # Broadcast AI response to admin dashboard
            await manager.broadcast_to_admins(json.dumps({
                "type": "log",
                "agent": "Orchestrator",
                "message": f"[{lang.upper()}] {response_text_target}" if lang != "en" else response_text_target
            }))

            # Send response back to the user
            await websocket.send_text(json.dumps({"text": response_text_target}))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        msg_count = len(session.get('conversation', []))
        session_manager.evict_cache(user_id)
        print(f"[WS] User {user_id} disconnected. {msg_count} messages persisted in DB.")


# ==========================================
# WEBSOCKET: ADMIN DASHBOARD
# ==========================================

@app.websocket("/ws/admin_matrix")
async def websocket_admin_matrix(websocket: WebSocket, token: str = Query(None)):
    """The live matrix feed for the Admin Dashboard."""
    if token not in ACTIVE_ADMIN_TOKENS:
        await websocket.close(code=1008)
        return
        
    await manager.connect(websocket, is_admin=True)
    try:
        await websocket.send_text(json.dumps({
            "type": "system",
            "message": "Connected to CrewAI Orchestration Matrix. Listening for agent telemetry..."
        }))
        while True:
            data = await websocket.receive_text()

            # Admins can send overrides or commands
            msg = json.loads(data)
            if msg.get("action") == "override":
                print("Admin Triggered Override:", msg)
                await manager.broadcast_to_admins(json.dumps({
                    "type": "log",
                    "agent": "Admin Override",
                    "message": f"Override action: {msg.get('type', 'unknown')}"
                }))

    except WebSocketDisconnect:
        manager.disconnect(websocket, is_admin=True)


# ==========================================
# WEBSOCKET: USER NOTIFICATIONS
# ==========================================

@app.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket, token: str = Query(None)):
    """User notifications WebSocket for real-time claim updates."""
    user_id = verify_user_token(token) if token else None
    if not user_id:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, user_id=user_id)
    try:
        async def _keepalive():
            while True:
                await asyncio.sleep(30)
                try:
                    await websocket.send_text("ping")
                except Exception:
                    break
        _keepalive_task = asyncio.create_task(_keepalive())
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        finally:
            _keepalive_task.cancel()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id=user_id)

# ==========================================
# STATIC FILES — Serve the frontend
# ==========================================

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_dir = os.path.join(parent_dir, "frontend")

@app.get("/")
async def root():
    return FileResponse(os.path.join(frontend_dir, "home.html"))

app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
