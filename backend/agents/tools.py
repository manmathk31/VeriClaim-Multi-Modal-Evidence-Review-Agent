import os
import json
from crewai.tools import tool

# ==========================================
# DEPENDENCY INJECTION
# Avoids the circular import: tools.py ←→ main.py
# main.py calls init_tools() at startup to inject the Supabase client.
# ==========================================

_supabase = None

def init_tools(supabase_client):
    """Called once from main.py at startup to inject the Supabase client."""
    global _supabase
    _supabase = supabase_client
    print("[TOOLS] Supabase client injected successfully." if _supabase else "[TOOLS] WARNING: Supabase client is None.")


# ==========================================
# TOOL 1: POLICY LOOKUP (Real Supabase Query)
# ==========================================

@tool("Policy Lookup Tool")
def lookup_policy(user_id: str) -> str:
    """
    Looks up the active insurance policy and claim history for a given user ID.
    Returns JSON with coverage tier, past claims count, policy status, and any flags.
    Use this to verify if a user is actually covered before processing their claim.
    """
    try:
        if not _supabase:
            return json.dumps({"status": "ERROR", "message": "Database client not initialized."})

        response = _supabase.table("policies").select("*").eq("id", user_id).execute()

        if response.data and len(response.data) > 0:
            return json.dumps(response.data[0], default=str)
        else:
            return json.dumps({
                "status": "NOT_FOUND",
                "tier": "Unknown",
                "past_claims": 0,
                "flags": "No policy record found for this user."
            })
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})


# ==========================================
# TOOL 2: VISION ANALYSIS (Real Gemini Multimodal API)
# ==========================================

@tool("Vision Analysis Tool")
def analyze_image(image_path: str, context: str) -> str:
    """
    Analyzes an uploaded image using the Gemini multimodal LLM.
    Performs forensic inspection for damage severity, tampering, stock photo detection,
    and consistency with the user's verbal claim description.

    Args:
        image_path: Path to the image file on the server (e.g. uploads/photo.jpg)
        context: The user's verbal description of what happened (their claim story)

    Returns:
        JSON string with damage severity, authenticity assessment, and red flags.
    """
    try:
        if not image_path:
            return json.dumps({"error": "No image path provided", "damage_severity": "UNKNOWN"})

        import google.generativeai as genai
        from PIL import Image
        import requests
        from io import BytesIO

        genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-3.1-flash-lite")

        if image_path.startswith("http://") or image_path.startswith("https://"):
            resp = requests.get(image_path, timeout=10)
            if resp.status_code != 200:
                return json.dumps({"error": f"Failed to fetch image from URL: {resp.status_code}", "damage_severity": "UNKNOWN"})
            img = Image.open(BytesIO(resp.content))
        else:
            if not os.path.exists(image_path):
                return json.dumps({"error": f"Image file not found: {image_path}", "damage_severity": "UNKNOWN"})
            img = Image.open(image_path)

        context_safe = context.replace('"""', "''").replace("\\", "")[:500]
        response = model.generate_content([
            f"""You are a senior forensic image analyst working for an insurance company's fraud prevention unit.
Analyze this image with extreme scrutiny.

The claimant's verbal description of the incident: "{context_safe}"

Provide your analysis as strict JSON (no markdown fences, just the raw JSON):
{{
    "object_detected": "What device or item is visible in the image",
    "damage_severity": "None | Minor | Moderate | Severe | Total_Loss",
    "damage_description": "Detailed description of all visible damage",
    "authenticity_score": <float 0.0 to 1.0 — 1.0 means definitely authentic>,
    "tampering_indicators": ["list of any manipulation signs, empty array if none"],
    "is_stock_photo": <boolean>,
    "matches_user_story": <boolean>,
    "story_consistency_notes": "How well does the visible damage match what the user described?",
    "estimated_damage_age": "Fresh (< 24 hours) | Recent (days) | Old (weeks+) | Cannot determine",
    "confidence": <float 0.0 to 1.0>,
    "red_flags": ["list of anything suspicious, empty array if clean"]
}}

Look specifically for:
- Lighting inconsistencies suggesting compositing or photo manipulation
- Signs this might be a stock photo (watermarks, studio lighting on "accident" photos)
- Whether the type and pattern of damage is consistent with the claimed event
- Age indicators — rust, dust, worn edges around supposedly "fresh" damage
- Any mismatch between the claimed item and what is actually shown""",
            img
        ])

        return response.text

    except Exception as e:
        return json.dumps({"error": str(e), "damage_severity": "ANALYSIS_FAILED"})


# ==========================================
# TOOL 3: CLAIM HISTORY LOOKUP (For Fraud Detective)
# ==========================================

@tool("Claim History Lookup Tool")
def get_claim_history(user_id: str) -> str:
    """
    Retrieves all past insurance claims filed by a specific user from the database.
    Use this to detect fraud patterns: frequent claims, similar damage types,
    escalating claim values, or suspicious timing between claims.

    Args:
        user_id: The user's UUID from Supabase auth

    Returns:
        JSON with total claim count and array of past claims with verdicts and dates.
    """
    try:
        if not _supabase:
            return json.dumps({"status": "ERROR", "message": "Database not available"})

        response = (_supabase.table("claims")
                    .select("*")
                    .eq("user_id", user_id)
                    .order("created_at", desc=True)
                    .execute())

        claims = response.data if response.data else []

        return json.dumps({
            "total_past_claims": len(claims),
            "claims": claims,
            "pattern_notes": (
                f"User has {len(claims)} prior claim(s) on record. Review for repeat patterns."
                if claims else
                "First-time claimant — no prior history on file."
            )
        }, default=str)

    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})


