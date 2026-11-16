"""Command line and AutoLISP via SendCommand, with a watchdog against hangs.

SendCommand blocks until the command finishes. If the command keeps waiting for input
(e.g. '_.LINE 0,0' with no more points) or opens a dialog, the call would hang forever.
The watchdog (Timer) closes dialogs and sends ESC to the drawing area after `timeout`
seconds, which releases the call.

Measured on AutoCAD 2027:
- With the window minimized, SendCommand is queued and AutoCAD freezes when restored.
  Restoring BEFORE sending works, but AutoCAD grabs keyboard focus, so auto-restore is
  opt-in (ACAD_MCP_AUTO_RESTORE=1).
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any

import pywintypes
import win32con
import win32gui

from ..i18n import t
from .errors import AcadError
from .winutil import cancel_everything, is_minimized

AUTO_RESTORE = False  # set by server.build() from Config

MINIMIZED_MSG = t(
    "The AutoCAD window is minimized. While minimized, commands get stuck in a queue and AutoCAD "
    "freezes when restored. Ask the user to restore the AutoCAD window (it may stay behind other "
    "windows, just not minimized) and try again. (Or start the server with ACAD_MCP_AUTO_RESTORE=1.)",
    "A janela do AutoCAD está minimizada. Com ela minimizada, comandos ficam presos na fila "
    "e o AutoCAD trava ao ser restaurado. Peça ao usuário para restaurar a janela do AutoCAD "
    "(pode ficar atrás de outras janelas, só não minimizada) e tente de novo. "
    "(Ou inicie o servidor com ACAD_MCP_AUTO_RESTORE=1.)",
)


def restore_behind(hwnd: int) -> None:
    """Restore without activating and push behind all other windows."""
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    time.sleep(1.0)


def require_visible(app: Any) -> int:
    hwnd = int(app.HWND)
    if is_minimized(hwnd):
        if not AUTO_RESTORE:
            raise AcadError(MINIMIZED_MSG)
        restore_behind(hwnd)
    return hwnd


def wait_idle(doc: Any, timeout: float = 10.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if doc.GetVariable("CMDACTIVE") == 0:
                return True
        except pywintypes.com_error:
            pass
        time.sleep(0.1)
    return False


def send(app: Any, doc: Any, text: str, timeout: float) -> dict[str, Any]:
    """Send text to the command line under a watchdog. Returns execution info."""
    hwnd = require_visible(app)
    if doc.GetVariable("CMDACTIVE") != 0:
        raise AcadError(t(
            "A command is already running in AutoCAD ({c}). Use cancel_command or ask the user to finish it.",
            "Já existe um comando em andamento no AutoCAD ({c}). Use cancel_command ou peça ao usuário para terminar.",
            c=doc.GetVariable("CMDNAMES"),
        ))
    started = threading.Event()
    actions: list[str] = []

    def watchdog() -> None:
        started.set()
        actions.extend(cancel_everything(hwnd))

    timer = threading.Timer(timeout, watchdog)
    timer.daemon = True
    t0 = time.monotonic()
    timer.start()
    try:
        doc.SendCommand(text)
    finally:
        timer.cancel()
        if started.is_set():
            timer.join(timeout=3)  # let the watchdog finish before reading results
    wait_idle(doc, 5)
    info: dict[str, Any] = {"seconds": round(time.monotonic() - t0, 2)}
    if started.is_set():
        info["cancelled_after_timeout"] = True
        info["cancel_actions"] = actions
        info["note"] = t(
            "The command kept waiting for input and was cancelled (ESC). Send every answer the command asks for.",
            "O comando ficou esperando entrada e foi cancelado (ESC). Envie todas as respostas do comando.",
        )
    return info


# ------------------------------------------------------------------ commands

_TOKEN = re.compile(r"[\s\n]+")


def normalize_command(cmd: str) -> str:
    cmd = cmd.replace("\r\n", "\n")
    if not cmd.endswith((" ", "\n")):
        cmd += "\n"
    return cmd


def check_command(cmd: str, blocked: set[str]) -> None:
    for tok in _TOKEN.split(cmd):
        name = tok.strip().lstrip("_.-+'").upper()
        if name in blocked:
            raise AcadError(t(
                "The command '{n}' is blocked for safety by this MCP server "
                "(it starts programs, loads code or closes AutoCAD).",
                "O comando '{n}' está bloqueado por segurança neste servidor MCP "
                "(abre programas, carrega código ou fecha o AutoCAD).",
                n=name,
            ))


def check_lisp(code: str, blocked: set[str]) -> None:
    low = code.lower()
    for b in blocked:
        if b in low:
            raise AcadError(t("'{b}' is blocked for safety in run_lisp.",
                              "Uso de '{b}' bloqueado por segurança em run_lisp.", b=b))


def run_command(app: Any, doc: Any, command: str, timeout: float) -> dict[str, Any]:
    cmd = normalize_command(command)
    ms = doc.ModelSpace
    before = ms.Count
    last_before = ms.Item(before - 1).Handle if before else None
    old = {}
    for var in ("FILEDIA", "CMDDIA"):
        try:
            old[var] = doc.GetVariable(var)
            doc.SetVariable(var, 0)
        except pywintypes.com_error:
            pass
    try:
        info = send(app, doc, cmd, timeout)
        try:
            info["last_prompt"] = doc.GetVariable("LASTPROMPT")
        except pywintypes.com_error:
            pass
    finally:
        for var, val in old.items():
            try:
                doc.SetVariable(var, val)
            except pywintypes.com_error:
                pass
    info["modelspace_entities_before"] = before
    info["modelspace_entities_after"] = ms.Count
    if ms.Count:
        try:
            last = ms.Item(ms.Count - 1).Handle
            if last != last_before:
                info["last_entity"] = last
        except pywintypes.com_error:
            pass
    return info


# ------------------------------------------------------------------ LISP


def lisp_str(s: str) -> str:
    """Python string -> AutoLISP string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lisp_path(p: str) -> str:
    return lisp_str(str(p).replace("\\", "/"))


