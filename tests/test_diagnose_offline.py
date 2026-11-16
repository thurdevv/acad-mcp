"""Diagnostic report: conclusions, privacy and the command (no AutoCAD needed)."""

import subprocess
import sys
from pathlib import Path

from acad_mcp import diagnose, oplog

SRC = str(Path(__file__).resolve().parents[1] / "src")


def fake(**over):
    d = {
        "when": "2026-01-01 10:00:00", "pc": "PC1", "acad_mcp": "9.9.9", "python": "3.12.0",
        "python_exe": "~\\py\\python.exe", "windows": "Windows-11", "deps": {"mcp": "2.3.0"},
        "scripts_on_path": True, "in_venv": False, "acad_mcp_command": "-",
        "autocad_installed": [{"release": "R26.0", "key": "ACAD-A101:409", "product": "AutoCAD 2027 - English", "lt": False}],
        "accoreconsole": "C:\\x\\accoreconsole.exe",
        "autocad": {"running": True, "version": "26.0s", "window": "maximized", "open_drawings": 1, "locale": "ENU"},
        "claude": {"running": True, "configs": [{"path": "~\\c.json", "servers": ["acad"], "acad_command": "py",
                                                 "acad_args": ["-m", "acad_mcp"], "command_exists": True, "lang": "pt"}]},
        "selftest": "ok: 62 tools, 1 prompt(s)",
        "log": {"folder": "~\\logs", "enabled": True, "calls": 0, "errors": 0, "by_tool": {}, "top_errors": [],
                "last_errors": []},
    }
    d.update(over)
    d["hints"] = diagnose.hints(d)
    return d


def test_healthy_install_says_so():
    assert len(fake()["hints"]) == 1 and "othing wrong" in fake()["hints"][0]


def test_conclusions_for_common_problems():
    def h(**over):
        return " | ".join(fake(**over)["hints"])

    assert "LT" in h(autocad_installed=[{"release": "R", "key": "k", "product": "AutoCAD LT 2027", "lt": True}])
    assert "not running" in h(autocad={"running": False, "detail": "x"})
    assert "minimized" in h(autocad={"running": True, "version": "26", "window": "minimized", "open_drawings": 0})
    assert "install-claude" in h(claude={"running": False, "configs": [{"path": "p", "servers": []}]})
    assert "no longer exists" in h(claude={"running": False, "configs": [
        {"path": "p", "servers": ["acad"], "acad_command": "gone.exe", "command_exists": False}]})
    assert "py -m acad_mcp" in h(scripts_on_path=False)
    assert "py -m acad_mcp" not in h(scripts_on_path=False, in_venv=True)
    assert "reinstall" in h(selftest="FAILED: boom")


def test_report_text_has_sections_and_compat_results():
    text = diagnose.format_report(fake(), compat=[{"step": "draw", "status": "pass", "detail": "ok"},
                                                  {"step": "undo", "status": "fail", "detail": "boom"}])
    for part in ("## AutoCAD", "## Claude Desktop", "AutoCAD 2027 - English", "1/2", "[PASS] draw", "[FAIL] undo - boom"):
        assert part in text


def test_log_summary_counts_errors_and_hides_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ACAD_MCP_LOG_DIR", str(tmp_path))
    home = str(Path.home())
    oplog.record("draw_line", {"start": [0, 0]}, 5, result={})
    oplog.record("open_drawing", {"path": "x"}, 5, error=f"File not found: {home}\\Secret Project\\a.dwg")
    oplog.record("open_drawing", {"path": "x"}, 5, error=f"File not found: {home}\\Secret Project\\a.dwg")
    s = diagnose._log_summary()
    assert s["calls"] == 3 and s["errors"] == 2 and s["top_errors"][0]["count"] == 2
    assert home not in str(s) and "~" in s["top_errors"][0]["error"]


def test_write_report_never_overwrites(tmp_path):
    a = diagnose.write_report("one", tmp_path)
    b = diagnose.write_report("two", tmp_path)
    assert a != b and a.read_text(encoding="utf-8-sig") == "one"


def test_command_runs_and_prints_a_report(tmp_path):
    env = {**__import__("os").environ, "PYTHONPATH": SRC, "ACAD_MCP_LANG": "en"}
    r = subprocess.run([sys.executable, "-m", "acad_mcp", "diagnose", "--out", str(tmp_path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert "acad-mcp diagnostic report" in r.stdout and "## Versions" in r.stdout
    assert len(list(tmp_path.glob("acad-mcp-diagnostico-*.txt"))) == 1
