"""
Executes remediation actions on the local RHEL / Tomcat host.

Each public method returns a dict with keys:
  success: bool
  output:  str   (stdout/stderr or description)
"""

import logging
import os
import re
import socket
import subprocess
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._tomcat = cfg["tomcat"]
        self._java = cfg["java"]
        self._dumps_dir = cfg["agent"]["dumps_dir"]
        os.makedirs(self._dumps_dir, exist_ok=True)

    # ── Health check ──────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """HTTP GET the app health endpoint; returns success=True on 2xx."""
        import urllib.request
        url = self._tomcat["webapps_url"] + self._tomcat.get("health_check_path", "/")
        logger.info("Health check: %s", url)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                code = resp.status
                ok = 200 <= code < 300
                return {"success": ok, "output": f"HTTP {code}"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ── Thread dump ───────────────────────────────────────────────────────────

    def thread_dump(self) -> dict:
        pid = self._tomcat_pid()
        if pid is None:
            return {"success": False, "output": "Tomcat PID not found"}

        ts = _ts()
        out_file = os.path.join(self._dumps_dir, f"thread_dump_{ts}.txt")
        result = _run(["jstack", str(pid)], capture=True)
        if result["success"]:
            with open(out_file, "w") as f:
                f.write(result["output"])
            logger.info("Thread dump saved: %s", out_file)
            return {"success": True, "output": f"Saved to {out_file}"}
        return result

    # ── Heap dump ────────────────────────────────────────────────────────────

    def heap_dump(self) -> dict:
        pid = self._tomcat_pid()
        if pid is None:
            return {"success": False, "output": "Tomcat PID not found"}

        ts = _ts()
        out_file = os.path.join(self._dumps_dir, f"heap_dump_{ts}.hprof")
        result = _run(["jmap", f"-dump:format=b,file={out_file}", str(pid)])
        if result["success"]:
            logger.info("Heap dump saved: %s", out_file)
            return {"success": True, "output": f"Saved to {out_file}"}
        return result

    # ── Restart Tomcat ────────────────────────────────────────────────────────

    def restart(self) -> dict:
        svc = self._tomcat["service_name"]
        logger.info("Restarting Tomcat service: %s", svc)
        result = _run(["systemctl", "restart", svc])
        if result["success"]:
            # Give Tomcat 15 seconds to come up before health-checking
            time.sleep(15)
        return result

    # ── Increase heap ─────────────────────────────────────────────────────────

    def increase_heap(self) -> dict:
        """
        Reads setenv.sh, bumps -Xmx and -Xms by heap_increment_mb, writes back.
        Takes effect on next Tomcat restart (we don't auto-restart here).
        """
        setenv = self._tomcat["setenv_sh"]
        increment = self._java.get("heap_increment_mb", 500)

        try:
            if os.path.exists(setenv):
                with open(setenv, "r") as f:
                    content = f.read()
            else:
                content = ""

            content, new_xmx = _bump_heap_flag(content, "Xmx", increment)
            content, new_xms = _bump_heap_flag(content, "Xms", increment)

            # Ensure CATALINA_OPTS line exists
            if "CATALINA_OPTS" not in content:
                content = f'export CATALINA_OPTS="-Xms{new_xms}m -Xmx{new_xmx}m"\n' + content

            with open(setenv, "w") as f:
                f.write(content)

            msg = f"Heap increased by {increment}MB → Xmx={new_xmx}m Xms={new_xms}m (restart required)"
            logger.info(msg)
            return {"success": True, "output": msg}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ── DB telnet check ───────────────────────────────────────────────────────

    def telnet_check(self) -> dict:
        db_cfg = self._cfg.get("database", {})
        host = db_cfg.get("host", "localhost")
        port = int(db_cfg.get("port", 5432))
        logger.info("Telnet check: %s:%s", host, port)
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            return {"success": True, "output": f"Connected to {host}:{port}"}
        except Exception as e:
            return {"success": False, "output": f"Cannot reach {host}:{port} — {e}"}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _tomcat_pid(self) -> str | None:
        result = _run(
            ["pgrep", "-f", "org.apache.catalina.startup.Bootstrap"],
            capture=True,
        )
        if result["success"] and result["output"].strip():
            return result["output"].strip().split()[0]
        return None


# ── Module-level helpers ──────────────────────────────────────────────────────

def _run(cmd: list, capture: bool = False) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=120,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "") + (proc.stderr or "") if capture else f"exit {proc.returncode}"
        return {"success": ok, "output": out.strip()}
    except Exception as e:
        return {"success": False, "output": str(e)}


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _bump_heap_flag(content: str, flag: str, increment_mb: int) -> tuple[str, int]:
    """Find -Xflag<N>m in content, bump N by increment_mb, return new content and new value."""
    pattern = re.compile(rf"-{flag}(\d+)m", re.IGNORECASE)
    match = pattern.search(content)
    if match:
        old_val = int(match.group(1))
        new_val = old_val + increment_mb
        content = pattern.sub(f"-{flag}{new_val}m", content)
        return content, new_val
    # Flag not present; use initial_heap_mb as baseline
    new_val = 512 + increment_mb
    return content, new_val