MAX_LINE = 950  # measured: the command line rejects expressions somewhere between 1,000 and 2,000 chars

# Change detector: a database reactor that is alive only while the code runs. It tells apart LISP/commands that
# modified the drawing (one counted undo step) from the ones that only read (measured on AutoCAD 2027).
_TRACK_ON = ("(if #mcp-x (vlr-remove #mcp-x))(defun #mcp-b (r a)(setq #mcp-c T))"
             "(setq #mcp-c nil #mcp-x (vlr-acdb-reactor nil (quote ((:vlr-objectAppended . #mcp-b)"
             "(:vlr-objectModified . #mcp-b)(:vlr-objectErased . #mcp-b)))))")
_TRACK_OFF = "(vlr-remove #mcp-x)(setq #mcp-x nil)"
# Same idea for raw command lines, with its own names so run_lisp does not disturb it.
_CMD_TRACK_ON = ("(progn (vl-load-com)(if #mcp-kx (vlr-remove #mcp-kx))(defun #mcp-kb (r a)(setq #mcp-kc T))"
                 "(setq #mcp-kc nil #mcp-kx (vlr-acdb-reactor nil (quote ((:vlr-objectAppended . #mcp-kb)"
                 "(:vlr-objectModified . #mcp-kb)(:vlr-objectErased . #mcp-kb)))))(princ))\n")
_CMD_TRACK_OFF = "(progn (if #mcp-kx (vlr-remove #mcp-kx)) (setq #mcp-kx nil) (if #mcp-kc 1 0))"


