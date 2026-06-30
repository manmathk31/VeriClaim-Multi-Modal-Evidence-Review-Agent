"""
SessionManager — Persistent conversation storage via Supabase.

Replaces the old in-memory session_store dict with real database persistence.
Conversations survive server restarts, and users can resume past sessions.

Architecture:
    - DB is the source of truth for all conversation data
    - A lightweight in-memory cache keeps the active WebSocket session fast
    - On WebSocket connect: load from DB → populate cache
    - On each message: write-through (cache + DB simultaneously)
    - On disconnect: cache evicts, DB retains everything
"""

import time
from typing import Optional
import os
import shutil


class SessionManager:
    """Manages persistent conversation sessions via Supabase."""

    def __init__(self, supabase_client):
        self._supabase = supabase_client
        # In-memory cache for active WebSocket sessions (fast reads during live chat)
        # Structure: { "user-uuid": { "conversation_id": "...", "conversation": [...], "images": [...], "claim_submitted": bool } }
        self._cache = {}

    def _safe_execute(self, query):
        """Executes a Supabase query with automatic retry if the HTTP/2 connection is dropped."""
        try:
            return query.execute()
        except Exception as e:
            err_str = str(e)
            if any(term in err_str for term in ["ConnectionTerminated", "RemoteProtocolError", "last_stream_id"]):
                print(f"[SESSION] Connection dropped by Supabase. Re-executing query: {err_str}")
                return query.execute()
            raise e

    # ══════════════════════════════════════════
    # CONVERSATION LIFECYCLE
    # ══════════════════════════════════════════

    def get_or_create_session(self, user_id: str) -> dict:
        """
        Gets an active session for the user. Checks:
        1. In-memory cache (fastest — for active WebSocket connections)
        2. Database for an existing 'active' conversation
        3. Creates a new conversation if none exists

        Returns a session dict compatible with the old session_store format.
        """
        # 1. Check cache first
        if user_id in self._cache:
            self._cache[user_id]["last_active"] = time.time()
            return self._cache[user_id]

        # 2. Check DB for an active conversation
        try:
            response = (self._supabase.table("conversations")
                        .select("*")
                        .eq("user_id", user_id)
                        .eq("status", "active")
                        .order("updated_at", desc=True)
                        .limit(1)
                        .execute())

            if response.data and len(response.data) > 0:
                conv = response.data[0]
                conversation_id = conv["id"]

                # Load messages from DB
                messages = self._load_messages(conversation_id)

                # Load evidence from DB
                images = self._load_evidence_paths(conversation_id)

                session = {
                    "conversation_id": conversation_id,
                    "conversation": messages,
                    "images": images,
                    "last_active": time.time(),
                    "claim_submitted": conv["status"] == "submitted"
                }
                self._cache[user_id] = session
                print(f"[SESSION] Restored session for user {user_id} from DB: {len(messages)} messages, {len(images)} images")
                return session

        except Exception as e:
            print(f"[SESSION] DB lookup failed for {user_id}, creating new: {e}")

        # 3. Create a new conversation
        return self._create_new_session(user_id)

    def _create_new_session(self, user_id: str) -> dict:
        """Creates a brand new conversation in DB and caches it."""
        conversation_id = None
        try:
            result = (self._supabase.table("conversations")
                      .insert({
                          "user_id": user_id,
                          "status": "active"
                      })
                      .execute())

            if result.data and len(result.data) > 0:
                conversation_id = result.data[0]["id"]
                print(f"[SESSION] Created new conversation {conversation_id} for user {user_id}")
        except Exception as e:
            print(f"[SESSION] Failed to create conversation in DB: {e}")

        session = {
            "conversation_id": conversation_id,
            "conversation": [],
            "images": [],
            "last_active": time.time(),
            "claim_submitted": False
        }
        self._cache[user_id] = session
        return session

    # ══════════════════════════════════════════
    # MESSAGE OPERATIONS
    # ══════════════════════════════════════════

    def append_message(self, user_id: str, role: str, content: str):
        """
        Appends a message to both the in-memory cache and the database.
        Write-through strategy: cache is updated immediately, DB write is best-effort.
        """
        session = self._cache.get(user_id)
        if not session:
            return

        # Update cache
        session["conversation"].append({"role": role, "content": content})
        session["last_active"] = time.time()

        # Write to DB
        conversation_id = session.get("conversation_id")
        if conversation_id:
            try:
                self._supabase.table("messages").insert({
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content
                }).execute()
            except Exception as e:
                print(f"[SESSION] Failed to persist message to DB: {e}")

        # Auto-generate conversation title from first user message
        if role == "user" and len(session["conversation"]) == 1:
            self._set_conversation_title(conversation_id, content)

    def _set_conversation_title(self, conversation_id: str, first_message: str):
        """Sets the conversation title based on the first user message."""
        if not conversation_id:
            return
        # Truncate to a reasonable title length
        title = first_message[:100].strip()
        if len(first_message) > 100:
            title += "..."
        try:
            self._supabase.table("conversations").update({
                "title": title
            }).eq("id", conversation_id).execute()
        except Exception as e:
            print(f"[SESSION] Failed to set conversation title: {e}")

    def _load_messages(self, conversation_id: str) -> list:
        """Loads all messages for a conversation from the database."""
        try:
            response = (self._supabase.table("messages")
                        .select("role, content")
                        .eq("conversation_id", conversation_id)
                        .order("created_at", desc=False)
                        .execute())
            return [{"role": m["role"], "content": m["content"]} for m in (response.data or [])]
        except Exception as e:
            print(f"[SESSION] Failed to load messages: {e}")
            return []

    # ══════════════════════════════════════════
    # EVIDENCE / IMAGE OPERATIONS
    # ══════════════════════════════════════════

    def add_evidence(self, user_id: str, file_name: str, file_path: str = None, storage_path: str = None, public_url: str = None):
        """Records an uploaded evidence file in both cache and DB."""
        session = self._cache.get(user_id)
        if session:
            session["images"].append(public_url or file_path)

        conversation_id = session.get("conversation_id") if session else None
        if conversation_id:
            try:
                self._supabase.table("evidence").insert({
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "file_name": file_name,
                    "file_path": public_url or file_path,
                    "storage_path": storage_path
                }).execute()
            except Exception as e:
                print(f"[SESSION] Failed to persist evidence to DB: {e}")

    def _load_evidence_paths(self, conversation_id: str) -> list:
        """Loads all evidence file paths for a conversation."""
        try:
            response = (self._supabase.table("evidence")
                        .select("file_path")
                        .eq("conversation_id", conversation_id)
                        .order("uploaded_at", desc=False)
                        .execute())
            return [e["file_path"] for e in (response.data or []) if e.get("file_path")]
        except Exception as e:
            print(f"[SESSION] Failed to load evidence: {e}")
            return []

    # ══════════════════════════════════════════
    # CLAIM LIFECYCLE
    # ══════════════════════════════════════════

    def mark_claim_submitted(self, user_id: str, claim_id: Optional[str] = None):
        """Marks the active conversation as submitted and links the claim."""
        session = self._cache.get(user_id)
        if session:
            session["claim_submitted"] = True

        conversation_id = session.get("conversation_id") if session else None
        if conversation_id:
            try:
                update_data = {"status": "submitted"}
                if claim_id:
                    update_data["claim_id"] = claim_id
                self._supabase.table("conversations").update(
                    update_data
                ).eq("id", conversation_id).execute()
                print(f"[SESSION] Conversation {conversation_id} marked as submitted")
            except Exception as e:
                print(f"[SESSION] Failed to mark conversation as submitted: {e}")

    # ══════════════════════════════════════════
    # DASHBOARD QUERIES (for Phase 4)
    # ══════════════════════════════════════════

    def get_user_conversations(self, user_id: str) -> list:
        """Returns all conversations for a user (for the dashboard)."""
        try:
            response = (self._supabase.table("conversations")
                        .select("*, claims(final_verdict, verdict_reasoning, created_at)")
                        .eq("user_id", user_id)
                        .order("updated_at", desc=True)
                        .execute())
            return response.data or []
        except Exception as e:
            print(f"[SESSION] Failed to fetch user conversations: {e}")
            return []

    def get_conversation_with_messages(self, conversation_id: str, user_id: str) -> Optional[dict]:
        """Returns a single conversation with all its messages and evidence."""
        try:
            # Verify ownership
            conv_response = (self._supabase.table("conversations")
                             .select("*, claims(final_verdict, verdict_reasoning)")
                             .eq("id", conversation_id)
                             .eq("user_id", user_id)
                             .limit(1)
                             .execute())

            if not conv_response.data:
                return None

            conversation = conv_response.data[0]

            # Load messages
            conversation["messages"] = self._load_messages(conversation_id)

            # Load evidence
            evidence_response = (self._supabase.table("evidence")
                                 .select("file_name, file_path, uploaded_at")
                                 .eq("conversation_id", conversation_id)
                                 .order("uploaded_at", desc=False)
                                 .execute())
            conversation["evidence"] = evidence_response.data or []

            return conversation
        except Exception as e:
            print(f"[SESSION] Failed to fetch conversation detail: {e}")
            return None

    def get_conversation_admin(self, conversation_id: str) -> Optional[dict]:
        """Admin version to fetch a conversation bypassing user ownership check."""
        try:
            conv_response = (self._supabase.table("conversations")
                             .select("*, claims(final_verdict, verdict_reasoning)")
                             .eq("id", conversation_id)
                             .limit(1)
                             .execute())
            if not conv_response.data:
                return None
            conversation = conv_response.data[0]
            conversation["messages"] = self._load_messages(conversation_id)
            evidence_response = (self._supabase.table("evidence")
                                 .select("file_name, file_path, uploaded_at")
                                 .eq("conversation_id", conversation_id)
                                 .order("uploaded_at", desc=False)
                                 .execute())
            conversation["evidence"] = evidence_response.data or []
            return conversation
        except Exception as e:
            print(f"[SESSION] Admin failed to fetch conversation: {e}")
            return None

    # ══════════════════════════════════════════
    # ADMIN QUERIES (Phase 5)
    # ══════════════════════════════════════════

    def get_admin_stats(self) -> dict:
        try:
            total_res = self._supabase.table("claims").select(
                "*", count="exact"
            ).execute()
            total = total_res.count or 0

            approved_res = self._supabase.table("claims").select(
                "*", count="exact"
            ).eq("final_verdict", "AUTO_APPROVE").execute()
            approved = approved_res.count or 0

            denied_res = self._supabase.table("claims").select(
                "*", count="exact"
            ).eq("final_verdict", "DENIED").execute()
            denied = denied_res.count or 0

            flagged_res = self._supabase.table("claims").select(
                "*", count="exact"
            ).eq("final_verdict", "FLAGGED").execute()
            flagged = flagged_res.count or 0

            return {
                "total": total,
                "approved": approved,
                "denied": denied,
                "flagged": flagged,
                "pending": total - approved - denied - flagged
            }
        except Exception as e:
            print(f"[SESSION] Admin Stats Error: {e}")
            return {}

    def get_all_claims_admin(self) -> list:
        try:
            res = (self._supabase.table("claims")
                   .select("*, conversations(id, title, user_id)")
                   .order("created_at", desc=True)
                   .execute())
            
            claims_data = []
            for c in (res.data or []):
                conv_list = c.get("conversations") or []
                conv = conv_list[0] if isinstance(conv_list, list) and len(conv_list) > 0 else (conv_list if isinstance(conv_list, dict) else {})
                
                raw_status = c.get("final_verdict", "")
                display_status = "pending"
                if raw_status == "AUTO_APPROVE":
                    display_status = "approved"
                elif raw_status == "DENIED":
                    display_status = "denied"
                elif raw_status == "FLAGGED":
                    display_status = "flagged"

                claims_data.append({
                    "id": c.get("id"),
                    "conversation_id": conv.get("id"),
                    "title": conv.get("title", "Untitled Claim"),
                    "user_id": conv.get("user_id", "Unknown"),
                    "status": display_status,
                    "created_at": c.get("created_at")
                })
            return claims_data
        except Exception as e:
            print(f"[SESSION] Admin Claims Error: {e}")
            return []

    def get_all_users_admin(self) -> list:
        try:
            # Infer active users from conversations to get counts
            res = self._supabase.table("conversations").select("user_id").execute()
            users_map = {}
            for row in (res.data or []):
                uid = row["user_id"]
                users_map[uid] = users_map.get(uid, 0) + 1
            
            # Fetch real users from Supabase Auth
            auth_res = self._supabase.auth.admin.list_users()
            users_list = []
            
            for u in auth_res:
                user_id = u.id
                email = u.email
                name = "Unknown"
                if hasattr(u, 'user_metadata') and u.user_metadata:
                    name = u.user_metadata.get("full_name", "Unknown")
                
                # Check ban status (banned_until usually exists if banned)
                banned_until = getattr(u, 'banned_until', None)
                is_banned = False
                if banned_until and str(banned_until).strip() != "" and str(banned_until).lower() != "none":
                    try:
                        from datetime import datetime, timezone
                        bu_str = str(banned_until).replace("Z", "+00:00")
                        bu_date = datetime.fromisoformat(bu_str)
                        if bu_date > datetime.now(timezone.utc):
                            is_banned = True
                    except:
                        is_banned = True
                
                status = "banned" if is_banned else "active"
                
                users_list.append({
                    "user_id": user_id,
                    "email": email,
                    "name": name,
                    "status": status,
                    "total_conversations": users_map.get(user_id, 0)
                })
            
            return users_list
        except Exception as e:
            print(f"[SESSION] Admin Users Error: {e}")
            return []

    def ban_user(self, user_id: str, reason: str, hard_ban: bool = False):
        try:
            # 1. Ban the user in Auth
            self._supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "876000h"})
            
            # 2. If hard ban, destroy data
            if hard_ban:
                conv_res = self._supabase.table("conversations").select("id").eq("user_id", user_id).execute()
                for c in (conv_res.data or []):
                    conv_id = c["id"]
                    ev_res = self._supabase.table("evidence").select("storage_path").eq("conversation_id", conv_id).execute()
                    files_to_delete = [ev["storage_path"] for ev in (ev_res.data or []) if ev.get("storage_path")]
                    if files_to_delete:
                        self._supabase.storage.from_("evidence").remove(files_to_delete)
                self._supabase.table("conversations").delete().eq("user_id", user_id).execute()
                self._supabase.table("claims").delete().eq("user_id", user_id).execute()
                self._supabase.table("policies").update({"past_claims": 0}).eq("id", user_id).execute()
            
            # 3. Log the action
            action_type = "HardBan" if hard_ban else "Suspend"
            try:
                self._supabase.table("admin_action_logs").insert({
                    "user_id": user_id,
                    "action_type": action_type,
                    "reason": reason
                }).execute()
            except:
                pass
            return {"status": "success"}
        except Exception as e:
            print(f"[SESSION] Ban User Error: {e}")
            raise Exception(str(e))

    def unban_user(self, user_id: str):
        try:
            try:
                self._supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "none"})
            except Exception as inner_e:
                print(f"[SESSION] 'none' failed, falling back to '0h' for {user_id}: {inner_e}")
                self._supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "0h"})
            
            try:
                self._supabase.table("admin_action_logs").insert({
                    "user_id": user_id,
                    "action_type": "Unban",
                    "reason": "Admin lifted suspension."
                }).execute()
            except:
                pass
            return {"status": "success"}
        except Exception as e:
            raise Exception(str(e))

    def delete_user(self, user_id: str):
        try:
            # 1. Destroy data explicitly to avoid Foreign Key constraint blocks
            conv_res = self._safe_execute(self._supabase.table("conversations").select("id").eq("user_id", user_id))
            for c in (conv_res.data or []):
                conv_id = c["id"]
                # A. Delete storage files
                ev_res = self._safe_execute(self._supabase.table("evidence").select("storage_path").eq("conversation_id", conv_id))
                files_to_delete = [ev["storage_path"] for ev in (ev_res.data or []) if ev.get("storage_path")]
                if files_to_delete:
                    self._supabase.storage.from_("evidence").remove(files_to_delete)
                # B. Explicitly delete evidence DB rows
                self._safe_execute(self._supabase.table("evidence").delete().eq("conversation_id", conv_id))
                # C. Explicitly delete messages DB rows
                self._safe_execute(self._supabase.table("messages").delete().eq("conversation_id", conv_id))
                
            self._safe_execute(self._supabase.table("conversations").delete().eq("user_id", user_id))
            self._safe_execute(self._supabase.table("claims").delete().eq("user_id", user_id))
            self._safe_execute(self._supabase.table("policies").delete().eq("id", user_id))
            self._safe_execute(self._supabase.table("notifications").delete().eq("user_id", user_id))
            self._safe_execute(self._supabase.table("admin_action_logs").delete().eq("user_id", user_id))
            
            # Evict from in-memory cache immediately
            self.evict_cache(user_id)
            
            # Delete local agent memory folder for this user if it exists
            user_memory_dir = f"./crewai_memory/{user_id}"
            if os.path.exists(user_memory_dir):
                try:
                    shutil.rmtree(user_memory_dir)
                except Exception as mem_err:
                    print(f"Warning: could not delete memory dir for {user_id}: {mem_err}")
            
            # 2. Delete Auth user
            self._supabase.auth.admin.delete_user(user_id)
            
            try:
                self._safe_execute(self._supabase.table("admin_action_logs").insert({
                    "user_id": user_id,
                    "action_type": "Delete",
                    "reason": "Account completely purged."
                }))
            except:
                pass
            return {"status": "success"}
        except Exception as e:
            raise Exception(str(e))

    def clear_user_data(self, user_id: str):
        try:
            # Destroy data without deleting auth account explicitly to avoid FK errors
            conv_res = self._safe_execute(self._supabase.table("conversations").select("id").eq("user_id", user_id))
            for c in (conv_res.data or []):
                conv_id = c["id"]
                # A. Delete storage files
                ev_res = self._safe_execute(self._supabase.table("evidence").select("storage_path").eq("conversation_id", conv_id))
                files_to_delete = [ev["storage_path"] for ev in (ev_res.data or []) if ev.get("storage_path")]
                if files_to_delete:
                    self._supabase.storage.from_("evidence").remove(files_to_delete)
                # B. Explicitly delete evidence DB rows
                self._safe_execute(self._supabase.table("evidence").delete().eq("conversation_id", conv_id))
                # C. Explicitly delete messages DB rows
                self._safe_execute(self._supabase.table("messages").delete().eq("conversation_id", conv_id))
                
            self._safe_execute(self._supabase.table("conversations").delete().eq("user_id", user_id))
            self._safe_execute(self._supabase.table("claims").delete().eq("user_id", user_id))
            self._safe_execute(self._supabase.table("policies").update({"past_claims": 0}).eq("id", user_id))
            self._safe_execute(self._supabase.table("notifications").delete().eq("user_id", user_id))
            
            # Evict from in-memory cache to force a fresh DB pull next time
            self.evict_cache(user_id)
            
            # Delete local agent memory folder for this user if it exists
            user_memory_dir = f"./crewai_memory/{user_id}"
            if os.path.exists(user_memory_dir):
                try:
                    shutil.rmtree(user_memory_dir)
                except Exception as mem_err:
                    print(f"Warning: could not delete memory dir for {user_id}: {mem_err}")
            
            try:
                self._safe_execute(self._supabase.table("admin_action_logs").insert({
                    "user_id": user_id,
                    "action_type": "Clear Data",
                    "reason": "Admin wiped all user claim data for testing/reset."
                }))
            except:
                pass
            return {"status": "success"}
        except Exception as e:
            print(f"[SESSION] Clear Data Error: {e}")
            raise Exception(str(e))

    def update_claim_status_admin(self, claim_id: str, verdict: str, reasoning: str = "Admin Override"):
        db_verdict = {"approved": "AUTO_APPROVE", "denied": "DENIED", "flagged": "FLAGGED"}.get(verdict.lower(), verdict.upper())
        try:
            self._supabase.table("claims").update({
                "final_verdict": db_verdict,
                "verdict_reasoning": reasoning
            }).eq("id", claim_id).execute()

            # Fetch user_id of the claim for audit logging
            try:
                claim_res = self._supabase.table("claims").select("user_id").eq("id", claim_id).execute()
                target_user_id = claim_res.data[0]["user_id"] if claim_res.data else None
                if target_user_id:
                    self._supabase.table("admin_action_logs").insert({
                        "user_id": target_user_id,
                        "action_type": f"VerdictOverride:{db_verdict}",
                        "reason": reasoning
                    }).execute()
            except Exception as log_err:
                print(f"[SESSION] Admin Claim Update Audit Log Error: {log_err}")

            return True
        except Exception as e:
            print(f"[SESSION] Admin Claim Update Error: {e}")
            return False

    def cleanup_old_logs(self, days: int = 30):
        """Deletes admin action logs older than N days. Call periodically."""
        try:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            self._supabase.table("admin_action_logs").delete().lt(
                "created_at", cutoff
            ).execute()
            print(f"[SESSION] Cleaned up admin_action_logs older than {days} days.")
        except Exception as e:
            print(f"[SESSION] Log cleanup error: {e}")
            
    def clear_all_logs(self):
        """Manually wipe all admin action logs."""
        try:
            self._supabase.table("admin_action_logs").delete().neq("id", 0).execute()
            print("[SESSION] Manually cleared all admin action logs.")
            return True
        except Exception as e:
            print(f"[SESSION] Clear logs error: {e}")
            return False

    # ══════════════════════════════════════════
    # CACHE MANAGEMENT
    # ══════════════════════════════════════════

    def evict_cache(self, user_id: str):
        """Removes a user from the in-memory cache (called on WebSocket disconnect)."""
        if user_id in self._cache:
            msg_count = len(self._cache[user_id].get("conversation", []))
            del self._cache[user_id]
            print(f"[SESSION] Cache evicted for user {user_id} ({msg_count} messages persisted in DB)")

    def start_new_conversation(self, user_id: str) -> dict:
        """Forces creation of a new conversation (e.g., user clicks 'New Claim')."""
        self.evict_cache(user_id)
        return self._create_new_session(user_id)
