"""
Ollama-based AI engine.

Sends filtered log lines to a locally-running Ollama model and returns
a structured action decision as a parsed Python dict.

Ollama is queried with format="json" which forces JSON-only output.
The system prompt makes the expected schema explicit so smaller models
(phi3:mini, gemma2:2b, llama3.2:3b) reliably produce parseable responses.
"""

import json
import logging
from typing import Any

import ollama

logger = logging.getLogger(__name__)

# ── System / runbook prompt ───────────────────────────────────────────────────
# Kept deliberately short for small models — less context = faster inference
# and fewer hallucinations on constrained hardware.
_RUNBOOK = """
You are an autonomous middleware self-healing agent for Apache Tomcat on RHEL.
Analyse the provided log lines and respond with a JSON object ONLY — no prose, no markdown, no code fences.

## Incident rules

1. HTTP 500 errors (>=5 in recent window)
   action: "health_check"
   additional_actions: ["thread_dump","heap_dump","restart"]  (only if still failing)
   urgency: high

2. OutOfMemoryError
   action: "thread_dump"
   additional_actions: ["increase_heap"]
   notify_admin: true if oom_count_total > 2
   urgency: critical

3. DB connectivity failure  ("Connection refused", "Communications link failure", "Cannot get a connection")
   action: "telnet_check"
   additional_actions: ["notify_admin"]
   urgency: critical

4. NullPointerException
   action: "restart"
   notify_admin: true if npe_count_total > 1
   urgency: high

5. GC issues  ("GC overhead limit", "FullGC", "GCLocker")
   action: "notify_admin"
   urgency: medium

6. No actionable signal
   action: "watch"
   urgency: low

## Required JSON schema (respond with this exact structure)
{
  "incident_type": "<http500|oom|db_connectivity|npe|gc|none>",
  "action": "<watch|health_check|thread_dump|heap_dump|restart|increase_heap|telnet_check|notify_admin>",
  "additional_actions": [],
  "reason": "<one sentence>",
  "urgency": "<low|medium|high|critical>",
  "notify_admin": false,
  "notification_message": ""
}
""".strip()


class AIEngine:
    def __init__(self, cfg: dict):
        ollama_cfg = cfg.get("ollama", {})
        self._model = ollama_cfg.get("model", "phi3:mini")
        self._host = ollama_cfg.get("host", "http://localhost:11434")
        self._timeout = int(ollama_cfg.get("timeout", 120))
        self._client = ollama.Client(host=self._host)
        logger.info("AIEngine ready — model=%s host=%s", self._model, self._host)

    def analyze(self, log_lines: list[str], state_summary: dict) -> dict[str, Any]:
        """
        Send filtered log lines + incident state to Ollama.
        Returns a parsed action-decision dict.
        """
        log_text = "\n".join(log_lines[-150:])  # cap to keep prompt small
        user_msg = (
            f"Current incident counts:\n{json.dumps(state_summary, indent=2)}\n\n"
            f"New Tomcat log lines:\n{log_text}\n\n"
            "Respond with the JSON decision only."
        )

        try:
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": _RUNBOOK},
                    {"role": "user",   "content": user_msg},
                ],
                format="json",          # forces JSON-only output
                options={
                    "temperature": 0,   # deterministic decisions
                    "num_predict": 512, # cap output tokens — decision is small
                },
            )
        except ollama.ResponseError as e:
            logger.error("Ollama response error: %s", e)
            return _fallback_watch(str(e))
        except Exception as e:
            logger.error("Ollama connection error: %s", e)
            return _fallback_watch(str(e))

        raw = response.message.content
        decision = _parse_json(raw)

        logger.info(
            "AI decision: %s / %s (urgency=%s)",
            decision.get("incident_type"),
            decision.get("action"),
            decision.get("urgency"),
        )
        return decision


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """
    Parse the model response, tolerating minor formatting issues.
    Falls back to 'watch' if the response can't be parsed.
    """
    text = raw.strip()

    # Strip accidental code fences some models add despite format="json"
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            l for l in lines if not l.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting the first {...} block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError as e:
                logger.warning("Could not parse Ollama JSON: %s\nRaw: %s", e, raw[:300])
                return _fallback_watch("json parse error")
        else:
            logger.warning("No JSON object in Ollama response. Raw: %s", raw[:300])
            return _fallback_watch("no json found")

    # Ensure required keys exist
    data.setdefault("incident_type", "none")
    data.setdefault("action", "watch")
    data.setdefault("additional_actions", [])
    data.setdefault("reason", "")
    data.setdefault("urgency", "low")
    data.setdefault("notify_admin", False)
    data.setdefault("notification_message", "")
    return data


def _fallback_watch(reason: str) -> dict:
    return {
        "incident_type": "none",
        "action": "watch",
        "additional_actions": [],
        "reason": f"Fallback watch — {reason}",
        "urgency": "low",
        "notify_admin": False,
        "notification_message": "",
    }
