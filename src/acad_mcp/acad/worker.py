"""Single thread for every COM call to AutoCAD.

MCP runs sync tools on arbitrary worker threads; COM objects cannot cross threads.
So everything goes through a queue served by ONE thread that called CoInitialize
and holds the AutoCAD reference.

When AutoCAD is busy, the COM message filter (msgfilter.py) retries call by call.
This thread NEVER re-runs a whole tool — that would duplicate saves and drawings
(a bug seen during development).
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, TypeVar

import pythoncom
import pywintypes
import win32com.client

from .. import oplog
from ..i18n import t
from .errors import AcadError
from .msgfilter import MessageFilter

T = TypeVar("T")

RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
_BUSY_CODES = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}
_DEAD_CODES = {
    -2147023174,  # RPC_S_SERVER_UNAVAILABLE
    -2147417848,  # RPC_E_DISCONNECTED
    -2147023170,  # RPC_S_CALL_FAILED
    -2147221021,  # MK_E_UNAVAILABLE
}

BUSY_MESSAGE = t(
    "AutoCAD is busy (a command is running or a dialog is open) and did not answer in time. "
    "Ask the user to finish or cancel (ESC) whatever is open in AutoCAD, or use the cancel_command tool, "
    "then try again. If this was a write tool, check the result before repeating it (it may have run "
    "partially; undo reverts it).",
    "O AutoCAD está ocupado (há um comando em andamento ou uma janela aberta nele) e não respondeu a tempo. "
    "Peça ao usuário para terminar ou cancelar (ESC) o que estiver aberto no AutoCAD, "
    "ou use a ferramenta cancel_command, e tente de novo. Se a ferramenta era de escrita, "
    "confira o resultado antes de repetir (pode ter sido feita em parte; undo desfaz).",
)


def com_message(exc: BaseException) -> str:
    """Readable text of a com_error."""
    if isinstance(exc, pywintypes.com_error):
        hr, msg, excinfo, _ = (list(exc.args) + [None] * 4)[:4]
        if excinfo and len(excinfo) > 2 and excinfo[2]:
            return f"{excinfo[2]} (COM {hr})"
        return f"{msg} (COM {hr})"
    return str(exc)


class ComWorker:
    def __init__(self, busy_timeout: float = 30.0, launch_if_missing: bool = False) -> None:
        self.busy_timeout = busy_timeout
        self.launch_if_missing = launch_if_missing
        self._q: queue.Queue[tuple[Callable[[Any], Any], Future]] = queue.Queue()
        self._app: Any = None
        self.main_hwnd: int | None = None  # guardado para cancelar sem usar COM
        self.msgfilter: MessageFilter | None = None
        self._thread = threading.Thread(target=self._loop, name="acad-com", daemon=True)
        self._thread.start()

    # ---------------------------------------------------------------- thread
    def _loop(self) -> None:
        pythoncom.CoInitialize()
        self.msgfilter = MessageFilter(timeout_s=self.busy_timeout)
        self.msgfilter.register()
        while True:
            fn, fut = self._q.get()
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(self._run(fn))
            except BaseException as exc:  # noqa: BLE001 - devolvido ao chamador
                fut.set_exception(exc)

    def _connect(self) -> Any:
        try:
            app = win32com.client.GetActiveObject("AutoCAD.Application")
        except pywintypes.com_error:
            if not self.launch_if_missing:
                raise AcadError(t(
                    "AutoCAD is not running. Ask the user to open AutoCAD and try again.",
                    "O AutoCAD não está aberto. Peça ao usuário para abrir o AutoCAD e tente de novo.",
                )) from None
            app = win32com.client.Dispatch("AutoCAD.Application")
            app.Visible = True
        try:
            self.main_hwnd = int(app.HWND)
            oplog.set_context(acad=str(app.Version))
        except Exception:  # noqa: BLE001
            pass
        return app

    def _run(self, fn: Callable[[Any], T]) -> T:
        if self._app is None:
            self._app = self._connect()
        else:
            # check the old connection still works (AutoCAD may have been closed and reopened)
            try:
                self._app.Version  # noqa: B018
            except pywintypes.com_error as exc:
                if exc.args[0] in _DEAD_CODES:
                    self._app = self._connect()
        try:
            return fn(self._app)
        except AcadError:
            raise
        except pywintypes.com_error as exc:
            code = exc.args[0]
            if code in _DEAD_CODES:
                self._app = None
                raise AcadError(t("Lost the connection to AutoCAD (was it closed?). Try again.",
                                  "Perdi a conexão com o AutoCAD (ele foi fechado?). Tente de novo.")) from exc
            if code in _BUSY_CODES:
                raise AcadError(BUSY_MESSAGE) from exc
            raise AcadError(com_message(exc)) from exc
        except AttributeError as exc:
            if str(exc).startswith("<unknown>."):
                # o pywin32 transforma 'ocupado' (recusa no GetIDsOfNames) em AttributeError
                raise AcadError(BUSY_MESSAGE + f" ({exc})") from exc
            raise

    # ---------------------------------------------------------------- API
    def call(self, fn: Callable[[Any], T], timeout: float | None = 300.0) -> T:
        """Executa fn(app) na thread COM e devolve o resultado."""
        fut: Future = Future()
        self._q.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            raise AcadError(t("The AutoCAD operation timed out. ", "A operação no AutoCAD passou do tempo limite. ")
                            + BUSY_MESSAGE) from None
