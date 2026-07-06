import os
import time
import random
from crewai import Agent, Task, Crew, Process
from crewai.memory.unified_memory import Memory
from .tools import (
    lookup_policy, analyze_image, send_user_update,
    get_claim_history, save_claim_verdict, check_claim_status
)

def retry_with_backoff(fn, *args, max_retries=5, initial_delay=2.0, backoff_factor=2.0, **kwargs):
    """
    Retries a function call if it fails due to a 429 Rate Limit/Resource Exhausted error.
    Uses exponential backoff with jitter.
    """
    delay = initial_delay
    for i in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str or "rate limit" in err_str:
                if i == max_retries - 1:
                    print(f"[RETRY] Max retries reached ({max_retries}). Failing with exception: {e}")
                    raise e
                sleep_time = delay + random.uniform(0.1, 1.0)
                print(f"[RETRY] 429 Rate limited/Quota exceeded. Retrying in {sleep_time:.2f}s (Attempt {i+1}/{max_retries})...")
                time.sleep(sleep_time)
                delay *= backoff_factor
            else:
                raise e

# ==========================================
# VERICLAIM CREW AI ORCHESTRATOR
# True Agentic Platform:
#   ✓ Real tools (Vision API, DB queries, verdicts)
#   ✓ CrewAI Memory enabled (short-term + long-term + entity)
#   ✓ Google text-embedding-004 embedder (no OpenAI dependency)
#   ✓ Gemini LLM for memory analysis (no OpenAI fallback)
#   ✓ Hierarchical process (manager LLM plans dynamically)
#   ✓ Full conversation context (not just latest message)
# ==========================================

