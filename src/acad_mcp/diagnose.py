"""Diagnostics: one report a person can send when something does not work.

    py -m acad_mcp diagnostico            -> writes acad-mcp-diagnostico-<PC>-<date>.txt on the Desktop
    py -m acad_mcp diagnostico --teste    -> also runs the compatibility test in AutoCAD (new drawing, not saved)

The report has versions, install/PATH state, AutoCAD found/running, Claude Desktop configuration and the recent
errors from the operation log. It has NO drawing names or contents; home-folder paths are shortened to "~".
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import sys
import time
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

DEPS = ("mcp", "pywin32", "pillow", "opencv-python-headless", "numpy", "ezdxf", "pydantic")
WINDOW = {1: "normal", 2: "minimized", 3: "maximized"}


def _home_short(text: Any) -> str:
    s = str(text)
    home = str(Path.home())
    return s.replace(home, "~").replace(home.replace("\\", "/"), "~") if home else s


def _dep_versions() -> dict[str, str]:
    out = {}
    for d in DEPS:
        try:
            out[d] = version(d)
        except PackageNotFoundError:
            out[d] = "-"
    return out


def _autocad_registry() -> list[dict[str, str]]:
    import winreg

    found = []
    for root_name in (r"SOFTWARE\Autodesk\AutoCAD", r"SOFTWARE\Autodesk\AutoCAD LT"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_name) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    rel = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, rel) as rk:
                        for j in range(winreg.QueryInfoKey(rk)[0]):
                            prod = winreg.EnumKey(rk, j)
                            try:
                                with winreg.OpenKey(rk, prod) as pk:
                                    name = winreg.QueryValueEx(pk, "ProductName")[0]
                            except OSError:
                                continue
                            found.append({"release": rel, "key": prod, "product": str(name),
                                          "lt": root_name.endswith("LT")})
        except OSError:
            continue
    return found


def _autocad_running(worker: Any = None) -> dict[str, Any]:
    from .acad.errors import AcadError
    from .acad.worker import ComWorker

    w = worker or ComWorker(busy_timeout=5)

    def f(app: Any) -> dict[str, Any]:
        d: dict[str, Any] = {"running": True, "version": str(app.Version),
                             "window": WINDOW.get(app.WindowState, app.WindowState),
                             "open_drawings": app.Documents.Count}
        if app.Documents.Count:
            try:
                d["locale"] = str(app.ActiveDocument.GetVariable("LOCALE"))
            except Exception:  # noqa: BLE001
                pass
        return d

    try:
        return w.call(f, timeout=20)
    except AcadError as exc:
        return {"running": False, "detail": _home_short(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"running": None, "detail": _home_short(f"{type(exc).__name__}: {exc}")}


def _claude() -> dict[str, Any]:
    import json

    from . import setup_claude as sc

    configs = []
    for p in sc.config_candidates():
        item: dict[str, Any] = {"path": _home_short(p)}
        try:
            cfg, _ = sc._load(p)
            servers = cfg.get("mcpServers") or {}
            item["servers"] = sorted(servers)
            entry = servers.get(sc.SERVER_NAME)
            if entry:
                cmd = str(entry.get("command", ""))
                item["acad_command"] = _home_short(cmd)
                item["acad_args"] = entry.get("args", [])
                item["command_exists"] = bool(Path(cmd).is_file() or shutil.which(cmd))
                item["lang"] = (entry.get("env") or {}).get("ACAD_MCP_LANG")
        except (OSError, json.JSONDecodeError) as exc:
            item["error"] = _home_short(exc)
        configs.append(item)
    try:
        running = sc.claude_running()
    except Exception:  # noqa: BLE001
        running = None
    return {"running": running, "configs": configs}


def _log_summary(days: int = 14) -> dict[str, Any]:
    from . import oplog

    entries = oplog.read_recent(days=days)
    errors = [e for e in entries if not e.get("ok", True)]
    top = Counter((e.get("tool", "?"), _home_short(e.get("err", ""))[:110]) for e in errors).most_common(10)
    return {
        "folder": _home_short(oplog.log_dir()),
        "enabled": oplog.enabled(),
        "calls": len(entries),
        "errors": len(errors),
        "by_tool": dict(Counter(e.get("tool", "?") for e in entries).most_common(15)),
        "top_errors": [{"tool": k[0], "error": k[1], "count": n} for k, n in top],
        "last_errors": [{"ts": e.get("ts"), "tool": e.get("tool"), "error": _home_short(e.get("err", ""))[:200]}
                        for e in errors[-12:]],
    }


def collect(worker: Any = None, selftest: bool = True) -> dict[str, Any]:
    """selftest=False when called from inside the running server (it obviously loaded)."""
    from . import __version__
    from .selftest import _count

    scripts = Path(sys.executable).parent
    scripts = scripts if scripts.name.lower() == "scripts" else scripts / "Scripts"
    path_dirs = [p.rstrip("\\/").lower() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    data: dict[str, Any] = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pc": socket.gethostname(),
        "acad_mcp": __version__,
        "python": sys.version.split()[0],
        "python_exe": _home_short(sys.executable),
        "windows": platform.platform(),
        "deps": _dep_versions(),
        "scripts_on_path": str(scripts).rstrip("\\/").lower() in path_dirs,
        "in_venv": sys.prefix != sys.base_prefix,
        "acad_mcp_command": _home_short(shutil.which("acad-mcp") or "-"),
        "autocad_installed": _autocad_registry(),
    }
    try:
        from .tools.batch import find_accoreconsole

        data["accoreconsole"] = str(find_accoreconsole())
    except Exception as exc:  # noqa: BLE001
        data["accoreconsole"] = f"- ({_home_short(exc)[:80]})"
    data["autocad"] = _autocad_running(worker)
    data["claude"] = _claude()
    if not selftest:
        data["selftest"] = "ok (asked from inside the running server)"
    else:
        try:
            import asyncio

            tools, prompts = asyncio.run(_count())
            data["selftest"] = f"ok: {tools} tools, {prompts} prompt(s)"
        except Exception as exc:  # noqa: BLE001
            data["selftest"] = f"FAILED: {_home_short(exc)[:200]}"
    data["log"] = _log_summary()
    data["hints"] = hints(data)
    return data


def hints(d: dict[str, Any]) -> list[str]:
    """Plain-language conclusions, most useful first."""
    from .i18n import t

    out = []
    full = [a for a in d["autocad_installed"] if not a["lt"]]
    if not full:
        out.append(t("No full AutoCAD found on this PC (AutoCAD LT does not work).",
                     "Nenhum AutoCAD completo encontrado neste PC (o AutoCAD LT não serve)."))
    ac = d["autocad"]
    if ac.get("running") is False:
        out.append(t("AutoCAD is not running: open it before asking Claude.",
                     "O AutoCAD não está aberto: abra antes de pedir ao Claude."))
    elif ac.get("window") == "minimized":
        out.append(t("The AutoCAD window is minimized: commands, LISP and images need it restored.",
                     "A janela do AutoCAD está minimizada: comandos, LISP e imagens precisam dela restaurada."))
    cfgs = d["claude"]["configs"]
    if not cfgs:
        out.append(t("No Claude Desktop configuration found: is Claude Desktop installed?",
                     "Nenhuma configuração do Claude Desktop encontrada: o Claude Desktop está instalado?"))
    elif not any("acad_command" in c for c in cfgs):
        out.append(t("acad-mcp is NOT connected to Claude. Run: py -m acad_mcp install-claude",
                     "O acad-mcp NÃO está conectado ao Claude. Rode: py -m acad_mcp install-claude"))
    elif any(c.get("command_exists") is False for c in cfgs):
        out.append(t("Claude points to a program that no longer exists. Run: py -m acad_mcp install-claude",
                     "O Claude aponta para um programa que não existe mais. Rode: py -m acad_mcp install-claude"))
    if not d["scripts_on_path"] and not d.get("in_venv"):
        out.append(t("Python's Scripts folder is not on PATH: use 'py -m acad_mcp ...' instead of 'acad-mcp ...'.",
                     "A pasta Scripts do Python não está no PATH: use 'py -m acad_mcp ...' em vez de 'acad-mcp ...'."))
    if str(d["selftest"]).startswith("FAILED"):
        out.append(t("The server does not load: reinstall with  py -m pip install --upgrade --force-reinstall acad-mcp",
                     "O servidor não carrega: reinstale com  py -m pip install --upgrade --force-reinstall acad-mcp"))
    if not out:
        out.append(t("Nothing wrong found in the installation.", "Nada errado encontrado na instalação."))
    return out


def format_report(d: dict[str, Any], compat: list[dict[str, Any]] | None = None) -> str:
    from .i18n import t

    L: list[str] = []
    add = L.append
    add("=" * 70)
    add(t("acad-mcp diagnostic report", "Relatório de diagnóstico do acad-mcp"))
    add(t("No drawing names or contents. Home-folder paths shown as ~.",
          "Sem nomes nem conteúdo de desenhos. Caminhos da pasta do usuário aparecem como ~."))
    add("=" * 70)
    add(f"{t('When', 'Quando')}: {d['when']}    PC: {d['pc']}")
    add("")
    add(t("## Summary", "## Resumo"))
    for h in d["hints"]:
        add(f"  * {h}")
    add("")
    add(t("## Versions", "## Versões"))
    add(f"  acad-mcp {d['acad_mcp']} | Python {d['python']} | {d['windows']}")
    add(f"  python: {d['python_exe']}")
    add("  " + " | ".join(f"{k} {v}" for k, v in d["deps"].items()))
    add(f"  {t('Scripts folder on PATH', 'Pasta Scripts no PATH')}: {d['scripts_on_path']}   acad-mcp: {d['acad_mcp_command']}")
    add(f"  {t('Self-test', 'Autoteste')}: {d['selftest']}")
    add("")
    add("## AutoCAD")
    if not d["autocad_installed"]:
        add(f"  {t('installed', 'instalado')}: -")
    for a in d["autocad_installed"]:
        add(f"  {t('installed', 'instalado')}: {a['product']} ({a['release']} {a['key']}){' [LT]' if a['lt'] else ''}")
    add(f"  accoreconsole: {d['accoreconsole']}")
    ac = d["autocad"]
    if ac.get("running"):
        add(f"  {t('running', 'aberto')}: {ac['version']} | {t('window', 'janela')}: {ac['window']} | "
            f"{t('open drawings', 'desenhos abertos')}: {ac['open_drawings']} | "
            f"{t('Windows locale', 'locale do Windows')}: {ac.get('locale', '-')}")
    else:
        add(f"  {t('running', 'aberto')}: {ac.get('running')} ({ac.get('detail', '')})")
    add("")
    add("## Claude Desktop")
    add(f"  {t('running', 'aberto')}: {d['claude']['running']}")
    for c in d["claude"]["configs"]:
        add(f"  config: {c['path']}")
        if "error" in c:
            add(f"    {t('error', 'erro')}: {c['error']}")
            continue
        add(f"    {t('servers', 'servidores')}: {c.get('servers')}")
        if "acad_command" in c:
            add(f"    acad -> {c['acad_command']} {c.get('acad_args')} | "
                f"{t('exists', 'existe')}: {c['command_exists']} | lang: {c.get('lang')}")
    add("")
    lg = d["log"]
    add(t("## Operation log (last 14 days)", "## Log de operações (últimos 14 dias)"))
    add(f"  {t('folder', 'pasta')}: {lg['folder']} | {t('enabled', 'ligado')}: {lg['enabled']}")
    add(f"  {t('calls', 'chamadas')}: {lg['calls']} | {t('errors', 'erros')}: {lg['errors']}")
    if lg["by_tool"]:
        add("  " + t("most used", "mais usadas") + ": " + ", ".join(f"{k} {v}" for k, v in lg["by_tool"].items()))
    for e in lg["top_errors"]:
        add(f"  {e['count']}x {e['tool']}: {e['error']}")
    if lg["last_errors"]:
        add("  " + t("last errors:", "últimos erros:"))
        for e in lg["last_errors"]:
            add(f"    {e['ts']} {e['tool']}: {e['error']}")
    if compat is not None:
        add("")
        add(t("## Compatibility test (in AutoCAD, on a new drawing, not saved)",
              "## Teste de compatibilidade (no AutoCAD, em desenho novo, sem salvar)"))
        passed = sum(1 for s in compat if s["status"] == "pass")
        add(f"  {passed}/{len(compat)} {t('passed', 'passaram')}")
        for s in compat:
            mark = {"pass": t("PASS", "PASSOU"), "fail": t("FAIL", "FALHOU"), "skip": t("SKIP", "PULADO")}[s["status"]]
            add(f"  [{mark}] {s['step']}" + (f" - {_home_short(s['detail'])[:200]}" if s.get("detail") else ""))
    add("")
    return "\n".join(L)


def desktop_dir() -> Path:
    try:
        from win32com.shell import shell, shellcon

        p = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
        if p.is_dir():
            return p
    except Exception:  # noqa: BLE001
        pass
    p = Path.home() / "Desktop"
    return p if p.is_dir() else Path.cwd()


def write_report(text: str, folder: Path | None = None) -> Path:
    folder = folder or desktop_dir()
    folder.mkdir(parents=True, exist_ok=True)
    base = f"acad-mcp-diagnostico-{socket.gethostname()}-{time.strftime('%Y%m%d-%H%M%S')}"
    path = folder / f"{base}.txt"
    i = 1
    while path.exists():
        path = folder / f"{base}_{i}.txt"
        i += 1
    path.write_text(text, encoding="utf-8-sig")
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="acad-mcp diagnostico",
                                 description="Write a diagnostic report to send when something does not work.")
    ap.add_argument("--teste", "--test", dest="test", action="store_true",
                    help="Also run the compatibility test in AutoCAD (new drawing, closed without saving).")
    ap.add_argument("--out", help="Folder for the report (default: Desktop).")
    ap.add_argument("--print", dest="show", action="store_true", help="Print the report instead of writing a file.")
    a = ap.parse_args(argv)

    import logging

    logging.disable(logging.CRITICAL)
    data = collect()
    compat = None
    if a.test:
        from .compat import run_compat

        print("Running the compatibility test in AutoCAD... / Rodando o teste de compatibilidade no AutoCAD...")
        compat = run_compat(data["autocad"])
    text = format_report(data, compat)
    if a.show:
        print(text)
        return 0
    path = write_report(text, Path(a.out) if a.out else None)
    print(text)
    print(f">>> {path}")
    return 0
