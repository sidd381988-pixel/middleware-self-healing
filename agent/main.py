"""
Middleware Self-Healing Agent — main orchestrator.

Loop (every 30 seconds):
  1. Collect new signal lines from Tomcat logs
  2. If lines found → ask Claude for an action decision
  3. Execute the decided action(s)
  4. Update incident state in SQLite
  5. Send email notification if required
"""

import logging
import os
import sys
import time

# Allow running directly: python agent/main.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.config_loader import load_config
from agent import state_manager
from agent.log_tailer import LogTailer
from agent.ai_engine import AIEngine
from agent.action_executor import ActionExecutor
from agent.notifier import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# Cooldown: don't restart more often than once every 5 minutes
_RESTART_COOLDOWN_SECONDS = 300
# Cooldown: don't increase heap more than once every 10 minutes
_HEAP_INCREASE_COOLDOWN_SECONDS = 600


def build_state_summary(cfg: dict) -> dict:
    window = cfg["thresholds"].get("http500_window_seconds", 120)
    return {
        "oom_count_total": state_manager.count_incidents("oom"),
        "npe_count_total": state_manager.count_incidents("npe"),
        "http500_recent": state_manager.count_incidents("http500", since_seconds=window),
        "db_connectivity_count": state_manager.count_incidents("db_connectivity"),
        "gc_count_total": state_manager.count_incidents("gc"),
    }


def execute_decision(decision: dict, executor: ActionExecutor, notifier: Notifier, cfg: dict):
    incident_type = decision.get("incident_type", "none")
    primary = decision.get("action", "watch")
    secondary = decision.get("additional_actions", [])
    all_actions = [primary] + [a for a in secondary if a != primary]

    for action in all_actions:
        if action == "watch":
            continue

        logger.info("Executing action: %s (incident=%s)", action, incident_type)
        result = _dispatch(action, executor, cfg)

        state_manager.record_action(incident_type, action, result.get("output"))

        if result["success"]:
            logger.info("  ✓ %s succeeded: %s", action, result["output"])
        else:
            logger.warning("  ✗ %s failed: %s", action, result["output"])

    if decision.get("notify_admin") and decision.get("notification_message"):
        urgency = decision.get("urgency", "medium")
        subject = f"[{urgency.upper()}] {incident_type} detected on Tomcat"
        notifier.notify_admin(subject, decision["notification_message"])


def _dispatch(action: str, executor: ActionExecutor, cfg: dict) -> dict:
    thresholds = cfg.get("thresholds", {})

    if action == "health_check":
        return executor.health_check()

    elif action == "thread_dump":
        return executor.thread_dump()

    elif action == "heap_dump":
        return executor.heap_dump()

    elif action == "restart":
        # Cooldown guard: don't hammer restarts
        secs = state_manager.seconds_since_last_action("*", "restart")
        if secs is not None and secs < _RESTART_COOLDOWN_SECONDS:
            msg = f"Restart skipped — last restart was {int(secs)}s ago (cooldown={_RESTART_COOLDOWN_SECONDS}s)"
            logger.info(msg)
            return {"success": True, "output": msg}
        return executor.restart()

    elif action == "increase_heap":
        secs = state_manager.seconds_since_last_action("oom", "increase_heap")
        if secs is not None and secs < _HEAP_INCREASE_COOLDOWN_SECONDS:
            msg = f"Heap increase skipped — last increase was {int(secs)}s ago"
            logger.info(msg)
            return {"success": True, "output": msg}
        return executor.increase_heap()

    elif action == "telnet_check":
        return executor.telnet_check()

    elif action == "notify_admin":
        # Handled separately after all actions; return no-op here
        return {"success": True, "output": "notification scheduled"}

    else:
        return {"success": False, "output": f"Unknown action: {action}"}


def record_incident_from_decision(decision: dict, cfg: dict):
    """Persist the incident type so counters stay accurate."""
    incident_type = decision.get("incident_type", "none")
    if incident_type == "none":
        return
    state_manager.record_incident(incident_type, decision.get("reason", ""))


def run():
    cfg = load_config()
    state_manager.init(cfg["agent"]["state_db"])

    tailer = LogTailer(cfg)
    engine = AIEngine(cfg)
    executor = ActionExecutor(cfg)
    notifier = Notifier(cfg)

    poll = cfg["agent"].get("poll_interval_seconds", 30)
    logger.info("Middleware self-healing agent started (poll=%ss)", poll)

    while True:
        try:
            lines = tailer.collect()

            if lines:
                logger.info("Collected %d signal lines — consulting Claude", len(lines))
                state_summary = build_state_summary(cfg)
                decision = engine.analyze(lines, state_summary)

                record_incident_from_decision(decision, cfg)
                execute_decision(decision, executor, notifier, cfg)
            else:
                logger.debug("No new signal lines — watching")

        except KeyboardInterrupt:
            logger.info("Agent stopped by user")
            break
        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)

        time.sleep(poll)


if __name__ == "__main__":
    run()
