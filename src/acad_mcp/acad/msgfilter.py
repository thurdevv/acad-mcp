"""COM message filter (IMessageFilter) built with ctypes.

When AutoCAD is busy it rejects calls (RPC_E_CALL_REJECTED). With this filter
registered on the COM thread, Windows itself retries THAT call until `timeout_s`
instead of failing right away. So a whole tool never needs to be re-run (which
would duplicate saves or drawings — seen during development).

pywin32 does not expose CoRegisterMessageFilter, hence ctypes. It must be
registered on the same (STA) thread that makes the COM calls.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, WINFUNCTYPE, byref, c_long, c_ubyte, c_ulong, c_ushort, c_void_p
from ctypes import wintypes


class GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def _guid(d1: int, d2: int, d3: int, d4: bytes) -> GUID:
    g = GUID(d1, d2, d3)
    for i, b in enumerate(d4):
        g.Data4[i] = b
    return g


IID_IUnknown = _guid(0x00000000, 0x0000, 0x0000, bytes([0xC0, 0, 0, 0, 0, 0, 0, 0x46]))
IID_IMessageFilter = _guid(0x00000016, 0x0000, 0x0000, bytes([0xC0, 0, 0, 0, 0, 0, 0, 0x46]))

S_OK = 0
E_NOINTERFACE = -2147467262  # 0x80004002
SERVERCALL_ISHANDLED = 0
PENDINGMSG_WAITDEFPROCESS = 2
CANCEL_CALL = 0xFFFFFFFF

_QI = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
_AddRef = WINFUNCTYPE(c_ulong, c_void_p)
_Release = WINFUNCTYPE(c_ulong, c_void_p)
_HandleInComingCall = WINFUNCTYPE(wintypes.DWORD, c_void_p, wintypes.DWORD, c_void_p, wintypes.DWORD, c_void_p)
_RetryRejectedCall = WINFUNCTYPE(wintypes.DWORD, c_void_p, c_void_p, wintypes.DWORD, wintypes.DWORD)
_MessagePending = WINFUNCTYPE(wintypes.DWORD, c_void_p, c_void_p, wintypes.DWORD, wintypes.DWORD)


class _Vtbl(Structure):
    _fields_ = [
        ("QueryInterface", _QI),
        ("AddRef", _AddRef),
        ("Release", _Release),
        ("HandleInComingCall", _HandleInComingCall),
        ("RetryRejectedCall", _RetryRejectedCall),
        ("MessagePending", _MessagePending),
    ]


class _Obj(Structure):
    _fields_ = [("lpVtbl", POINTER(_Vtbl))]


def _same(a: GUID, b: GUID) -> bool:
    return bytes(a) == bytes(b)


class MessageFilter:
    """Keep the instance alive for as long as the thread lives."""

    def __init__(self, timeout_s: float = 30.0, retry_ms: int = 200) -> None:
        self.timeout_ms = int(timeout_s * 1000)
        self.retry_ms = retry_ms
        self.rejections = 0  # for diagnostics/tests

        def qi(this, riid, ppv):  # noqa: ANN001
            if _same(riid.contents, IID_IUnknown) or _same(riid.contents, IID_IMessageFilter):
                ppv[0] = this
                return S_OK
            ppv[0] = None
            return E_NOINTERFACE

        def retry(this, htask, tick, reject_type):  # noqa: ANN001
            self.rejections += 1
            if tick < self.timeout_ms:
                return self.retry_ms
            return CANCEL_CALL

        self._cbs = (
            _QI(qi),
            _AddRef(lambda this: 1),
            _Release(lambda this: 1),
            _HandleInComingCall(lambda this, t, h, tick, info: SERVERCALL_ISHANDLED),
            _RetryRejectedCall(retry),
            _MessagePending(lambda this, h, tick, ptype: PENDINGMSG_WAITDEFPROCESS),
        )
        self._vtbl = _Vtbl(*self._cbs)
        self._obj = _Obj(ctypes.pointer(self._vtbl))
        self._old = c_void_p()

    def register(self) -> None:
        hr = ctypes.windll.ole32.CoRegisterMessageFilter(ctypes.cast(ctypes.pointer(self._obj), c_void_p), byref(self._old))
        if hr != S_OK:
            raise OSError(f"CoRegisterMessageFilter falhou: {hr:#x}")

    def unregister(self) -> None:
        ctypes.windll.ole32.CoRegisterMessageFilter(None, byref(c_void_p()))
