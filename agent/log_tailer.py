"""
Tails catalina.out and the Tomcat access log.

Uses seek-position tracking so the agent only processes new lines
across each 30-second poll cycle, and survives log rotation.
"""

import glob
import os
import re
import time
from dataclasses import dataclass, field
from typing import List

# ── regex pre-filter ─────────────────────────────────────────────────────────
# Only lines matching at least one of these patterns are forwarded to the LLM.
# This keeps prompt size small and reduces inference cost.
_SIGNAL_PATTERNS = [
    re.compile(r"SEVERE|ERROR|Exception|Error"),
    re.compile(r"OutOfMemoryError|java\.lang\.OutOfMemory"),
    re.compile(r"NullPointerException|java\.lang\.NullPointer"),
    re.compile(r"GC overhead limit|GCLocker|FullGC|Full GC"),
    re.compile(r"Connection refused|Cannot get a connection|Communications link failure"),
    re.compile(r"\b5\d{2}\b"),          # HTTP 5xx in access log
    re.compile(r"catalina\.out.*WARN"),
]


def _is_signal_line(line: str) -> bool:
    return any(p.search(line) for p in _SIGNAL_PATTERNS)


# ── file position tracker ─────────────────────────────────────────────────────
@dataclass
class _FileState:
    path: str
    inode: int = 0
    position: int = 0


class LogTailer:
    """
    Tracks multiple log files and yields only newly-written signal lines.

    Usage:
        tailer = LogTailer(cfg)
        while True:
            lines = tailer.collect()
            time.sleep(30)
    """

    def __init__(self, cfg: dict):
        self._catalina = cfg["tomcat"]["catalina_out"]
        self._access_glob = cfg["tomcat"]["access_log"]
        self._states: dict[str, _FileState] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def collect(self) -> List[str]:
        """Return all new signal lines since the last call."""
        lines: List[str] = []
        for path in self._tracked_paths():
            lines.extend(self._read_new_lines(path))
        return lines

    # ── internals ─────────────────────────────────────────────────────────────

    def _tracked_paths(self) -> List[str]:
        paths = []
        if os.path.exists(self._catalina):
            paths.append(self._catalina)
        # Expand access log glob (e.g. localhost_access_log.2024-01-15.txt)
        paths.extend(glob.glob(self._access_glob))
        return paths

    def _read_new_lines(self, path: str) -> List[str]:
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return []

        state = self._states.get(path)

        # Detect log rotation: inode changed or file shrank
        if state is None or state.inode != stat.st_ino or stat.st_size < state.position:
            state = _FileState(path=path, inode=stat.st_ino, position=0)
            self._states[path] = state

        new_lines: List[str] = []
        try:
            with open(path, "r", errors="replace") as f:
                f.seek(state.position)
                for raw in f:
                    line = raw.rstrip("\n")
                    if _is_signal_line(line):
                        new_lines.append(line)
                state.position = f.tell()
        except OSError:
            pass

        return new_lines
