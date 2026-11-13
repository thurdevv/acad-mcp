"""Connect / disconnect acad-mcp to Claude Desktop (Windows).

Used by:
- double-clicking acad-mcp.exe (it notices it was opened by a person, not by Claude),
- `acad-mcp install-claude` / `acad-mcp uninstall-claude`,
- INSTALAR.bat.

Claude Desktop rewrites its config from memory when it quits, so the config is only written with
Claude closed (it is closed after asking, and reopened at the end).
"""

from __future__ import annotations

import ctypes
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SERVER_NAME = "acad"
OLD_NAMES = ("autocad",)  # entries from earlier versions


# ------------------------------------------------------------------ helpers (no user interaction)


def ui_is_portuguese() -> bool:
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x16  # LANG_PORTUGUESE
    except Exception:  # noqa: BLE001
        return False


def config_candidates() -> list[Path]:
    """Every place Claude Desktop keeps claude_desktop_config.json on Windows (classic and Store/MSIX)."""
    local, roaming = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
    pats = [
        os.path.join(roaming, "Claude", "claude_desktop_config.json"),
        os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"),
        os.path.join(local, "Claude-Data", "*", "claude_desktop_config.json"),
    ]
    found: list[Path] = []
    for p in pats:
        found += [Path(x) for x in glob.glob(p)]
    return found


def default_config() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude" / "claude_desktop_config.json"


def claude_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"], capture_output=True, text=True).stdout
    return "claude.exe" in out.lower()


def close_claude(timeout: float = 15) -> bool:
    subprocess.run(["taskkill", "/IM", "claude.exe", "/T"], capture_output=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not claude_running():
            return True
        time.sleep(1)
    subprocess.run(["taskkill", "/IM", "claude.exe", "/T", "/F"], capture_output=True)
    time.sleep(2)
    return not claude_running()


def claude_app_id() -> str | None:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-StartApps | Where-Object Name -eq 'Claude' | Select-Object -First 1).AppID"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def reopen_claude() -> bool:
    app = claude_app_id()
    if not app:
        return False
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app}"])
    return True


def autocad_installed() -> bool:
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "AutoCAD.Application"))
        return True
    except OSError:
        return False


def server_entry(lang: str | None) -> dict:
    """How Claude should start this server: the Python running now, or uvx if this is a throw-away uvx env."""
    exe = Path(sys.executable)
    low = str(exe).lower()
    entry: dict
    if "\\uv\\cache\\" in low or "\\archive-v" in low:
        uvx = shutil.which("uvx")
        entry = {"command": uvx or "uvx", "args": ["acad-mcp"]}
    else:
        entry = {"command": str(exe), "args": ["-m", "acad_mcp"]}
    if lang:
        entry["env"] = {"ACAD_MCP_LANG": lang}
    return entry


def _load(path: Path) -> tuple[dict, bool]:
    raw = path.read_bytes() if path.exists() else b"{}"
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig").strip() or "{}"
    return json.loads(text), bom


def _save(path: Path, cfg: dict, bom: bool) -> Path | None:
    backup = None
    if path.exists():
        backup = path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    return backup


def apply_entry(paths: list[Path], entry: dict | None, dry_run: bool = False) -> list[str]:
    """Add (entry) or remove (entry=None) the server in each config. Other keys are kept. Returns report lines."""
    report = []
    for p in paths:
        cfg, bom = _load(p)
        servers = cfg.setdefault("mcpServers", {})
        for old in OLD_NAMES:
            servers.pop(old, None)
        if entry is None:
            servers.pop(SERVER_NAME, None)
        else:
            servers[SERVER_NAME] = entry
        if dry_run:
            report.append(f"[dry-run] {p}: {SERVER_NAME} = {json.dumps(servers.get(SERVER_NAME))}")
            continue
        backup = _save(p, cfg, bom)
        check, _ = _load(p)
        present = SERVER_NAME in check.get("mcpServers", {})
        ok = present if entry is not None else not present
        report.append(f"[{'ok' if ok else 'x'}] {p}" + (f"  (backup: {backup.name})" if backup else ""))
    return report


def selftest() -> tuple[bool, str]:
    import asyncio
    import logging

    from .selftest import _count

    logging.disable(logging.CRITICAL)
    try:
        tools, prompts = asyncio.run(_count())
        return tools > 0, f"{tools} tools, {prompts} prompt(s)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        logging.disable(logging.NOTSET)


# ------------------------------------------------------------------ interactive flow


def _ask(prompt: str, default: str, assume_yes: bool) -> str:
    if assume_yes or not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def _pause(interactive: bool) -> None:
    if interactive and sys.stdin and sys.stdin.isatty():
        try:
            input("\nEnter para fechar / Press Enter to close...")
        except EOFError:
            pass


def run(action: str = "install", lang: str | None = None, assume_yes: bool = False, dry_run: bool = False,
        reopen: bool = True, interactive: bool = True) -> int:
    print("=" * 60)
    if action == "install":
        print("  acad-mcp  -  conectar ao Claude Desktop / connect to Claude Desktop")
    else:
        print("  acad-mcp  -  desconectar do Claude Desktop / disconnect from Claude Desktop")
    print("=" * 60)

    if action == "install":
        ok, info = selftest()
        if not ok:
            print(f"[x] O servidor nao carregou / The server failed to load: {info}")
            _pause(interactive)
            return 1
        print(f"[ok] servidor / server: {info}")
        if autocad_installed():
            print("[ok] AutoCAD encontrado / found")
        else:
            print("[!] AutoCAD completo nao encontrado (o LT nao serve). / Full AutoCAD not found (LT does not work).")
        if lang is None:
            d = "pt" if ui_is_portuguese() else "en"
            lang = _ask(f"Idioma / Language [pt/en] (Enter = {d}): ", d, assume_yes).lower()
            lang = "pt" if lang.startswith("p") else "en"
        print(f"[ok] idioma / language: {lang}")

    paths = config_candidates() or [default_config()]

    was_running = claude_running()
    if was_running and not dry_run:
        ans = _ask("O Claude esta aberto e precisa fechar (ele reabre no final). Fechar agora? [S/n]\n"
                   "Claude must close (it reopens at the end). Close now? [Y/n]: ", "s", assume_yes)
        if ans.lower().startswith("n"):
            print("Cancelado. / Cancelled.")
            _pause(interactive)
            return 2
        if not close_claude():
            print("[x] Nao consegui fechar o Claude. Feche pelo icone perto do relogio > Sair, e tente de novo.")
            print("[x] Could not close Claude. Quit it from the tray icon and try again.")
            _pause(interactive)
            return 3
        print("[ok] Claude fechado / closed")

    entry = server_entry(lang) if action == "install" else None
    for line in apply_entry(paths, entry, dry_run):
        print(line)

    if reopen and not dry_run and (was_running or action == "install"):
        print("[ok] Claude reaberto / reopened" if reopen_claude()
              else "[!] Abra o Claude pelo menu Iniciar / Open Claude from the Start menu")

    print()
    if dry_run:
        print("(ensaio: nada foi alterado / rehearsal: nothing was changed)")
    elif action == "install":
        print("PRONTO! Abra o AutoCAD, comece uma conversa NOVA no Claude e peca: \"Use o MCP do AutoCAD: status\".")
        print("DONE! Open AutoCAD, start a NEW chat in Claude and ask: \"Use the AutoCAD MCP: status\".")
    else:
        print("Desconectado. / Disconnected.")
    _pause(interactive)
    return 0
