"""Operation log: what is written, what is cut, rotation, switches (no AutoCAD)."""

import asyncio
import json

import pytest

from acad_mcp import oplog


@pytest.fixture()
def logdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ACAD_MCP_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACAD_MCP_LOG", raising=False)
    monkeypatch.delenv("ACAD_MCP_LOG_ARGS", raising=False)
    return tmp_path


def lines(folder):
    out = []
    for f in sorted(folder.glob("acad-mcp-*.jsonl")):
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    return out


def test_summary_keeps_small_values_and_cuts_content():
    s = oplog.summarize
    assert s([0.0, 1.23456789]) == [0.0, 1.2346]
    assert s("WALL") == "WALL"
    assert s("x" * 500) == "<str 500 chars>"
    assert s("(setq a 1)", "code") == "<10 chars>"
    assert s("QUJD" * 1000, "content_base64") == "<4000 chars>"
    assert s([[0, 0], [1, 1], [2, 2], [3, 3]]) == {"len": 4, "first": [[0, 0], [1, 1], [2, 2]]}
    big = s({"items": [{"type": "text", "text": "y" * 200}] * 50})
    assert big["items"]["len"] == 50 and "y" * 61 not in json.dumps(big)


def test_record_ok_error_and_engine(logdir):
    oplog.record("draw_line", {"start": [0, 0], "end": [10, 0], "layer": None}, 12.4, result={"handle": "2A"})
    oplog.record("query_entities", {"limit": 5}, 80, result={"engine": "lisp", "entities": []})
    oplog.record("import_file", {"content_text": "secret drawing data " * 50}, 3, error="Invalid   DXF\n" + "z" * 900)
    a, b, c = lines(logdir)
    assert a["tool"] == "draw_line" and a["ok"] and a["ms"] == 12 and a["args"] == {"start": [0, 0], "end": [10, 0]}
    assert b["engine"] == "lisp"
    assert not c["ok"] and len(c["err"]) <= oplog.MAX_ERROR and c["err"].startswith("Invalid DXF z")
    assert "secret" not in json.dumps(c)
    assert all(k in a for k in ("ts", "v", "host", "user"))


def test_modes_and_switch(logdir, monkeypatch):
    monkeypatch.setenv("ACAD_MCP_LOG_ARGS", "none")
    oplog.record("t", {"text": "abc"}, 1)
    monkeypatch.setenv("ACAD_MCP_LOG_ARGS", "full")
    oplog.record("t", {"text": "x" * 300}, 1)
    monkeypatch.setenv("ACAD_MCP_LOG", "0")
    oplog.record("t", {"text": "never"}, 1)
    got = lines(logdir)
    assert len(got) == 2 and "args" not in got[0] and got[1]["args"]["text"] == "x" * 300


def test_rotation_keeps_a_few_files(logdir, monkeypatch):
    monkeypatch.setattr(oplog, "MAX_BYTES", 400)
    for i in range(40):
        oplog.record("t", {"i": i}, 1)
    files = sorted(p.name for p in logdir.iterdir())
    assert len(files) <= oplog.KEEP and any(n.endswith(".1") for n in files)
    assert oplog.read_recent()[-1]["args"] == {"i": 39}


def test_logging_failure_never_breaks_the_tool(monkeypatch):
    monkeypatch.setenv("ACAD_MCP_LOG_DIR", "Z:\\does\\not\\exist\\<>|")
    oplog.record("t", {"a": 1}, 1)  # must not raise


def test_every_tool_call_is_logged_through_the_server(logdir):
    from mcp import Client

    from acad_mcp.config import Config
    from acad_mcp.server import build

    async def go():
        async with Client(build(Config())) as c:
            await c.call_tool("list_standards", {})
            r = await c.call_tool("import_file", {"filename": "bad.dxf", "content_text": "garbage", "action": "save_only"})
            assert r.is_error

    asyncio.run(go())
    got = lines(logdir)
    assert [x["tool"] for x in got] == ["list_standards", "import_file"]
    assert got[0]["ok"] and not got[1]["ok"] and got[1]["args"]["content_text"] == "<7 chars>"
