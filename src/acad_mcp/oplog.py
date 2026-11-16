"""Operation log: one JSON line per tool call, kept on this PC, for troubleshooting.

Where:  %LOCALAPPDATA%\\acad-mcp\\logs\\acad-mcp-YYYY-MM.jsonl   (ACAD_MCP_LOG_DIR overrides)
What:   time, tool, duration, ok / error message, engine, and the arguments SUMMARIZED: numbers and short
        strings as they are; long strings, code and file contents only as a size (ACAD_MCP_LOG_ARGS=none|summary|full).
Off:    ACAD_MCP_LOG=0

Logging must never break or slow a tool: every failure in here is swallowed.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
KEEP = 3
SHORT = 60
MAX_ERROR = 240
SIZE_ONLY_KEYS = {"code", "content_text", "content_base64"}

_lock = threading.Lock()
_context: dict[str, Any] = {}


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "sim", "on"}


def enabled() -> bool:
    return _flag("ACAD_MCP_LOG", True)


def args_mode() -> str:
    m = os.environ.get("ACAD_MCP_LOG_ARGS", "summary").strip().lower()
    return m if m in {"none", "summary", "full"} else "summary"


def log_dir() -> Path:
    custom = os.environ.get("ACAD_MCP_LOG_DIR", "").strip()
    if custom:
        return Path(custom)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "acad-mcp" / "logs"


def current_file() -> Path:
    return log_dir() / f"acad-mcp-{time.strftime('%Y-%m')}.jsonl"


def set_context(**kw: Any) -> None:
    """Facts added to every line from now on (e.g. the AutoCAD version, once known)."""
    _context.update({k: v for k, v in kw.items() if v is not None})


def summarize(value: Any, key: str | None = None, depth: int = 0) -> Any:
    """Small, privacy-minded picture of an argument."""
    if key in SIZE_ONLY_KEYS and isinstance(value, str):
        return f"<{len(value)} chars>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, str):
        return value if len(value) <= SHORT else f"<str {len(value)} chars>"
    if isinstance(value, (list, tuple)):
        if len(value) <= 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
            return [round(x, 4) if isinstance(x, float) else x for x in value]  # a point
        if depth >= 2:
            return f"<list {len(value)}>"
        return {"len": len(value), "first": [summarize(x, None, depth + 1) for x in value[:3]]}
    if isinstance(value, dict):
        if depth >= 2:
            return f"<dict {len(value)} keys>"
        out = {str(k): summarize(v, str(k), depth + 1) for k, v in list(value.items())[:12]}
        if len(value) > 12:
            out["..."] = f"+{len(value) - 12} keys"
        return out
    return f"<{type(value).__name__}>"


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_BYTES:
        return
    for i in range(KEEP - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        if src.exists():
            if i + 1 >= KEEP:
                src.unlink()
            else:
                src.replace(path.with_name(f"{path.name}.{i + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def record(tool: str, args: dict[str, Any] | None, ms: float, error: str | None = None, result: Any = None) -> None:
    if not enabled():
        return
    try:
        from . import __version__

        line: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "v": __version__,
            "tool": tool,
            "ms": round(ms),
            "ok": error is None,
        }
        if error is not None:
            line["err"] = " ".join(str(error).split())[:MAX_ERROR]  # error texts may quote data: keep them short
        if isinstance(result, dict) and isinstance(result.get("engine"), str):
            line["engine"] = result["engine"]
        mode = args_mode()
        if args and mode == "summary":
            line["args"] = {k: summarize(v, k) for k, v in args.items() if v is not None}
        elif args and mode == "full":
            line["args"] = args
        line.update(_context)
        line["host"] = socket.gethostname()
        line["user"] = getpass.getuser()
        text = json.dumps(line, ensure_ascii=False, default=str)
        with _lock:
            path = current_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
    except Exception:  # noqa: BLE001 - logging never breaks a tool
        pass


def read_recent(days: int = 30, limit: int = 5000) -> list[dict[str, Any]]:
    """Most recent entries (oldest first), from the current and rotated files."""
    out: list[dict[str, Any]] = []

    def order(p: Path) -> tuple[str, int]:
        # acad-mcp-2026-11.jsonl.2 (oldest) < .1 < acad-mcp-2026-11.jsonl (current)
        base, _, rot = p.name.partition(".jsonl")
        return base, -int(rot[1:]) if rot[1:].isdigit() else 0

    try:
        files = sorted(log_dir().glob("acad-mcp-*.jsonl*"), key=order)
    except OSError:
        return out
    cutoff = time.time() - days * 86400
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                continue
            with open(f, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    try:
                        out.append(json.loads(raw))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out[-limit:]
