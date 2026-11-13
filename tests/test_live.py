"""End-to-end tests against a REAL running AutoCAD (Windows).

Each test creates new test drawings, drives the tools through the MCP protocol (in-memory client) and
closes what it opened without saving. It never touches the user's drawings.
Skip with:  pytest -m "not live"
Needs the AutoCAD window open and NOT minimized.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import os
import tempfile

import pytest

pytestmark = pytest.mark.live


def _acad_state() -> str:
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        app = win32com.client.GetActiveObject("AutoCAD.Application")
        return "minimized" if app.WindowState == 2 else "ok"
    except Exception:  # noqa: BLE001
        return "missing"


_STATE = _acad_state()
if _STATE == "missing":
    pytest.skip("AutoCAD is not running", allow_module_level=True)
if _STATE == "minimized":
    pytest.skip("AutoCAD window is minimized: restore it to run the live tests", allow_module_level=True)


class Session:
    def __init__(self, client):
        self.c = client

    async def __call__(self, tool: str, /, **args):
        r = await self.c.call_tool(tool, args)
        if r.is_error:
            raise AssertionError(f"{tool}({args}) -> ERROR: {r.content[0].text}")
        if r.structured_content is not None:
            sc = r.structured_content
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        return r.content

    async def expect_error(self, tool: str, contains: str = "", /, **args) -> str:
        r = await self.c.call_tool(tool, args)
        assert r.is_error, f"{tool} should have failed"
        text = r.content[0].text
        assert contains.lower() in text.lower(), text
        return text


def run_session(cfg, body, tmpdir: str | None = None):
    """Open an MCP session on a fresh drawing, run body(call, tmpdir), close what was opened."""
    from mcp import Client

    from acad_mcp.server import build

    server = build(cfg)
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="acadmcp_")

    async def go():
        async with Client(server) as client:
            call = Session(client)
            test_doc = (await call("new_drawing"))["created"]["name"]
            try:
                await body(call, tmpdir)
            finally:
                for d in (await call("list_documents"))["documents"]:  # the name changes after save_drawing
                    if d["name"] == test_doc or (d["path"] or "").lower().startswith(tmpdir.lower()):
                        await call("close_drawing", name=d["name"], discard_changes=True)

    asyncio.run(go())


@pytest.mark.parametrize("engine", ["lisp", "com"])
def test_live_end_to_end(engine):
    from acad_mcp.config import Config

    cfg = Config(command_timeout=4, fast_threshold=1 if engine == "lisp" else 10**9)
    run_session(cfg, lambda call, tmp: run_flow(call, tmp, engine))


async def run_flow(call: Session, tmpdir: str, engine: str) -> None:
    st = await call("status")
    assert st["autocad_version"].startswith("26")

    # ---------- layers and styles
    r = await call("manage_layer", name="MCP_WALL", color=1, lineweight=0.35, make_current=False)
    assert r["created"] and r["layer"]["lineweight"] == 35
    r = await call("manage_layer", name="MCP_AXIS", color="#00FF00", linetype="CENTER")
    assert r["layer"]["linetype"].upper() == "CENTER" and r["layer"]["color"] == "0,255,0"
    await call("manage_text_style", name="MCP_TXT", font_file="romans.shx", width_factor=0.8)

    # ---------- batch drawing
    b = await call("draw_batch", layer="MCP_WALL", items=[
        {"type": "rectangle", "corner1": [0, 0], "corner2": [400, 300]},
        {"type": "circle", "center": [200, 150], "radius": 50},
        {"type": "line", "start": [0, 150], "end": [400, 150], "layer": "MCP_AXIS"},
        {"type": "text", "text": "ROOM", "insertion": [200, 260], "height": 20, "justify": "center"},
        {"type": "polyline", "points": [[500, 0], [600, 0], [600, 100]], "closed": False},
    ])
    assert b["created"] == 5
    rect, circ, line, text, pl = b["handles"]

    e = await call("get_entity", handle=rect)
    assert e["type"] == "Polyline" and e["closed"] and math.isclose(e["area"], 120000.0)
    e = await call("get_entity", handle=line)
    assert e["layer"] == "MCP_AXIS" and math.isclose(e["length"], 400.0)

    q0 = await call("query_entities", limit=1)
    assert q0["engine"] == engine
    await call.expect_error("draw_batch", "item 1", items=[
        {"type": "line", "start": [0, 0], "end": [1, 1]},
        {"type": "circle", "center": [0, 0]},
    ])
    assert (await call("query_entities", limit=1))["total"] == q0["total"]  # failed batch leaves nothing

    # ---------- single drawing tools
    d = await call("draw_dimension", kind="aligned", p1=[0, 0], p2=[400, 0], text_position=[200, -40])
    assert math.isclose(d["measurement"], 400.0)
    d = await call("draw_dimension", kind="radius", handle=circ)
    assert math.isclose(d["measurement"], 50.0)
    d = await call("draw_dimension", kind="vertical", p1=[400, 0], p2=[400, 300], text_position=[450, 150])
    assert math.isclose(d["measurement"], 300.0)
    h = await call("draw_hatch", boundary_handles=[circ], pattern="ANSI31", scale=5)
    assert math.isclose(h["area"], math.pi * 2500, rel_tol=1e-6)
    tb = await call("draw_table", insertion=[700, 300], title="MATERIALS",
                    rows=[["ITEM", "QTY"], ["Pipe", "10"], ["Elbow", "4"]], column_widths=[60, 30])
    assert tb["rows"] == 4 and tb["columns"] == 2
    assert (await call("draw_leader", points=[[200, 150], [300, 350]], text="AXIS"))["type"] == "MLeader"
    a = await call("draw_arc", center=[0, 0], radius=100, start_angle_deg=0, end_angle_deg=90)
    assert math.isclose(a["length"], math.pi * 50, rel_tol=1e-6)

    # ---------- blocks with attributes (built with LISP, which also tests run_lisp)
    lisp = (
        '(setq blk (vla-Add (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))) '
        '(vlax-3d-point 0 0 0) "MCP_EQUIP"))'
        '(vla-AddCircle blk (vlax-3d-point 0 0 0) 10.0)'
        '(vla-AddAttribute blk 5.0 0 "TAG?" (vlax-3d-point 12 0 0) "TAG" "X")'
        '(vla-AddAttribute blk 3.0 0 "DESC?" (vlax-3d-point 12 -6 0) "DESC" "-")'
        '(vla-get-Count blk)'
    )
    r = await call("run_lisp", code=lisp)
    assert r.get("error") is None and r["result"] == "3", r
    ins = []
    for i, tag in enumerate(["P-101", "P-102", "P-103"]):
        bi = await call("insert_block", name="MCP_EQUIP", insertion=[100 * i, -200],
                        attributes={"TAG": tag, "DESC": "Pump"}, layer="MCP_EQUIP")
        assert bi["attributes"]["TAG"] == tag
        ins.append(bi["handle"])
    sa = await call("set_block_attributes", handle=ins[0], values={"DESC": "Spare pump"})
    assert sa["attributes"]["DESC"] == "Spare pump"
    await call.expect_error("set_block_attributes", "tags", handle=ins[0], values={"NOPE": "x"})
    eq = next(x for x in (await call("list_blocks"))["blocks"] if x["name"] == "MCP_EQUIP")
    assert eq["insert_count"] == 3 and set(eq["attribute_tags"]) == {"TAG", "DESC"}

    # ---------- reading and filters
    q = await call("query_entities", layers=["MCP_WALL"], types=["polyline"])
    assert {rect, pl} <= {x["handle"] for x in q["entities"]} and q["engine"] == engine
    q = await call("query_entities", block_name="MCP_EQ*")
    assert q["total"] == 3 and all(x["attributes"]["DESC"] for x in q["entities"])
    q = await call("query_entities", text_contains="ROO")
    assert [x["handle"] for x in q["entities"]] == [text]
    q = await call("query_entities", within=[[-10, -10], [410, 310]], within_mode="inside", types=["circle"])
    assert [x["handle"] for x in q["entities"]] == [circ]
    q = await call("query_entities", limit=2, offset=0, include_bbox=True)
    assert q["returned"] == 2 and q["has_more"] and q["entities"][0]["bbox"]

    # ---------- editing
    tr = await call("transform_entities", handles=[pl], operation="copy", from_point=[0, 0], to_point=[0, 500])
    copy_pl = tr["new_handles"][0]
    assert (await call("get_entity", handle=copy_pl))["vertices"][0] == [500.0, 500.0]
    await call("transform_entities", handles=[copy_pl], operation="rotate", base_point=[500, 500], angle_deg=90)
    assert [round(v, 6) for v in (await call("get_entity", handle=copy_pl))["vertices"][1]] == [500.0, 600.0]
    await call("transform_entities", handles=[copy_pl], operation="scale", base_point=[500, 500], factor=2)
    assert math.isclose((await call("get_entity", handle=copy_pl))["length"], 400.0)
    mi = await call("transform_entities", handles=[copy_pl], operation="mirror", mirror_line=[[0, 0], [0, 1]],
                    keep_original=True)
    assert (await call("get_entity", handle=mi["new_handles"][0]))["vertices"][0] == [-500.0, 500.0]

    off = await call("offset_entity", handle=rect, distance=20, toward_point=[200, 150])
    assert math.isclose(off["new"][0]["area"], 360 * 260)  # inwards
    off = await call("offset_entity", handle=rect, distance=20, toward_point=[-100, -100])
    assert math.isclose(off["new"][0]["area"], 440 * 340)  # outwards

    ar = await call("array_entities", handles=[ins[2]], kind="rectangular", rows=2, columns=3,
                    row_spacing=-60, column_spacing=100)
    assert ar["created"] == 5
    await call("erase_entities", handles=ar["new_handles"])
    await call.expect_error("get_entity", "handle", handle=ar["new_handles"][0])

    sp = await call("set_entity_properties", handles=[line], color="red", lineweight=0.5, layer="MCP_NEW")
    assert "layer 'MCP_NEW' created" in sp["notes"]
    e = await call("get_entity", handle=line)
    assert e["color"] == 1 and e["lineweight"] == 50 and e["layer"] == "MCP_NEW"
    et = await call("edit_text", handle=text, text="ROOM 01", height=25)
    assert et["text"] == "ROOM 01" and math.isclose(et["height"], 25)
    assert math.isclose((await call("modify_geometry", handle=circ, radius=60))["radius"], 60)
    assert (await call("explode_entities", handles=[pl]))["created"] == 2

    # ---------- extraction
    qt = await call("quantities", group_by="layer", layers=["MCP_WALL"])
    row = next(r for r in qt["rows"] if r["group"] == "MCP_WALL")
    assert row["count"] >= 3 and row["total_area"] > 0 and qt["engine"] == engine
    csvp = os.path.join(tmpdir, f"blocks_{engine}.csv")
    br = await call("block_report", block_name="MCP_EQUIP", csv_path=csvp, csv_format="excel-comma-decimal")
    assert br["total_found"] == 3 and os.path.isfile(csvp)
    with open(csvp, encoding="utf-8-sig") as fh:
        content = fh.read()
    assert "P-102" in content and "Spare pump" in content and ";" in content
    await call.expect_error("block_report", "already exists", block_name="MCP_EQUIP", csv_path=csvp)

    # ---------- standards
    std = {
        "name": "test", "units": "mm",
        "layers": {"MCP_WALL": {"color": 1, "lineweight": 0.35},
                   "MCP_DIM": {"color": 2, "linetype": "Continuous", "lineweight": 0.18}},
        "text_styles": {"MCP_TXT": {"font_file": "romans.shx", "width_factor": 0.8}},
        "forbid_entities_on_layer_0": True,
    }
    codes = {i["code"] for i in (await call("check_standards", standard=std))["issues"]}
    assert "layer_missing" in codes and "entities_on_layer_0" in codes
    ap = await call("apply_standards", standard=std, move_layer0_to="MCP_GENERAL")
    left = {i["code"] for i in ap["remaining_issues"]}
    assert "layer_missing" not in left and "entities_on_layer_0" not in left, ap
    assert "example" in (await call("list_standards"))["standards"]

    # ---------- free commands, cancellation and guards
    before = (await call("query_entities", limit=1))["total"]
    rc = await call("run_command", command="_.CIRCLE 1000,1000 25")
    assert rc["modelspace_entities_after"] == rc["modelspace_entities_before"] + 1
    rc = await call("run_command", command="_.LINE 0,0\n", timeout_s=3)
    assert rc.get("cancelled_after_timeout") is True, rc
    assert (await call("status")).get("command_in_progress") is None
    await call.expect_error("run_command", "blocked", command="_.SHELL\n")
    await call.expect_error("run_lisp", "blocked", code='(startapp "notepad.exe")')
    r = await call("run_lisp", code="(/ 1 0)")
    assert r["result"] is None and "divide" in r["error"].lower()
    long_code = "\n".join(f"(setq v{i} {i})" for i in range(400)) + "\n(+ v1 v399)"
    assert (await call("run_lisp", code=long_code))["result"] == "400"  # > command-line limit, goes via file

    # ---------- undo: 1 call = 1 step, even with fast queries in between
    n0 = (await call("query_entities", limit=1))["total"]
    await call("draw_batch", items=[{"type": "line", "start": [0, 0], "end": [9, 9]}] * 4)
    assert (await call("query_entities", limit=1))["total"] == n0 + 4
    await call("quantities")
    u = await call("undo", steps=1)
    assert (await call("query_entities", limit=1))["total"] == n0, u
    assert n0 == before + 1

    # ---------- image (with coordinate grid)
    img = await call("get_view_image", zoom="extents", grid=True)
    png = next(c for c in img if c.type == "image")
    assert png.mime_type == "image/png" and len(png.data) > 1000
    meta = next(c for c in img if c.type == "text").text
    assert "units_per_pixel" in meta and "top_left" in meta
    with open(os.path.join(tmpdir, f"view_{engine}.png"), "wb") as fh:
        fh.write(base64.b64decode(png.data))

    # ---------- purge and file safety
    pa = await call("purge_and_audit", purge=True, audit=True)
    assert pa["after"]["layers"] <= pa["before"]["layers"]
    await call.expect_error("save_drawing", "never saved")
    sv = await call("save_drawing", path=os.path.join(tmpdir, f"test_mcp_{engine}.dwg"))
    assert os.path.isfile(sv["saved_as"])
    await call.expect_error("save_drawing", "already exists", path=sv["saved_as"])
    await call.expect_error("save_drawing", "overwrite")


def test_engines_agree():
    """LISP and COM must describe the same entities the same way."""
    from acad_mcp.config import Config
    from mcp import Client

    from acad_mcp.server import build

    async def body(call, tmp):
        await call("run_lisp", code=(
            '(setq blk (vla-Add (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))) '
            '(vlax-3d-point 0 0 0) "MCP_B"))(vla-AddAttribute blk 2.0 0 "T?" (vlax-3d-point 0 0 0) "T" "x") 1'))
        await call("draw_batch", items=[
            {"type": "line", "start": [1, 2], "end": [4, 6], "layer": "A"},
            {"type": "circle", "center": [10, 10], "radius": 3, "color": 5},
            {"type": "arc", "center": [0, 0], "radius": 10, "start_angle_deg": 30, "end_angle_deg": 120},
            {"type": "polyline", "points": [[0, 0], [10, 0], [10, 5]], "closed": True},
            {"type": "text", "text": 'Ação "x" \\ y', "insertion": [5, 5], "height": 2},
            {"type": "mtext", "text": "two\nlines", "insertion": [20, 20], "height": 2},
        ])
        await call("insert_block", name="MCP_B", insertion=[7, 7], attributes={"T": "ok"}, rotation_deg=30)

        lisp_srv = build(Config(fast_threshold=1))
        com_srv = build(Config(fast_threshold=10**9))
        out = {}
        for key, srv in (("lisp", lisp_srv), ("com", com_srv)):
            async with Client(srv) as c:
                r = await c.call_tool("query_entities", {"limit": 100})
                out[key] = {e["handle"]: e for e in r.structured_content["entities"]}
                assert r.structured_content["engine"] == key
        assert out["lisp"].keys() == out["com"].keys()
        fields = ["type", "layer", "color", "length", "area", "radius", "center", "start", "end", "text",
                  "attributes", "name", "vertex_count", "closed", "start_angle_deg", "end_angle_deg"]
        for h, le in out["lisp"].items():
            ce = out["com"][h]
            for f in fields:
                if f in le and f in ce:
                    lv, cv = le[f], ce[f]
                    if isinstance(lv, float) or isinstance(cv, float):
                        assert math.isclose(float(lv), float(cv), rel_tol=1e-6, abs_tol=1e-6), (h, f, lv, cv)
                    elif isinstance(lv, list):
                        assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(lv, cv)), (h, f, lv, cv)
                    else:
                        assert lv == cv, (h, f, lv, cv)

    run_session(Config(), body)


def _vector_pdf(w: float = 400, h: float = 200) -> bytes:
    """Hand-written one-page PDF with a single stroked rectangle (true vector geometry)."""
    content = f"2 w 100 100 {w} {h} re S".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 700 500] /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_import_files(monkeypatch, tmp_path):
    """import_file: DXF text (exploded and as a block), DWG by path, PNG by base64, vector PDF, bad DXF."""
    from PIL import Image

    from acad_mcp.config import Config
    from acad_mcp.tools import files

    monkeypatch.setattr(files, "RECEIVED_DIR", tmp_path / "received")
    min_dxf = ("0\nSECTION\n2\nENTITIES\n"
               "0\nLINE\n8\nIMPORTED\n10\n0\n20\n0\n30\n0\n11\n200\n21\n0\n31\n0\n"
               "0\nCIRCLE\n8\nIMPORTED\n10\n100\n20\n50\n30\n0\n40\n25\n"
               "0\nENDSEC\n0\nEOF\n")

    async def body(call, tmp):
        test_doc = (await call("status"))["active_document"]["name"]
        r = await call("import_file", filename="sketch.dxf", content_text=min_dxf, insertion=[1000, 0])
        assert r["action"] == "insert_exploded" and r["inserted_entities"] == 2, r
        assert r["converted_to"].endswith("_converted.dwg")
        ents = [await call("get_entity", handle=h) for h in r["handles"]]
        assert sorted(e["type"] for e in ents) == ["Circle", "Line"]
        circle = next(e for e in ents if e["type"] == "Circle")
        assert circle["center"] == [1100.0, 50.0] and math.isclose(circle["radius"], 25.0)
        assert (await call("status"))["active_document"]["name"] == test_doc, "must return to the target drawing"

        r = await call("import_file", source_path=r["converted_to"], action="insert_block",
                       insertion=[0, 500], scale=2, layer="BLOCKS")
        assert r["block"]["type"] == "BlockReference" and r["block"]["scale"] == [2.0, 2.0, 2.0]

        buf = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(buf, "PNG")
        r = await call("import_file", filename="ref.png", content_base64=base64.b64encode(buf.getvalue()).decode(),
                       insertion=[0, -300], scale=400, layer="REFERENCE")
        assert r["action"] == "attach_image" and math.isclose(r["image"]["width"], 400.0)
        assert math.isclose(r["image"]["height"], 200.0)

        r = await call("import_file", filename="sheet.pdf", content_base64=base64.b64encode(_vector_pdf()).decode(),
                       insertion=[0, 2000])
        assert r["action"] == "import_pdf" and r["imported_entities"] >= 1, r
        polys = [await call("get_entity", handle=h) for h in r["handles"]]
        rect = max(polys, key=lambda e: e.get("area") or 0)
        (x0, y0), (x1, y1) = rect["bbox"][0][:2], rect["bbox"][1][:2]
        assert math.isclose((x1 - x0) / (y1 - y0), 2.0, rel_tol=0.02), rect  # 400 x 200 keeps its proportion
        print(f"\nPDF 400x200 pt imported as {x1 - x0:.3f} x {y1 - y0:.3f} drawing units")

        await call.expect_error("import_file", "dxf", filename="bad.dxf", content_text="garbage")
        r = await call("import_file", filename="keep.dxf", content_text=min_dxf, action="save_only")
        assert os.path.isfile(r["file"])
        assert any(f["name"] == "keep.dxf" for f in (await call("list_received_files"))["files"])
        r = await call("import_file", source_path=r["file"], action="open")
        assert r["opened"]["modelspace_entities"] == 2
        await call("close_drawing", name=r["opened"]["name"], discard_changes=True)

    run_session(Config(), body, str(tmp_path))


def _make_test_dwgs(folder: str, n: int = 3) -> list[str]:
    """Create n small DWGs (texts + a block with attributes) through COM, saved in folder."""
    from acad_mcp.acad.util import P
    from acad_mcp.acad.worker import ComWorker

    w = ComWorker()
    paths = []

    def mk(app, i):
        doc = app.Documents.Add()
        ms = doc.ModelSpace
        ms.AddText(f"OLD TEXT {i}", P([0, 0]), 2.5)
        ms.AddMText(P([0, 10]), 0, "an OLD note")
        blk = doc.Blocks.Add(P([0, 0]), "STAMP")
        blk.AddCircle(P([0, 0]), 1)
        blk.AddAttribute(2.0, 0, "REV?", P([2, 0]), "REV", "A")
        br = ms.InsertBlock(P([50, 0]), "STAMP", 1, 1, 1, 0)
        br.GetAttributes()[0].TextString = "OLD-A"
        ms.AddLine(P([0, 0]), P([100, 100]))
        path = os.path.join(folder, f"sheet_{i}.dwg")
        doc.SaveAs(path)
        doc.Close(False)
        return path

    for i in range(n):
        paths.append(w.call(lambda app, i=i: mk(app, i)))
    return paths


def _sha(path: str) -> str:
    import hashlib

    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_routines_and_batch(monkeypatch, tmp_path):
    from acad_mcp.config import Config
    from acad_mcp.tools import routines

    monkeypatch.setattr(routines, "USER_DIR", tmp_path / "routines")
    src = tmp_path / "dwgs"
    src.mkdir()
    dwgs = _make_test_dwgs(str(src))
    hashes = {p: _sha(p) for p in dwgs}

    async def wait_job(call, job_id, limit=300):
        for _ in range(limit):
            st = await call("batch_status", job_id=job_id)
            if st["status"] != "running":
                return st
            await asyncio.sleep(1)
        raise AssertionError("batch did not finish")

    async def body(call, tmp):
        names = {r["name"] for r in (await call("list_routines"))["routines"]}
        assert {"find_replace_text", "fill_title_block", "export_attributes", "count_blocks", "plot_pdf"} <= names

        # ---- routines on the active (test) drawing
        await call("draw_batch", items=[{"type": "text", "text": "OLD here", "insertion": [0, 0]}])
        r = await call("run_routine", name="find_replace_text", args={"find": "OLD", "replace": "NEW"})
        assert r.get("error") is None and "1" in r["result"], r
        assert (await call("query_entities", text_contains="NEW here"))["total"] == 1

        # ---- a routine saved by the model, reused
        code = ('(defun mcp-r:count_lines (args / ss) (setq ss (ssget "_X" (list (cons 0 "LINE")))) '
                '(itoa (if ss (sslength ss) 0)))')
        sv = await call("save_routine", name="count_lines", code=code, description="Counts lines")
        assert sv["autocad_command"] == "MCP_COUNT_LINES"
        await call.expect_error("save_routine", "already exists", name="count_lines", code=code, description="x")
        await call.expect_error("save_routine", "blocked", name="evil", code='(defun mcp-r:evil (a) (startapp "x"))',
                                description="x")
        await call("draw_batch", items=[{"type": "line", "start": [0, 0], "end": [1, 1]}] * 3)
        assert (await call("run_routine", name="count_lines"))["result"] == '"3"'
        ex = await call("export_routines_for_autocad")
        assert os.path.isfile(ex["file"]) and "MCP_FIND_REPLACE" in ex["commands"]

        # ---- batch, console engine (AutoCAD without a window)
        dry = await call("batch_process", folder=str(src), routine="find_replace_text", dry_run=True)
        assert dry["count"] == 3
        out_console = str(tmp_path / "out_console")
        job = await call("batch_process", folder=str(src), routine="find_replace_text",
                         args={"find": "OLD", "replace": "NEW"}, output_folder=out_console, workers=3)
        st = await wait_job(call, job["job_id"])
        assert st["status"] == "done" and st["ok"] == 3, st
        assert os.path.isfile(st["report"])
        print(f"\nconsole batch: 3 files in {st['seconds']} s")

        # attribute values are inside the block: export them to CSV with a second batch run
        csvp = str(tmp_path / "attrs.csv")
        job = await call("batch_process", folder=out_console, routine="export_attributes",
                         args={"csv": csvp, "block": "STAMP"}, output_folder=str(tmp_path / "out_attrs"))
        st = await wait_job(call, job["job_id"])
        assert st["ok"] == 3, st
        rows = open(csvp, encoding="mbcs").read().splitlines()
        assert len(rows) == 3 and all("REV=NEW-A" in r for r in rows), rows

        # ---- batch, gui engine with a JSON standard
        job = await call("batch_process", folder=str(src), standard_name="example", engine="gui",
                         output_folder=str(tmp_path / "out_gui"))
        st = await wait_job(call, job["job_id"])
        assert st["status"] == "done" and st["ok"] == 3, st

        # ---- batch PDF plot
        job = await call("batch_process", folder=str(src), routine="plot_pdf",
                         args={"out_dir": str(tmp_path / "pdfs").replace("\\", "/")},
                         output_folder=str(tmp_path / "out_pdf"))
        (tmp_path / "pdfs").mkdir(exist_ok=True)
        st = await wait_job(call, job["job_id"])
        pdfs = list((tmp_path / "pdfs").glob("*.pdf"))
        assert st["ok"] == 3 and len(pdfs) == 3, (st, pdfs)

    run_session(Config(), body, str(tmp_path))

    # originals untouched
    assert all(_sha(p) == h for p, h in hashes.items()), "originals changed!"

    # a processed copy really changed
    from acad_mcp.acad.worker import ComWorker

    def check(app):
        doc = app.Documents.Open(str(tmp_path / "out_gui" / "sheet_0.dwg"), True)
        try:
            names = {doc.Layers.Item(i).Name for i in range(doc.Layers.Count)}
        finally:
            doc.Close(False)
        doc = app.Documents.Open(str(tmp_path / "out_console" / "sheet_0.dwg"), True)
        try:
            texts = [doc.ModelSpace.Item(i).TextString for i in range(doc.ModelSpace.Count)
                     if doc.ModelSpace.Item(i).ObjectName in ("AcDbText", "AcDbMText")]
        finally:
            doc.Close(False)
        return names, texts

    names, texts = ComWorker().call(check)
    assert {"OUTLINE", "CENTER", "DIM"} <= names
    assert texts and all("OLD" not in x for x in texts), texts


def test_trace_image_and_prompt(tmp_path):
    import cv2
    import numpy as np
    from mcp import Client

    from acad_mcp.config import Config
    from acad_mcp.server import build

    img = np.full((600, 1000), 255, np.uint8)
    cv2.rectangle(img, (100, 100), (900, 500), 0, 4)
    png = tmp_path / "scan.png"
    cv2.imwrite(str(png), img)

    async def body(call, tmp):
        r = await call("trace_image", source_path=str(png), real_width=10000, insertion=[0, 0])
        assert r["segments"] >= 4, r
        q = await call("query_entities", layers=["TRACE"], types=["line"])
        lens = sorted((e["length"] for e in q["entities"]), reverse=True)[:4]
        assert all(abs(x - 8000) / 8000 < 0.01 for x in lens[:2]), lens  # 800 px * 10 units/px
        assert all(abs(x - 4000) / 4000 < 0.01 for x in lens[2:4]), lens

    run_session(Config(), body, str(tmp_path))

    async def prompts():
        async with Client(build(Config())) as c:
            ps = await c.list_prompts()
            assert any(p.name == "recreate_drawing" for p in ps.prompts)
            got = await c.get_prompt("recreate_drawing", {"notes": "units mm"})
            text = got.messages[0].content.text
            assert "draw_batch" in text and "units mm" in text

    asyncio.run(prompts())


def test_busy_autocad_waits_instead_of_failing():
    """With AutoCAD busy ~3 s (a pending command from ANOTHER process), the call waits and completes."""
    import subprocess
    import sys
    import threading
    import time

    from acad_mcp.acad.winutil import cancel_everything
    from acad_mcp.acad.worker import ComWorker

    w = ComWorker(busy_timeout=15)
    name = w.call(lambda app: app.Documents.Add().Name)
    try:
        hwnd = w.main_hwnd
        blocker = subprocess.Popen([sys.executable, "-c", (
            "import pythoncom, win32com.client; pythoncom.CoInitialize();"
            "a = win32com.client.GetActiveObject('AutoCAD.Application');"
            "a.ActiveDocument.SendCommand('_.LINE 0,0\\n')"
        )])
        time.sleep(1.5)  # let LINE start
        threading.Timer(3.0, lambda: cancel_everything(hwnd)).start()
        before = w.msgfilter.rejections
        t0 = time.monotonic()
        doc_name = w.call(lambda app: app.ActiveDocument.Name)
        waited = time.monotonic() - t0
        blocker.wait(timeout=10)
        assert doc_name == name
        assert w.msgfilter.rejections > before, "the filter should have seen rejections"
        assert 1.0 < waited < 10, waited
    finally:
        def close(app):
            for i in range(app.Documents.Count):
                d = app.Documents.Item(i)
                if d.Name == name:
                    d.Close(False)
                    return
        w.call(close)