class VeriClaimCrew:
    def __init__(self, telemetry_callback=None):
        self.telemetry_callback = telemetry_callback
        # Primary LLM for all agents — Gemini Flash for speed
        self.llm = "gemini/gemini-3.1-flash-lite"
        # Manager LLM for hierarchical process — same model, manages routing
        self.manager_llm = "gemini/gemini-3.1-flash-lite"

        # ── Embedder Config for CrewAI Memory ──
        # CrewAI defaults to OpenAI's embedder which fails without an OpenAI key.
        # We override it with Google's embedder using our existing Gemini key.
        # Note: CrewAI's pydantic validator strictly requires specific model names.
        self.embedder_config = {
            "provider": "google-generativeai",
            "config": {
                "api_key": os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
                "model": "gemini-embedding-001"
            }
        }

    def create_memory(self, user_id: str):
        """
        Creates a fully configured Memory instance using Gemini for BOTH:
          1. Embeddings (vector storage) — gemini-embedding-001
          2. Memory analysis LLM (extraction, consolidation) — Gemini Flash

        Without this, CrewAI's Memory defaults its internal LLM to 'gpt-4o-mini',
        which crashes with 'OPENAI_API_KEY is required'.
        """
        # Route memory to a unique local folder per user to ensure complete isolation
        # and allow clean deletion when an account is deleted.
        storage_dir = f"./crewai_memory/{user_id}"
        os.makedirs(storage_dir, exist_ok=True)
        os.environ["CREWAI_STORAGE_DIR"] = storage_dir
            
        return Memory(
            llm=self.llm,
            embedder=self.embedder_config,
        )

    def get_agent_callback(self, agent_name: str):
        def callback(step_output):
            if self.telemetry_callback:
                try:
                    # step_output is an AgentStep, convert its thought process to string
                    text = getattr(step_output, 'thought', str(step_output))
                    if hasattr(step_output, 'tool') and step_output.tool:
                        text += f"\n[Using tool: {step_output.tool}]"
                    self.telemetry_callback(agent_name, text)
                except Exception as e:
                    pass
        return callback

    def create_agents(self, user_id: str):
        """
        Creates the 6-agent crew with real tools and user-specific context.
        Each agent has a distinct role, tools, and behavioral backstory.
        """

        orchestrator = Agent(
            role="Voice Orchestrator",
            step_callback=self.get_agent_callback("Orchestrator"),
            goal=(
                "Manage the conversation flow. Analyze the FULL conversation history "
                "to determine if the user has provided enough detail about their claim. "
                "Extract the core claim story when complete."
            ),
            backstory=(
                "You are the friendly, professional frontline AI for VeriClaim Insurance. "
                "You guide users through the claims process with natural warmth, intelligence, and professionalism. "
                "You NEVER promise a payout or specific amount. "
                "To remain human-like and smart, you avoid repeating robotic expressions of sympathy, over-apologizing, or writing long-winded paragraphs. "
                "Instead, you speak in a naturally concise, direct, and conversational tone while maintaining high analytical intelligence. "
                "You remember everything the user has said across the entire conversation. "
                "When the story is complete (what device, what happened, when), you summarize "
                "it clearly for the backend agents."
            ),
            verbose=False,
            allow_delegation=False,
            llm=self.llm
        )

        policy_agent = Agent(
            role="Policy Triage Agent",
            step_callback=self.get_agent_callback("Policy Triage Agent"),
            goal=(
                f"Verify insurance coverage for user {user_id}. "
                "Check their tier, active status, and past claims history to assess risk level."
            ),
            backstory=(
                "You are a strict compliance officer in the insurance industry. "
                "You use the Policy Lookup Tool to pull the user's actual records from the "
                "secure database. You check: Is their policy active? What tier are they? "
                "How many past claims do they have? "
                "A user with 5+ past claims in a year is HIGH RISK — flag them."
            ),
            tools=[lookup_policy],
            verbose=False,
            allow_delegation=False,
            llm=self.llm
        )

        vision_analyst = Agent(
            role="Multimodal Vision Analyst",
            step_callback=self.get_agent_callback("Multimodal Vision Analyst"),
            goal=(
                "Perform forensic image analysis on uploaded evidence photos to detect "
                "damage severity, tampering, and consistency with the user's story."
            ),
            backstory=(
                "You are a forensic image expert with 15 years of experience detecting "
                "insurance fraud through visual evidence. You trust NO ONE. Every image is "
                "suspect until proven authentic. "
                "You use the Vision Analysis Tool to run images through a multimodal AI for "
                "pixel-level analysis. You look for: stock photos, metadata stripping, "
                "Photoshop artifacts, lighting inconsistencies, and mismatches between the "
                "claimed damage and what the image actually shows. "
                "If the user says 'cracked screen' but the image shows a pristine device, "
                "you flag it immediately."
            ),
            tools=[analyze_image],
            verbose=False,
            allow_delegation=False,
            llm=self.llm
        )

        fraud_detective = Agent(
            role="Fraud Detective",
            step_callback=self.get_agent_callback("Fraud Detective"),
            goal=(
                f"Cross-reference the user's verbal claim against visual evidence and "
                f"historical claim data to detect fraud patterns for user {user_id}."
            ),
            backstory=(
                "You are a seasoned insurance fraud investigator with a track record of "
                "catching sophisticated fraud rings. "
                "You use the Claim History Lookup Tool to pull the user's FULL claim history "
                "from the database. You look for:\n"
                "- Temporal lies: 'It broke today' but the image shows old damage\n"
                "- Pattern fraud: Filing similar claims every few months\n"
                "- Story inconsistencies: Details that change or contradict visual evidence\n"
                "- Value inflation: Claiming expensive items when damage is minor\n"
                "If you find ANYTHING suspicious, you flag it with HIGH confidence."
            ),
            tools=[get_claim_history],
            verbose=False,
            allow_delegation=False,
            llm=self.llm
        )

        assessor = Agent(
            role="Final Assessor",
            step_callback=self.get_agent_callback("Final Assessor"),
            goal=(
                f"Synthesize all reports (policy, vision, fraud) into a final verdict "
                f"and SAVE it to the database using the Save Claim Verdict Tool for user {user_id}."
            ),
            backstory=(
                "You are the ultimate decision-maker for insurance claims. You weigh:\n"
                "1. Policy Agent's coverage report\n"
                "2. Vision Analyst's damage assessment (if images were provided)\n"
                "3. Fraud Detective's risk assessment\n\n"
                "You output a verdict: AUTO_APPROVE (clean claim), FLAGGED (needs human review), "
                "or DENIED (clear fraud evidence).\n"
                "You MUST use the Save Claim Verdict Tool to persist your decision to the database. "
                "Your reasoning must be thorough enough to survive legal scrutiny."
            ),
            tools=[save_claim_verdict],
            verbose=False,
            allow_delegation=False,
            llm=self.llm
        )

        communication_agent = Agent(
            role="Communication Messenger",
            step_callback=self.get_agent_callback("Communication Messenger"),
            goal=(
                "Draft and dispatch a professional, empathetic status update to the user "
                "based on the Final Assessor's verdict."
            ),
            backstory=(
                "You are the secure communication specialist for VeriClaim. "
                "Based on the Final Assessor's verdict, you draft a clear, professional message:\n"
                "- AUTO_APPROVE: Congratulate them, explain next steps for payout processing\n"
                "- FLAGGED: Explain that additional review is needed (do NOT accuse them of fraud)\n"
                "- DENIED: Be professional but firm, explain they can file an appeal\n\n"
                "You NEVER reveal internal fraud scores, agent names, or reasoning to the user. "
                "You use the Email Communication Tool to dispatch the message."
            ),
            tools=[send_user_update],
            verbose=False,
            allow_delegation=False,
            llm=self.llm
        )

        return [orchestrator, policy_agent, vision_analyst, fraud_detective, assessor, communication_agent]

    def _summarize_prefix(self, conversation_prefix: list) -> str:
        """Uses Gemini Flash to instantly summarize the beginning of the conversation to save context tokens."""
        import google.generativeai as genai
        try:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-3.1-flash-lite")
            
            text = "\n".join([f"{'USER' if t['role'] == 'user' else 'AI AGENT'}: {t['content']}" for t in conversation_prefix])
            
            def make_call():
                return model.generate_content(
                    f"Please write a highly detailed, dense summary of this chat history. "
                    f"Extract every mentioned fact (names, objects, timestamps, damage descriptions, feelings). "
                    f"Keep it concise. \n\n{text}"
                )
                
            resp = retry_with_backoff(make_call)
            return resp.text.strip()
        except Exception as e:
            print(f"[CACHE] Fallback: Failed to summarize prefix: {e}")
            return "No summary available."

    def process_live_claim(self, user_id: str, conversation_history: list, image_paths: list):
        """
        Executes the full crew pipeline on a live claim.

        This is the main entry point called from main.py via run_in_executor.

        Args:
            user_id: Authenticated user UUID from Supabase
            conversation_history: List of dicts [{"role": "user"|"ai", "content": "..."}]
            image_paths: List of absolute file paths to uploaded evidence images

        Returns:
            String response to send back to the user via WebSocket
        """

        def _safe(text: str) -> str:
            return text.replace("{", "(").replace("}", ")").replace("Ignore", "").replace("ignore", "")[:1000]

        if len(conversation_history) > 6:
            prefix = conversation_history[:-6]
            recent = conversation_history[-6:]
            summary = self._summarize_prefix(prefix)
            
            conversation_text = f"[PREVIOUS CONTEXT SUMMARY (RELIABLE)]:\n{summary}\n\n--- RECENT 6 MESSAGES ---\n"
            conversation_text += "\n".join([
                f"{'USER' if turn['role'] == 'user' else 'AI AGENT'}: {_safe(turn['content'])}"
                for turn in recent
            ])
        else:
            conversation_text = "\n".join([
                f"{'USER' if turn['role'] == 'user' else 'AI AGENT'}: {_safe(turn['content'])}"
                for turn in conversation_history
            ])

        agents = self.create_agents(user_id)
        orchestrator = agents[0]

        # ═══════════════════════════════════════════════════════
        # PHASE 1: CONVERSATION ROUTING (Single Agent)
        #
        # The Orchestrator reads the FULL conversation history
        # and decides: does the user's claim story have enough
        # detail to proceed, or do we need more info?
        # ═══════════════════════════════════════════════════════

        has_images = bool(image_paths)

        t1 = Task(
            description=(
                f"Analyze this conversation with the user (older messages may be summarized for context):\n\n"
                f"--- CONVERSATION HISTORY ---\n"
                f"{conversation_text}\n"
                f"--- END ---\n\n"
                f"Your job:\n"
                f"1. Read the ENTIRE provided text (including the summary if present), not just the last message.\n"
                f"2. Be naturally warm and professional. Avoid repeating robotic expressions of sympathy or over-apologizing on every turn. Only express concern or sympathy when the user first describes the incident, then focus on practical help.\n"
                f"3. Speak in a naturally concise, conversational, and direct manner, like a real human customer service representative. Do not add unnecessary padding, pleasantries, or long paragraphs, but do not sacrifice intelligence or logic.\n"
                f"4. Ask clarifying follow-up questions to gather more context. Do not rush to finish. Ensure you know the device, exactly what happened, when, and the severity.\n"
                f"5. Evidence Check: Has the user uploaded evidence photos? Currently, uploaded_photos={has_images}. "
                f"If they haven't uploaded photos, you must eventually get them, BUT do not demand them repeatedly.\n"
                f"6. SOCIAL INTELLIGENCE: Be highly socially aware. Mimic a real human conversation. "
                f"If the user says 'give me a minute' or asks a quick side question, just acknowledge it naturally without appending a demand for more details or photos. Do NOT relentlessly ask for missing information on every single turn.\n"
                f"7. If the story is NOT complete OR they haven't uploaded photos yet, "
                f"output EXACTLY starting with 'INCOMPLETE:' followed by your natural, conversational reply.\n"
                f"8. ONLY if the story is fully complete AND they have provided photos (if applicable), "
                f"output EXACTLY starting with 'COMPLETE:' followed by a structured summary:\n"
                f"   • Device/Item: ...\n"
                f"   • What happened: ...\n"
                f"   • When it happened: ...\n"
                f"   • Additional details: ...\n\n"
                f"Important: You remember everything from the conversation. Never repeat yourself."
            ),
            expected_output=(
                "String starting with 'INCOMPLETE: [Empathetic question]' "
                "or 'COMPLETE: [Structured summary]'"
            ),
            agent=orchestrator
        )

        phase1_crew = Crew(
            agents=[orchestrator],
            tasks=[t1],
            verbose=False,
            # memory=self.create_memory(user_id), # Temporarily disabled to avoid heavy 429 quota limits on Memory Save
            memory=False,
            max_rpm=10
        )
        t1_result = str(phase1_crew.kickoff()).strip()

        # Short-circuit: If the story is incomplete, keep conversing
        if t1_result.upper().startswith("INCOMPLETE"):
            reply = t1_result.split(":", 1)[1].strip() if ":" in t1_result else t1_result
            return False, reply

        # Extract the complete claim summary
        summary = t1_result.split(":", 1)[1].strip() if ":" in t1_result else t1_result
        reply = "Thank you so much for explaining what happened. I have gathered all the necessary details and submitted your claim for review. Our assessment team will review the details and evidence, and you will receive an email update shortly. Is there anything else I can help you with today?"
        return True, (summary, reply)

    def process_post_claim(self, user_id: str, conversation_history: list):
        """
        Handles follow-up questions from the user after the claim has been submitted.
        Queries the database dynamically instead of giving hardcoded responses.
        """
        # Format the conversation text to give context
        if len(conversation_history) > 6:
            prefix = conversation_history[:-6]
            recent = conversation_history[-6:]
            summary = self._summarize_prefix(prefix)
            
            conversation_text = f"[PREVIOUS CONTEXT SUMMARY]:\n{summary}\n\n--- RECENT MESSAGES ---\n"
            for msg in recent:
                role = "AI Agent" if msg["role"] == "ai" else "Customer"
                conversation_text += f"{role}: {msg['content']}\n"
        else:
            conversation_text = ""
            for msg in conversation_history:
                role = "AI Agent" if msg["role"] == "ai" else "Customer"
                conversation_text += f"{role}: {msg['content']}\n"

        support_agent = Agent(
            role="Post-Claim Support Specialist",
            goal="Answer the customer's follow up questions regarding their claim status accurately and politely.",
            backstory=(
                "You are the VeriClaim Post-Claim Support Specialist. "
                "You answer customer questions with natural warmth, intelligence, and professional conciseness. "
                "You do not write long-winded paragraphs, repeat expressions of sympathy, or over-apologize. "
                "The customer has already successfully submitted their claim earlier in this conversation, "
                "and it is currently being processed (or has been processed) by the backend assessment team. "
                "You have access to the Check Claim Status Tool to look up real-time database results."
            ),
            llm=self.llm,
            tools=[check_claim_status],
            verbose=False,
            allow_delegation=False
        )

        t1 = Task(
            description=(
                f"Analyze the conversation:\n\n"
                f"--- CONVERSATION HISTORY ---\n"
                f"{conversation_text}\n"
                f"--- END ---\n\n"
                f"The user has already submitted their claim. Look at their most recent question/statement.\n"
                f"1. You MUST use the `check_claim_status` tool with user_id={user_id} to get the latest verdict and reasoning from the database.\n"
                f"2. Formulate a friendly, natural-sounding reply answering their question.\n"
                f"3. Do NOT repeat the hardcoded message 'I have submitted your claim for review...'\n"
                f"4. Speak in a naturally concise, conversational, and direct manner, like a real human representative on the phone. Answer the question directly with clarity, avoiding unnecessary pleasantries, robotic apologies, or fluff."
            ),
            expected_output="A polite, conversational reply explaining the status/verdict to the user.",
            agent=support_agent
        )

        support_crew = Crew(
            agents=[support_agent],
            tasks=[t1],
            verbose=False,
            # memory=self.create_memory(user_id), # Temporarily disabled to avoid heavy 429 quota limits on Memory Save
            memory=False,
            max_rpm=10
        )
        
        reply = str(support_crew.kickoff()).strip()
        return reply

    def run_backend_processing(self, user_id: str, conversation_id: str, summary: str, image_paths: list):
        """
        Runs the multi-agent backend processing asynchronously.
        """
        agents = self.create_agents(user_id)

        # ═══════════════════════════════════════════════════════
        # PHASE 2: BACKEND PROCESSING (Multi-Agent, Hierarchical)
        #
        # The manager LLM dynamically decides which agents to
        # invoke and in what order based on what it discovers.
        # Tasks are defined, but the manager plans execution.
        # ═══════════════════════════════════════════════════════

        valid_images = [p for p in (image_paths or []) if p.startswith("http") or os.path.exists(p)]
        has_images = bool(valid_images)
        image_paths_str = ", ".join(valid_images) if valid_images else "No images uploaded"

        backend_tasks = []
        backend_agents = []

        # ── Task: Policy Verification (always runs) ──
        t2 = Task(
            description=(
                f"Look up the insurance policy for user ID: {user_id}\n"
                f"Use the Policy Lookup Tool with this exact user_id.\n"
                f"Report their coverage tier, active status, number of past claims, "
                f"and any existing flags or warnings."
            ),
            expected_output="A policy status summary with tier, past claims count, and risk indicators.",
            agent=agents[1]
        )
        backend_tasks.append(t2)
        backend_agents.append(agents[1])

        # ── Task: Vision Analysis (only if images exist) ──
        if has_images:
            t3 = Task(
                description=(
                    f"Analyze the uploaded evidence image(s) at these paths: {image_paths_str}\n"
                    f"The user's claim story: {summary}\n\n"
                    f"Use the Vision Analysis Tool for each image. "
                    f"Pass the image path as the first argument and the user's story as context.\n"
                    f"Produce a detailed forensic damage and authenticity report."
                ),
                expected_output="A forensic damage and authenticity report with severity, tampering check, and red flags.",
                agent=agents[2]
            )
            backend_tasks.append(t3)
            backend_agents.append(agents[2])

        # ── Task: Fraud Investigation (always runs) ──
        t4 = Task(
            description=(
                f"Investigate this claim for potential fraud.\n"
                f"User ID: {user_id}\n"
                f"User's claim story: {summary}\n"
                f"{'Visual evidence has been analyzed by the Vision Analyst — review their findings above.' if has_images else 'WARNING: No visual evidence was uploaded. The absence of photos may itself be a red flag for certain claim types.'}\n\n"
                f"Use the Claim History Lookup Tool with user_id '{user_id}' to check their past claims.\n"
                f"Cross-reference everything: Do past claims show a pattern? "
                f"Does the story have temporal contradictions? "
                f"Is the damage consistent with the claimed timeline?\n\n"
                f"Output a fraud risk level: LOW, MEDIUM, or HIGH with detailed, evidence-backed reasoning."
            ),
            expected_output="Fraud risk assessment: LOW/MEDIUM/HIGH with specific evidence and reasoning.",
            agent=agents[3]
        )
        backend_tasks.append(t4)
        backend_agents.append(agents[3])

        # ── Task: Final Assessment + DB Save (always runs) ──
        t5 = Task(
            description=(
                f"Review ALL prior agent reports and reach a final verdict on this claim.\n"
                f"User ID: {user_id}\n\n"
                f"Synthesize:\n"
                f"1. Policy Agent's coverage and risk report\n"
                f"{'2. Vision Analyst damage and authenticity assessment' if has_images else '2. Note: No visual evidence was provided by the claimant'}\n"
                f"3. Fraud Detective's risk assessment and history analysis\n\n"
                f"Determine your verdict — one of:\n"
                f"- AUTO_APPROVE: Clean claim, no red flags, policy is active\n"
                f"- FLAGGED: Some concerns exist, needs human review\n"
                f"- DENIED: Clear evidence of fraud or policy violation\n\n"
                f"Then you MUST use the Save Claim Verdict Tool to persist your decision:\n"
                f"- user_id: {user_id}\n"
                f"- conversation_id: {conversation_id}\n"
                f"- transcript_summary: A concise summary of the claim and evidence\n"
                f"- verdict: Your verdict (AUTO_APPROVE, FLAGGED, or DENIED)\n"
                f"- reasoning: Your detailed reasoning\n\n"
                f"This creates a permanent audit record. Be thorough and fair."
            ),
            expected_output="Final verdict (AUTO_APPROVE/FLAGGED/DENIED) with reasoning, confirmed saved to database.",
            agent=agents[4]
        )
        backend_tasks.append(t5)
        backend_agents.append(agents[4])

        # ── Task: User Communication (always runs) ──
        t6 = Task(
            description=(
                f"Based on the Final Assessor's verdict, draft and send a professional "
                f"status update message to the user.\n"
                f"User ID: {user_id}\n\n"
                f"Message guidelines by verdict:\n"
                f"- AUTO_APPROVE: 'Great news! Your claim has been reviewed and approved...'\n"
                f"- FLAGGED: 'Thank you for your submission. Our team will conduct a brief additional review...' (do NOT mention fraud suspicion)\n"
                f"- DENIED: 'After careful review, we are unable to approve this claim at this time...' (include appeal info)\n\n"
                f"Use the Email Communication Tool to dispatch the message.\n"
                f"NEVER reveal internal fraud scores, agent names, or detailed reasoning to the user."
            ),
            expected_output="Confirmation that a professional user message was drafted and dispatched.",
            agent=agents[5]
        )
        backend_tasks.append(t6)
        backend_agents.append(agents[5])

        # ── Run the backend crew with HIERARCHICAL process ──
        # The manager LLM dynamically plans execution order and routing.
        # It can skip agents, re-order tasks, or ask follow-ups.
        backend_crew = Crew(
            agents=backend_agents,
            tasks=backend_tasks,
            process=Process.hierarchical,
            manager_llm=self.manager_llm,
            # memory=self.create_memory(user_id), # Temporarily disabled to avoid heavy 429 quota limits on Memory Save
            memory=False,
            verbose=True,
            max_rpm=2  # Intentionally very slow (2 requests per min) to simulate natural review and completely avoid API rate limits
        )

        final_result = backend_crew.kickoff()

        # Return a user-friendly summary
        result_str = str(final_result)

        # If the result contains the raw verdict JSON, extract a friendly message
        if "AUTO_APPROVE" in result_str.upper():
            return "Your claim has been reviewed and approved. You should receive a confirmation email shortly with next steps for processing your payout."
        elif "FLAGGED" in result_str.upper():
            return "Thank you for your submission. Our review team will conduct a brief additional review of your claim. You'll receive an update within 24-48 hours."
        elif "DENIED" in result_str.upper():
            return "After careful review of the evidence provided, we are unable to approve this claim at this time. You may file an appeal within 30 days with additional supporting documentation."
        else:
            return f"Your claim has been processed by our multi-agent review system. {result_str}"
