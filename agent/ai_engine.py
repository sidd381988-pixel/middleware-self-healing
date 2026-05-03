"""
Claude API integration.

Sends filtered log lines to claude-opus-4-7 with adaptive thinking and
prompt-cached runbook system prompt.  Returns a structured action decision.
"""

import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# ── Runbook system prompt (static → cached) ───────────────────────────────────
_RUNBOOK = """
You are an autonomous middleware self-healing agent monitoring Apache Tomcat on RHEL.
Your job is to analyze log snippets and decide the correct remediation action.

## Known incident types and required responses

### 1. HTTP 500 errors (continuous)
- Trigger: ≥5 HTTP 500 responses within 2 minutes
- Step 1: Execute health_check against the app URL
- Step 2 (if still failing): Execute thread_dump + heap_dump, then restart
- Action: "health_check" → if still 500 → "thread_dump,heap_dump,restart"

### 2. OutOfMemoryError (OOM)
- Trigger: java.lang.OutOfMemoryError in catalina.out
- Step 1: Execute thread_dump, then increase_heap (+500 MB)
- Step 2: If OOM has occurred MORE THAN 2 times total → also notify_admin
- Action: "thread_dump,increase_heap" (+ "notify_admin" if count > 2)

### 3. Database connectivity issue
- Trigger: "Connection refused", "Cannot get a connection", "Communications link failure"
- Step 1: Execute telnet_check against the database host/port
- Step 2: notify_admin immediately
- Action: "telnet_check,notify_admin"

### 4. NullPointerException (NPE)
- Trigger: java.lang.NullPointerException in catalina.out
- Step 1: restart Tomcat (logs are auto-preserved)
- Step 2: If NPE persists (occurs again after restart) → notify_admin
- Action: "restart" (+ "notify_admin" if recurring)

### 5. GC issues
- Trigger: "GC overhead limit exceeded", "FullGC", "GCLocker"
- Step 1: Analyze heap usage pattern
- Step 2: notify_admin with tuning recommendations
- Action: "notify_admin"

## Output rules
Respond ONLY with valid JSON matching the schema below — no prose, no markdown.
If the logs contain no actionable signal, set action to "watch".

Schema:
{
  "incident_type": "http500|oom|db_connectivity|npe|gc|none",
  "action": "watch|health_check|thread_dump|heap_dump|restart|increase_heap|telnet_check|notify_admin",
  "additional_actions": ["list of secondary actions to execute after the primary"],
  "reason": "one sentence explaining the decision",
  "urgency": "low|medium|high|critical",
  "notify_admin": true|false,
  "notification_message": "message text if notify_admin is true, else empty string"
}
""".strip()

# JSON schema for structured output enforcement
_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "incident_type": {
            "type": "string",
            "enum": ["http500", "oom", "db_connectivity", "npe", "gc", "none"],
        },
        "action": {
            "type": "string",
            "enum": [
                "watch", "health_check", "thread_dump", "heap_dump",
                "restart", "increase_heap", "telnet_check", "notify_admin",
            ],
        },
        "additional_actions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reason": {"type": "string"},
        "urgency": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "notify_admin": {"type": "boolean"},
        "notification_message": {"type": "string"},
    },
    "required": [
        "incident_type", "action", "additional_actions",
        "reason", "urgency", "notify_admin", "notification_message",
    ],
}


class AIEngine:
    def __init__(self, cfg: dict):
        self._client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])

    def analyze(self, log_lines: list[str], state_summary: dict) -> dict[str, Any]:
        """
        Send filtered log lines to Claude and return a parsed action decision.

        state_summary: dict with incident counts, e.g.
            {"oom_count": 3, "npe_count": 1, "http500_recent": 7}
        """
        log_text = "\n".join(log_lines[-200:])  # cap at 200 lines
        user_msg = (
            f"Current incident state:\n{json.dumps(state_summary, indent=2)}\n\n"
            f"New log lines:\n{log_text}"
        )

        try:
            response = self._client.messages.create(
                model="claude-opus-4-7",
                max_tokens=1024,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": _RUNBOOK,
                        "cache_control": {"type": "ephemeral"},  # prompt caching
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "action_decision",
                            "schema": _ACTION_SCHEMA,
                            "strict": True,
                        },
                    }
                },
            )
        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            return _fallback_watch(str(e))

        # Extract text block from response
        text_block = next(
            (b.text for b in response.content if hasattr(b, "text")), None
        )
        if not text_block:
            logger.warning("No text block in Claude response")
            return _fallback_watch("empty response")

        try:
            decision = json.loads(text_block)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Claude JSON: %s\nRaw: %s", e, text_block)
            return _fallback_watch("json parse error")

        logger.info(
            "AI decision: %s / %s (urgency=%s)",
            decision.get("incident_type"),
            decision.get("action"),
            decision.get("urgency"),
        )
        return decision


def _fallback_watch(reason: str) -> dict:
    return {
        "incident_type": "none",
        "action": "watch",
        "additional_actions": [],
        "reason": f"Fallback watch due to: {reason}",
        "urgency": "low",
        "notify_admin": False,
        "notification_message": "",
    }