# ==========================================
# TOOL 4: SAVE CLAIM VERDICT (For Final Assessor)
# ==========================================

@tool("Save Claim Verdict Tool")
def save_claim_verdict(user_id: str, conversation_id: str, transcript_summary: str, verdict: str, reasoning: str) -> str:
    """
    Persists the final claim verdict into the database for a permanent audit trail.
    This MUST be called by the Final Assessor after reaching a decision.

    Args:
        user_id: The authenticated user's UUID
        transcript_summary: A concise summary of the claim conversation and evidence
        verdict: One of: AUTO_APPROVE, FLAGGED, DENIED
        reasoning: Detailed reasoning explaining the verdict decision

    Returns:
        JSON confirmation with the saved claim ID.
    """
    try:
        if not _supabase:
            return json.dumps({"status": "ERROR", "message": "Database not available"})

        result = _supabase.table("claims").insert({
            "user_id": user_id,
            "transcript": transcript_summary,
            "ai_summary": transcript_summary,
            "final_verdict": verdict,
            "verdict_reasoning": reasoning
        }).execute()

        # Increment the past_claims counter on the user's policy record
        try:
            current = _supabase.table("policies").select("past_claims").eq("id", user_id).execute()
            if current.data:
                new_count = current.data[0].get("past_claims", 0) + 1
                _supabase.table("policies").update({"past_claims": new_count}).eq("id", user_id).execute()
        except Exception:
            pass  # Non-critical — don't fail the verdict save for a counter update

        claim_id = result.data[0]["id"] if result.data else None
        
        # LINK THE CLAIM TO THE CONVERSATION (Bugfix)
        if claim_id:
            try:
                _supabase.table("conversations").update({
                    "claim_id": claim_id,
                    "status": "closed"
                }).eq("id", conversation_id).execute()
            except Exception as e:
                print(f"[SAVE_CLAIM] Error linking claim to conversation: {e}")

        # Log the AI verdict decision in admin_action_logs
        try:
            _supabase.table("admin_action_logs").insert({
                "user_id": user_id,
                "action_type": f"AI_Verdict:{verdict}",
                "reason": reasoning
            }).execute()
        except Exception as log_err:
            print(f"[SAVE_CLAIM] Audit Log Error: {log_err}")

        return json.dumps({"status": "SUCCESS", "claim_id": str(claim_id), "verdict": verdict})

    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})


# ==========================================
# TOOL 5: EMAIL COMMUNICATION
# Production: Wire to Resend/SendGrid API
# Current: Structured logging (ready for swap-in)
# ==========================================

@tool("Email Communication Tool")
def send_user_update(user_id: str, message: str) -> str:
    """
    Sends a secure out-of-band communication (email/SMS) to the user.
    The message must be sanitized and professional before dispatch.

    In production, this connects to Resend or SendGrid API.
    Currently logs the dispatch as a structured record.

    Args:
        user_id: The target user's UUID
        message: The sanitized message content to send
    """
    # ─── PRODUCTION SWAP-IN POINT ───
    # To make this real, uncomment and configure:
    #
    # import httpx
    # resp = httpx.post("https://api.resend.com/emails",
    #     headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
    #     json={
    #         "from": "claims@vericlaim.ai",
    #         "to": user_email,  # Fetch from Supabase auth
    #         "subject": "VeriClaim — Claim Status Update",
    #         "text": message
    #     })
    # return json.dumps({"status": "SENT", "resend_id": resp.json().get("id")})
    # ─────────────────────────────────

    print(f"[COMMS AGENT] Dispatching secure update to user {user_id}")
    print(f"[COMMS AGENT] Message: {message}")
    return json.dumps({
        "status": "DISPATCHED",
        "recipient": user_id,
        "channel": "email",
        "message_preview": message[:200] + "..." if len(message) > 200 else message
    })

# ==========================================
# TOOL 6: CLAIM STATUS CHECK
# Used by Post-Claim Support Agent
# ==========================================

@tool("Check Claim Status Tool")
def check_claim_status(user_id: str) -> str:
    """
    Checks the real-time processing status of a claim for a given user ID in the database.
    If the backend analysis has finished, it returns the final verdict and reasoning.
    If it has not finished, it returns that it is still processing.
    """
    try:
        if not _supabase:
            return json.dumps({"status": "ERROR", "message": "Database client not initialized."})

        # Fetch the most recent claim for this user
        response = _supabase.table("claims").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()

        if response.data and len(response.data) > 0:
            claim = response.data[0]
            # If the final_verdict is set, the backend crew finished
            if claim.get("final_verdict"):
                return json.dumps({
                    "status": "COMPLETED",
                    "verdict": claim["final_verdict"],
                    "reasoning": claim.get("verdict_reasoning", "No detailed reasoning provided.")
                })
            else:
                return json.dumps({
                    "status": "PROCESSING",
                    "message": "The assessment team is currently reviewing the evidence. No final verdict has been reached yet."
                })
        else:
            return json.dumps({
                "status": "NOT_FOUND",
                "message": "No claims found for this user in the database."
            })
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})
