"""
Amazon Bedrock AI engine.

Invokes Claude Sonnet via the Bedrock Converse API (boto3) and returns a
structured action decision as a parsed Python dict.

Authentication: boto3 picks up credentials automatically from:
  1. Environment variables  (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
  2. ~/.aws/credentials      (aws configure)
  3. IAM instance role       (if running on EC2)
"""

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ── Runbook system prompt ─────────────────────────────────────────────────────
_RUNBOOK = """
You are an autonomous middleware self-healing agent for Apache Tomcat on RHEL.
Analyse the provided log lines and respond with a JSON object ONLY — no prose, no markdown, no code fences.

## Incident rules

1. HTTP 500 errors  (>=5 in recent window)
   action: "health_check"
   additional_actions: ["thread_dump", "heap_dump", "restart"]
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

## Required JSON schema — respond with this exact structure, nothing else
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
        bedrock_cfg = cfg.get("bedrock", {})
        self._model_id = bedrock_cfg.get(
            "model_id",
            "us.anthropic.claude-sonnet-4-5-20250514-v1:0",
        )
        self._max_tokens = int(bedrock_cfg.get("max_tokens", 1024))
        region = bedrock_cfg.get("region", "us-east-1")

        # boto3 auto-reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from env
        self._client = boto3.client("bedrock-runtime", region_name=region)
        logger.info(
            "AIEngine ready — model=%s region=%s", self._model_id, region
        )

    def analyze(self, log_lines: list[str], state_summary: dict) -> dict[str, Any]:
        """
        Send filtered log lines + incident state to Claude via Bedrock Converse API.
        Returns a parsed action-decision dict.
        """
        log_text = "\n".join(log_lines[-200:])
        user_msg = (
            f"Current incident counts:\n{json.dumps(state_summary, indent=2)}\n\n"
            f"New Tomcat log lines:\n{log_text}\n\n"
            "Respond with the JSON decision only."
        )

        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=[{"text": _RUNBOOK}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_msg}],
                    }
                ],
                inferenceConfig={
                    "maxTokens": self._max_tokens,
                    "temperature": 0.0,   # deterministic decisions
                },
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            logger.error("Bedrock ClientError [%s]: %s", code, e)
            return _fallback_watch(f"ClientError: {code}")
        except BotoCoreError as e:
            logger.error("Bedrock BotoCoreError: %s", e)
            return _fallback_watch(str(e))
        except Exception as e:
            logger.error("Unexpected Bedrock error: %s", e)
            return _fallback_watch(str(e))

        # Extract text from Converse response
        try:
            raw = response["output"]["message"]["content"][0]["text"]
        except (KeyError, IndexError) as e:
            logger.error("Unexpected Bedrock response shape: %s | response=%s", e, response)
            return _fallback_watch("unexpected response shape")

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
    Falls back to 'watch' if the response cannot be parsed.
    """
    text = raw.strip()

    # Strip accidental markdown code fences
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
                logger.warning("Could not parse Bedrock JSON: %s\nRaw: %s", e, raw[:300])
                return _fallback_watch("json parse error")
        else:
            logger.warning("No JSON object in Bedrock response. Raw: %s", raw[:300])
            return _fallback_watch("no json found")

    # Ensure all required keys exist with safe defaults
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
