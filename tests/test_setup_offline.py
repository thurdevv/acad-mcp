"""Connecting to Claude Desktop: config editing, entry choice, and server-vs-wizard dispatch (no AutoCAD)."""

import asyncio
import json
import sys
from pathlib import Path

from acad_mcp import server, setup_claude


def test_add_keeps_other_settings_and_backs_up(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"preferences": {"x": 1}, "mcpServers": {"other": {"command": "o"},
                                                                         "autocad": {"command": "old"}}}),
                   encoding="utf-8")
    entry = {"command": "py", "args": ["-m", "acad_mcp"]}
    rep = setup_claude.apply_entry([cfg], entry)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["preferences"] == {"x": 1}
    assert data["mcpServers"]["other"] == {"command": "o"}
    assert data["mcpServers"]["acad"] == entry
    assert "autocad" not in data["mcpServers"]  # old name removed
    assert rep[0].startswith("[ok]") and list(tmp_path.glob("*.bak-*"))


def test_remove_and_dry_run(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": {"acad": {"command": "x"}}}), encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    setup_claude.apply_entry([cfg], {"command": "y"}, dry_run=True)
    assert cfg.read_text(encoding="utf-8") == before  # dry run writes nothing
    setup_claude.apply_entry([cfg], None)
    assert "acad" not in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_bom_and_missing_file(tmp_path):
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {}}).encode())
    setup_claude.apply_entry([bom], {"command": "z"})
    assert bom.read_bytes().startswith(b"\xef\xbb\xbf")
    new = tmp_path / "sub" / "claude_desktop_config.json"
    setup_claude.apply_entry([new], {"command": "z"})
    assert json.loads(new.read_text(encoding="utf-8"))["mcpServers"]["acad"]["command"] == "z"


def test_entry_uses_this_python_or_uvx(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Python313\python.exe")
    e = setup_claude.server_entry("pt")
    assert e == {"command": r"C:\Python313\python.exe", "args": ["-m", "acad_mcp"], "env": {"ACAD_MCP_LANG": "pt"}}
    monkeypatch.setattr(sys, "executable", r"C:\Users\x\AppData\Local\uv\cache\archive-v0\abc\Scripts\python.exe")
    assert setup_claude.server_entry(None)["args"] == ["acad-mcp"]  # throw-away uvx env -> uvx


def test_console_start_opens_wizard_not_server(monkeypatch):
    called = {}

    class TTY:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", TTY())
    monkeypatch.setattr(setup_claude, "run", lambda action="install", **k: called.setdefault("action", action) and 0)
    try:
        server.main([])
    except SystemExit:
        pass
    assert called.get("action") == "install"


def test_mcp_client_through_pipes_still_gets_the_server():
    """What Claude does: start `python -m acad_mcp` with pipes. It must answer as an MCP server."""
    from mcp import Client, StdioServerParameters

    src = str(Path(__file__).resolve().parents[1] / "src")

    async def go():
        p = StdioServerParameters(command=sys.executable, args=["-m", "acad_mcp"], env={"PYTHONPATH": src})
        async with Client(p) as c:
            return len((await c.list_tools()).tools)

    assert asyncio.run(go()) > 50