def strip_lisp_comments(code: str) -> str:
    """Remove ; and ;|...|; comments outside strings (AutoLISP `read` does not skip comments)."""
    out: list[str] = []
    i, n, in_str = 0, len(code), False
    while i < n:
        c = code[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif code.startswith(";|", i):
            end = code.find("|;", i + 2)
            i = n if end < 0 else end + 2
            continue
        elif c == ";":
            end = code.find("\n", i)
            i = n if end < 0 else end
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def write_lisp_file(code: str, path: str | None = None) -> str:
    """Write code as ONE (progn ...) form, comments stripped, to be read back by eval_file_expr."""
    path = path or os.path.join(tempfile.gettempdir(), f"acad_mcp_src_{uuid.uuid4().hex}.lsp")
    body = strip_lisp_comments(code).replace("\r\n", "\n")
    with open(path, "w", encoding="mbcs", errors="replace") as fh:
        fh.write("(progn " + body + "\n)")
    return path


def eval_file_expr(path: str) -> str:
    """Short expression that reads a file as TEXT and evaluates it. Reading text is not 'loading code',
    so AutoCAD's SECURELOAD dialog is not triggered (unlike (load ...))."""
    return (
        f"(progn (setq #mcp-sf (open {lisp_path(path)} \"r\") #mcp-s \"\")"
        "(while (setq #mcp-l (read-line #mcp-sf)) (setq #mcp-s (strcat #mcp-s #mcp-l \"\\n\")))"
        "(close #mcp-sf) (setq #mcp-sf nil) (eval (read #mcp-s)))"
    )


def _wrap(expr: str, res_path: str) -> str:
    """The line sent to AutoCAD: run expr under the change detector, write 'C1'/'C0' and the result to a file."""
    return (
        "(progn (vl-load-com)" + _TRACK_ON
        + f"(setq #mcp-r (vl-catch-all-apply (function (lambda () {expr}))))" + _TRACK_OFF
        + f"(setq #mcp-f (open {lisp_path(res_path)} \"w\"))"
        '(write-line (if #mcp-c "C1" "C0") #mcp-f)'
        "(write-line (if (vl-catch-all-error-p #mcp-r)"
        ' (strcat "ERR:" (vl-catch-all-error-message #mcp-r))'
        " (vl-prin1-to-string #mcp-r)) #mcp-f)"
        "(close #mcp-f)(setq #mcp-r nil #mcp-f nil)(princ))\n"
    )


def build_line(code: str, res_path: str) -> tuple[str, str | None]:
    """(line to send, temp source file or None). Short one-line code goes inline; anything long or multi-line
    goes to a temp file that a small bootstrap reads and evaluates (a line break or a ';' comment typed at the
    command line would cut the expression)."""
    clean = strip_lisp_comments(code).strip()
    if "\n" not in clean and "\r" not in clean:
        line = _wrap(f"(progn {clean})", res_path)
        if len(line) <= MAX_LINE:
            return line, None
    src = write_lisp_file(code)
    return _wrap(eval_file_expr(src), res_path), src


def run_lisp(app: Any, doc: Any, code: str, timeout: float) -> dict[str, Any]:
    """Evaluate AutoLISP. Returns result / error and 'changed' (did it modify the drawing database?)."""
    res_path = os.path.join(tempfile.gettempdir(), f"acad_mcp_{uuid.uuid4().hex}.txt")
    line, src_file = build_line(code, res_path)
    try:
        return _run_lisp(app, doc, line, res_path, timeout)
    finally:
        if src_file:
            try:
                os.remove(src_file)
            except OSError:
                pass


def run_lisp_step(app: Any, doc: Any, code: str, timeout: float) -> dict[str, Any]:
    """run_lisp as ONE undo step, counted only when the code changed the drawing."""
    from .util import bump_undo_counter

    doc.StartUndoMark()
    try:
        info = run_lisp(app, doc, code, timeout)
        if info.get("changed"):
            bump_undo_counter(doc)
        return info
    finally:
        try:
            doc.EndUndoMark()
        except pywintypes.com_error:
            pass


def run_command_step(app: Any, doc: Any, command: str, timeout: float) -> dict[str, Any]:
    """run_command as ONE undo step, counted only when the command changed the drawing."""
    from .util import UNDO_MARKER, bump_undo_counter

    if not UNDO_MARKER:
        return run_command(app, doc, command, timeout)
    doc.StartUndoMark()
    try:
        send(app, doc, _CMD_TRACK_ON, min(timeout, 15))
        info = run_command(app, doc, command, timeout)
        flag = run_lisp(app, doc, _CMD_TRACK_OFF, min(timeout, 15))
        info["changed"] = flag.get("result") == "1"
        if info["changed"]:
            bump_undo_counter(doc)
        return info
    finally:
        try:
            doc.EndUndoMark()
        except pywintypes.com_error:
            pass


def _run_lisp(app: Any, doc: Any, line: str, res_path: str, timeout: float) -> dict[str, Any]:
    info = send(app, doc, line, timeout)
    t0 = time.monotonic()
    while not os.path.exists(res_path) and time.monotonic() - t0 < 3:
        time.sleep(0.05)
    if not os.path.exists(res_path):
        info["result"] = None
        info["error"] = t(
            "The code returned nothing (syntax error, unbalanced parentheses or cancelled command). Check the code.",
            "O código não devolveu resultado (erro de sintaxe, parênteses desbalanceados ou comando cancelado). "
            "Confira o código.",
        )
        return info
    try:
        with open(res_path, encoding="mbcs", errors="replace") as f:
            out = f.read().rstrip("\r\n")
    finally:
        try:
            os.remove(res_path)
        except OSError:
            pass
    flag, _, out = out.partition("\n")
    info["changed"] = flag.strip() == "C1"
    out = out.rstrip("\r\n")
    if out.startswith("ERR:"):
        info["error"] = out[4:]
        info["result"] = None
    else:
        info["result"] = out
    return info
