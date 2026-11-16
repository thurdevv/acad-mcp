"""Tests that do not need AutoCAD: conversions, safety guards, tool registration, languages."""

import asyncio

import pytest

from acad_mcp.acad.commands import check_command, check_lisp, normalize_command
from acad_mcp.acad.errors import AcadError
from acad_mcp.acad.util import dxf_types, parse_color, parse_lineweight
from acad_mcp.config import DEFAULT_BLOCKED_COMMANDS, DEFAULT_BLOCKED_LISP, Config
from acad_mcp.server import build


def test_parse_color():
    assert parse_color(1) == ("aci", 1)
    assert parse_color("vermelho") == ("aci", 1)
    assert parse_color("ByLayer") == ("aci", 256)
    assert parse_color("#FF8000") == ("rgb", (255, 128, 0))
    assert parse_color("10, 20, 30") == ("rgb", (10, 20, 30))
    with pytest.raises(AcadError):
        parse_color("roxo-neon")
    with pytest.raises(AcadError):
        parse_color(300)


def test_parse_lineweight():
    assert parse_lineweight(0.35) == 35
    assert parse_lineweight("0,5mm") == 50
    assert parse_lineweight(35) == 35
    assert parse_lineweight("bylayer") == -1
    with pytest.raises(AcadError):
        parse_lineweight(0.33)


def test_dxf_types():
    assert dxf_types(["line", "polilinha", "BLOCK"]) == "LINE,LWPOLYLINE,POLYLINE,INSERT"
    assert dxf_types(None) is None


def test_blocked_commands():
    blocked = set(DEFAULT_BLOCKED_COMMANDS)
    for bad in ["_.SHELL dir\n", "SH\n", "_.-NETLOAD x.dll\n", "_QUIT _Y\n", "'_.SCRIPT a.scr\n"]:
        with pytest.raises(AcadError):
            check_command(bad, blocked)
    check_command("_.CIRCLE 0,0 10\n", blocked)
    check_command("_.-LAYER _M PAREDE\n\n", blocked)


def test_blocked_lisp():
    blocked = set(DEFAULT_BLOCKED_LISP)
    with pytest.raises(AcadError):
        check_lisp('(startapp "cmd.exe")', blocked)
    with pytest.raises(AcadError):
        check_lisp('(vl-file-delete "c:/x")', blocked)
    check_lisp('(sslength (ssget "X"))', blocked)


def test_normalize_command():
    assert normalize_command("_.ZOOM _E") == "_.ZOOM _E\n"
    assert normalize_command("_.REGEN\n") == "_.REGEN\n"


def _tool_names(cfg: Config) -> set[str]:
    m = build(cfg)

    async def go():
        from mcp import Client

        async with Client(m) as c:
            return {t.name for t in (await c.list_tools()).tools}

    return asyncio.run(go())


def test_read_only_mode_hides_write_tools():
    full = _tool_names(Config())
    ro = _tool_names(Config(read_only=True))
    assert {"draw_line", "erase_entities", "run_lisp", "save_drawing"} <= full
    assert ro < full
    assert "draw_line" not in ro and "run_lisp" not in ro and "erase_entities" not in ro
    assert {"status", "query_entities", "quantities", "get_view_image", "check_standards"} <= ro


def test_disable_lisp_and_commands():
    names = _tool_names(Config(allow_lisp=False, allow_commands=False))
    assert "run_lisp" not in names and "run_command" not in names
    assert "draw_line" in names


def _descriptions(lang: str) -> dict:
    """Tool descriptions as seen by a client, with the language set before import (fresh process)."""
    import json
    import subprocess
    import sys

    code = (
        "import asyncio, json\n"
        "from acad_mcp.server import build\n"
        "from acad_mcp.config import Config\n"
        "from mcp import Client\n"
        "async def go():\n"
        "    async with Client(build(Config())) as c:\n"
        "        ts = (await c.list_tools()).tools\n"
        "        return {t.name: {'d': t.description, 'p': t.input_schema['properties']} for t in ts}\n"
        "print(json.dumps(asyncio.run(go())))\n"
    )
    from pathlib import Path

    env = {**__import__("os").environ, "ACAD_MCP_LANG": lang,
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_english_default_and_portuguese_option():
    en, pt = _descriptions("en"), _descriptions("pt")
    assert en["draw_line"]["d"].startswith("Draws a straight line")
    assert pt["draw_line"]["d"].startswith("Desenha uma linha")
    assert "Point [x, y]" in en["draw_line"]["p"]["start"]["description"]
    assert "Ponto [x, y]" in pt["draw_line"]["p"]["start"]["description"]
    assert en.keys() == pt.keys()
    missing_pt = [n for n, v in pt.items() if v["d"] == en[n]["d"]]
    assert not missing_pt, f"tools without a Portuguese description: {missing_pt}"


def test_long_lisp_goes_through_file_and_comments_are_stripped():
    import os

    from acad_mcp.acad.commands import MAX_LINE, build_line, strip_lisp_comments

    assert strip_lisp_comments('(a "x;y") ; c\n(b) ;| blk |; (c)') == '(a "x;y") \n(b)  (c)'
    res = os.path.join(os.environ.get("TEMP", "."), "r.txt")

    line, src = build_line("(+ 1 2) ; trailing comment", res)  # short, one line: inline, comment removed
    assert src is None and "(progn (+ 1 2))" in line and "trailing" not in line and len(line) <= MAX_LINE
    assert "vlr-acdb-reactor" in line and '"C1"' in line  # runs under the change detector

    for code in ("(setq a 1)" * 120, "(setq a 1)\n(setq b 2)"):  # long, or multi-line: through a file
        line, src = build_line(code, res)
        try:
            assert src and len(line) <= MAX_LINE and "read-line" in line
        finally:
            os.remove(src)
