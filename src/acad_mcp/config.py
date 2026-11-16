"""Configuration through environment variables (all optional).

ACAD_MCP_LANG=en|pt           language of tool descriptions and messages (default en)
ACAD_MCP_READ_ONLY=1          register read-only tools only
ACAD_MCP_ALLOW_LISP=0         disable run_lisp
ACAD_MCP_ALLOW_COMMANDS=0     disable run_command
ACAD_MCP_AUTO_RESTORE=1       restore a minimized AutoCAD window when needed (AutoCAD may grab focus)
ACAD_MCP_COMMAND_TIMEOUT=30   seconds before a command waiting for input is cancelled (ESC)
ACAD_MCP_BUSY_TIMEOUT=30      seconds to keep retrying while AutoCAD says "busy"
ACAD_MCP_LAUNCH=1             start AutoCAD if it is not running
ACAD_MCP_EXTRA_BLOCKED=A,B    extra commands to block in run_command
ACAD_MCP_FAST_THRESHOLD=300   entity count above which queries use in-process LISP (much faster)
ACAD_MCP_UNDO_MARKER=0        do not keep the undo-step counter in drawings (undo then reads English prompts)
ACAD_MCP_LOG=0                no operation log   (ACAD_MCP_LOG_ARGS=none|summary|full, ACAD_MCP_LOG_DIR=folder)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "sim", "on"}


# Commands that start external programs, load code, close AutoCAD or run scripts past the guards.
DEFAULT_BLOCKED_COMMANDS = {
    "SHELL", "SH", "START", "NETLOAD", "APPLOAD", "ARX", "VBALOAD", "VBARUN", "VBAMAN",
    "VBAIDE", "SCRIPT", "QUIT", "EXIT", "CLOSEALL", "SECURELOAD", "TRUSTEDPATHS",
}

# LISP functions blocked in run_lisp (basic guard, NOT a sandbox).
DEFAULT_BLOCKED_LISP = {
    "startapp", "vl-file-delete", "vl-file-rename", "vl-registry-write", "vl-registry-delete",
    "dos_", "acet-sys-", "acet-file-", "vlax-create-object", "vlax-get-or-create-object",
    "shell", "netload", "arxload", "vl-vbaload", "vl-vbarun", "setenv",
}


@dataclass
class Config:
    read_only: bool = False
    allow_lisp: bool = True
    allow_commands: bool = True
    auto_restore: bool = False
    undo_marker: bool = True
    command_timeout: float = 30.0
    busy_timeout: float = 30.0
    launch_if_missing: bool = False
    fast_threshold: int = 300
    blocked_commands: set[str] = field(default_factory=lambda: set(DEFAULT_BLOCKED_COMMANDS))
    blocked_lisp: set[str] = field(default_factory=lambda: set(DEFAULT_BLOCKED_LISP))

    @classmethod
    def from_env(cls) -> "Config":
        c = cls(
            read_only=_flag("ACAD_MCP_READ_ONLY", False),
            allow_lisp=_flag("ACAD_MCP_ALLOW_LISP", True),
            allow_commands=_flag("ACAD_MCP_ALLOW_COMMANDS", True),
            auto_restore=_flag("ACAD_MCP_AUTO_RESTORE", False),
            undo_marker=_flag("ACAD_MCP_UNDO_MARKER", True),
            command_timeout=float(os.environ.get("ACAD_MCP_COMMAND_TIMEOUT", "30")),
            busy_timeout=float(os.environ.get("ACAD_MCP_BUSY_TIMEOUT", "30")),
            launch_if_missing=_flag("ACAD_MCP_LAUNCH", False),
            fast_threshold=int(os.environ.get("ACAD_MCP_FAST_THRESHOLD", "300")),
        )
        extra = os.environ.get("ACAD_MCP_EXTRA_BLOCKED", "")
        c.blocked_commands |= {x.strip().upper() for x in extra.split(",") if x.strip()}
        return c
