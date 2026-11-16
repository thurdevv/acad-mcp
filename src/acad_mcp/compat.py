"""Compatibility test: drives the real tools against the running AutoCAD on a NEW drawing that is closed
without saving. Meant to be run by anyone (`acad-mcp diagnostico --teste`) to find out whether a given
AutoCAD version / language works, step by step. It never touches drawings that were already open.
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import tempfile
import time
from typing import Any

MIN_DXF = ("0\nSECTION\n2\nENTITIES\n0\nLINE\n8\nCOMPAT\n10\n0\n20\n0\n30\n0\n11\n200\n21\n0\n31\n0\n"
           "0\nENDSEC\n0\nEOF\n")


def run_compat(autocad_state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    st = autocad_state or {}
    if st.get("running") is not True:
        return [{"step": "AutoCAD running", "status": "skip", "detail": "AutoCAD is not running: open it and run again"}]
    if st.get("window") == "minimized":
        return [{"step": "AutoCAD window", "status": "skip",
                 "detail": "the AutoCAD window is minimized: restore it and run again"}]
    return asyncio.run(_run())


async def _run() -> list[dict[str, Any]]:
    from mcp import Client

    from .config import Config
    from .server import build
    from .tools import files

    steps: list[dict[str, Any]] = []
    tmp = tempfile.mkdtemp(prefix="acadmcp_compat_")
    old_received = files.RECEIVED_DIR
    files.RECEIVED_DIR = type(old_received)(tmp) / "received"

    async def call(c: Any, tool: str, /, **args: Any) -> Any:
        r = await c.call_tool(tool, args)
        if r.is_error:
            raise RuntimeError(r.content[0].text)
        sc = r.structured_content
        if sc is None:
            return r.content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc

    async def step(name: str, coro: Any) -> Any:
        t0 = time.monotonic()
        try:
            detail = await coro
            steps.append({"step": name, "status": "pass", "detail": f"{detail} ({time.monotonic() - t0:.1f}s)"})
            return True
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": name, "status": "fail", "detail": f"{type(exc).__name__}: {exc}"})
            return False

    lisp_srv = build(Config(command_timeout=5, fast_threshold=1))
    com_srv = build(Config(command_timeout=5, fast_threshold=10**9))
    doc_name: str | None = None
    try:
        async with Client(lisp_srv) as c, Client(com_srv) as c_com:
            async def s_status():
                s = await call(c, "status")
                return f"AutoCAD {s['autocad_version']}, window {s['window']}"

            async def s_new():
                nonlocal doc_name
                doc_name = (await call(c, "new_drawing"))["created"]["name"]
                return "new drawing"

            if not await step("status", s_status()) or not await step("new_drawing", s_new()):
                return steps

            handles: dict[str, str] = {}

            async def s_draw():
                b = await call(c, "draw_batch", layer="COMPAT", items=[
                    {"type": "rectangle", "corner1": [0, 0], "corner2": [400, 300]},
                    {"type": "circle", "center": [200, 150], "radius": 50},
                    {"type": "text", "text": "Ação çã 01", "insertion": [10, 320], "height": 20},
                ])
                handles["rect"], handles["circle"], handles["text"] = b["handles"]
                return "3 entities"

            async def s_read(client: Any, engine: str):
                q = await call(client, "query_entities", layers=["COMPAT"])
                assert q["engine"] == engine, f"engine {q['engine']}"
                by = {e["handle"]: e for e in q["entities"]}
                assert math.isclose(by[handles["rect"]]["area"], 120000.0), by[handles["rect"]]
                assert by[handles["text"]]["text"] == "Ação çã 01", by[handles["text"]]["text"]
                return f"{q['total']} entities, accents ok, {q['seconds']}s"

            async def s_locale():
                # LOCALE follows Windows, not AutoCAD's own language; the local name of the LINE command does.
                r = await call(c, "run_lisp", code=(
                    '(strcat "Windows locale " (getvar "LOCALE") ", AutoCAD " (getvar "ACADVER")'
                    ' ", LINE command is called " (vl-princ-to-string (getcname "_LINE")))'))
                assert not r.get("error"), r
                return str(r["result"]).strip('"')

            async def s_long_lisp():
                code = "\n".join(f"(setq v{i} {i})" for i in range(400)) + "\n(+ v1 v399)"
                r = await call(c, "run_lisp", code=code)
                assert r["result"] == "400", r
                return "long code through a file"

            async def s_command():
                r = await call(c, "run_command", command="_.CIRCLE 1000,1000 25")
                assert r["modelspace_entities_after"] == r["modelspace_entities_before"] + 1, r
                return "CIRCLE by command line"

            async def s_watchdog():
                r = await call(c, "run_command", command="_.LINE 0,0\n", timeout_s=3)
                assert r.get("cancelled_after_timeout") is True, r
                s = await call(c, "status")
                assert s.get("command_in_progress") is None, s
                return "pending command cancelled with ESC"

            async def s_hatch():
                h = await call(c, "draw_hatch", boundary_handles=[handles["circle"]], pattern="ANSI31", scale=5)
                assert math.isclose(h["area"], math.pi * 2500, rel_tol=1e-6), h
                return "hatch area ok"

            async def s_undo():
                n0 = (await call(c, "query_entities", limit=1))["total"]
                await call(c, "draw_batch", items=[{"type": "line", "start": [0, 0], "end": [9, 9]}] * 3)
                await call(c, "quantities")
                u = await call(c, "undo", steps=1)
                n1 = (await call(c, "query_entities", limit=1))["total"]
                assert n1 == n0, f"expected {n0} entities after undo, got {n1}: {u}"
                assert u["undone_steps"] == 1, u
                return f"1 undo step removed the batch ({u['u_commands_sent']} U sent, {u['pending_steps']} left)"

            async def s_image():
                img = await call(c, "get_view_image", zoom="extents", grid=True)
                png = next(x for x in img if x.type == "image")
                assert len(png.data) > 1000
                return f"png {len(png.data) * 3 // 4 // 1024} KB"

            async def s_import():
                r = await call(c, "import_file", filename="compat.dxf", content_text=MIN_DXF, insertion=[2000, 0])
                assert r["inserted_entities"] == 1, r
                return "DXF validated, converted and inserted"

            async def s_console():
                folder = os.path.join(tmp, "dwgs")
                os.makedirs(folder)
                sv = await call(c, "save_drawing", path=os.path.join(folder, "compat.dwg"))
                nonlocal doc_name
                doc_name = os.path.basename(sv["saved_as"])
                job = await call(c, "batch_process", folder=folder, routine="count_blocks",
                                 output_folder=os.path.join(tmp, "out"))
                for _ in range(120):
                    s = await call(c, "batch_status", job_id=job["job_id"])
                    if s["status"] != "running":
                        break
                    await asyncio.sleep(1)
                assert s["status"] == "done" and s["ok"] == 1, s
                return f"accoreconsole processed 1 file in {s['seconds']}s"

            for name, coro in [
                ("draw (COM)", s_draw()),
                ("read (in-process LISP)", s_read(c, "lisp")),
                ("read (COM)", s_read(c_com, "com")),
                ("language / version", s_locale()),
                ("long LISP", s_long_lisp()),
                ("command line", s_command()),
                ("command watchdog (ESC)", s_watchdog()),
                ("hatch", s_hatch()),
                ("undo", s_undo()),
                ("screenshot with grid", s_image()),
                ("import DXF", s_import()),
                ("batch with accoreconsole", s_console()),
            ]:
                await step(name, coro)

            async def s_close():
                docs = (await call(c, "list_documents"))["documents"]
                closed = 0
                for d in docs:
                    if d["name"] == doc_name or (d["path"] or "").lower().startswith(tmp.lower()):
                        await call(c, "close_drawing", name=d["name"], discard_changes=True)
                        closed += 1
                assert closed >= 1, "test drawing not found to close"
                return f"closed {closed} test drawing(s) without saving"

            await step("close test drawing", s_close())
    finally:
        files.RECEIVED_DIR = old_received
        shutil.rmtree(tmp, ignore_errors=True)
    return steps
