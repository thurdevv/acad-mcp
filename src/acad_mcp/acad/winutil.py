"""AutoCAD windows (Win32): find the drawing area, send ESC, close dialogs.

Measured on AutoCAD 2027:
- ESC sent to the main window does NOT cancel commands; sent to the drawing-area
  window (child of the active MDI document) it does.
- With the window minimized, SendCommand is queued and AutoCAD freezes when
  restored. That is why commands need the window not minimized.
"""

from __future__ import annotations

import ctypes
import time

import win32con
import win32gui
import win32process

WM_MDIGETACTIVE = 0x0229


def make_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def find_acad_main() -> int | None:
    """Janela principal do AutoCAD sem usar COM."""
    found: list[int] = []

    def cb(h: int, _: object) -> None:
        if win32gui.GetClassName(h).startswith("AfxMDIFrame") and "AutoCAD" in win32gui.GetWindowText(h):
            found.append(h)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def is_minimized(main_hwnd: int) -> bool:
    return bool(win32gui.IsIconic(main_hwnd))


def active_view_hwnd(main_hwnd: int) -> int | None:
    """Drawing-area window of the active document."""
    mdiclient = win32gui.FindWindowEx(main_hwnd, 0, "MDIClient", None)
    if not mdiclient:
        return None
    child = win32gui.SendMessage(mdiclient, WM_MDIGETACTIVE, 0, 0)
    if not child:
        return None
    best, best_area = None, -1
    kids: list[int] = []
    win32gui.EnumChildWindows(child, lambda h, _: kids.append(h), None)
    for h in kids:
        if win32gui.GetParent(h) != child or not win32gui.IsWindowVisible(h):
            continue
        l, t, r, b = win32gui.GetWindowRect(h)
        area = (r - l) * (b - t)
        if area > best_area:
            best, best_area = h, area
    return best


def post_escape(hwnd: int, times: int = 2) -> None:
    for _ in range(times):
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0x00010001)
        win32gui.PostMessage(hwnd, win32con.WM_CHAR, 27, 0x00010001)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0xC0010001)
        time.sleep(0.15)


def acad_dialogs(main_hwnd: int) -> list[tuple[int, str]]:
    """Visible dialogs (#32770) of the AutoCAD process."""
    _, pid = win32process.GetWindowThreadProcessId(main_hwnd)
    out: list[tuple[int, str]] = []

    def cb(h: int, _: object) -> None:
        if not win32gui.IsWindowVisible(h) or win32gui.GetClassName(h) != "#32770":
            return
        _, p = win32process.GetWindowThreadProcessId(h)
        if p == pid:
            out.append((h, win32gui.GetWindowText(h)))

    win32gui.EnumWindows(cb, None)
    return out


def close_dialogs(main_hwnd: int) -> list[str]:
    closed = []
    for h, title in acad_dialogs(main_hwnd):
        win32gui.PostMessage(h, win32con.WM_COMMAND, 2, 0)  # IDCANCEL
        closed.append(title or "(untitled)")
    return closed


def cancel_everything(main_hwnd: int) -> list[str]:
    """Close dialogs and send ESC to the drawing area. Safe to call from any thread."""
    notes = []
    closed = close_dialogs(main_hwnd)
    if closed:
        notes.append("dialogs closed: " + ", ".join(closed))
        time.sleep(0.3)
    view = active_view_hwnd(main_hwnd)
    if view:
        post_escape(view)
        notes.append("ESC sent")
    return notes
