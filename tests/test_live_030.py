"""Live tests for what 0.3.0 added: hatch with holes, dimension styles, layouts / viewports / PDF per sheet and
dynamic blocks. Same rules as test_live.py: own drawings only, closed without saving."""

from __future__ import annotations

import asyncio
import math
import os

import pytest
from test_live import Session, run_session  # skips the module when AutoCAD is missing or minimized

pytestmark = pytest.mark.live

SAMPLE = r"C:\Program Files\Autodesk\AutoCAD 2027\Sample\en-us\Dynamic Blocks\Architectural - Metric.dwg"


async def lisp_value(call: Session, code: str) -> str:
    r = await call("run_lisp", code=code)
    assert r.get("error") is None, r
    return str(r["result"]).strip('"')


def test_hatch_with_holes():
    from acad_mcp.config import Config

    async def body(call, tmp):
        b = await call("draw_batch", items=[
            {"type": "rectangle", "corner1": [0, 0], "corner2": [100, 100]},
            {"type": "circle", "center": [30, 30], "radius": 10},
            {"type": "circle", "center": [70, 70], "radius": 10},
            {"type": "line", "start": [10, 80], "end": [20, 90]},
        ])
        outer, c1, c2, line = b["handles"]

        h = await call("draw_hatch", boundary_handles=[outer], inner_boundary_handles=[c1, c2], pattern="ANSI31",
                       scale=2, layer="MCP_HATCH")
        assert math.isclose(h["area"], 10000 - 2 * math.pi * 100, rel_tol=1e-6), h
        assert h["holes"] == 2 and math.isclose(h["outer_area"], 10000.0) and h["layer"] == "MCP_HATCH"
        assert math.isclose((await call("get_entity", handle=h["handle"]))["area"], h["area"])

        full = await call("draw_hatch", boundary_handles=[outer], pattern="SOLID")  # no holes: as before
        assert math.isclose(full["area"], 10000.0) and "holes" not in full

        # an island inside a hole is filled again
        ring = await call("draw_batch", items=[{"type": "circle", "center": [300, 50], "radius": r} for r in (50, 30, 10)])
        o, i, k = ring["handles"]
        n = await call("draw_hatch", boundary_handles=[o], inner_boundary_handles=[i, k], pattern="SOLID")
        assert math.isclose(n["area"], math.pi * (2500 - 900 + 100), rel_tol=1e-6), n

        hatches = (await call("query_entities", types=["hatch"]))["total"]
        await call.expect_error("draw_hatch", "both", boundary_handles=[outer], inner_boundary_handles=[outer])
        await call.expect_error("draw_hatch", "closed", boundary_handles=[outer], inner_boundary_handles=[line])
        assert (await call("query_entities", types=["hatch"]))["total"] == hatches  # failures leave nothing

        u = await call("undo", steps=1)
        assert u["undone_steps"] == 1 and (await call("query_entities", types=["hatch"]))["total"] == hatches - 1

    run_session(Config(command_timeout=5), body)


def test_dimension_styles():
    from acad_mcp.config import Config

    async def styles(call):
        ls = await call("list_styles", kind="dimension")
        return ls, {s["name"]: s for s in ls["dim_styles"]}

    async def body(call, tmp):
        ls, by = await styles(call)
        start = ls["current_dim_style"]
        assert "text_height" in by[start] and "dim_styles_note" not in ls, ls

        r = await call("manage_dim_style", name="MCP_D50", text_height=3.5, arrow_size=2, overall_scale=50,
                       decimals=1, extension_offset=1.5, make_current=True)
        assert r["created"] and r["current"], r
        assert (r["text_height"], r["arrow_size"], r["overall_scale"], r["decimals"]) == (3.5, 2.0, 50.0, 1), r

        d = await call("draw_dimension", kind="aligned", p1=[0, 0], p2=[1000, 0], text_position=[500, -300])
        assert d["style"] == "MCP_D50" and math.isclose(d["measurement"], 1000.0)
        height = f'(vla-get-TextHeight (vlax-ename->vla-object (handent "{d["handle"]}")))'
        assert math.isclose(float(await lisp_value(call, height)), 3.5)

        ls, by = await styles(call)
        s = by["MCP_D50"]
        assert ls["current_dim_style"] == "MCP_D50"
        assert (s["text_height"], s["arrow_size"], s["overall_scale"], s["decimals"], s["extension_offset"]) == \
            (3.5, 2.0, 50.0, 1, 1.5), s

        # a second style based on the first, NOT made current: the current style and its variables stay
        await call("manage_text_style", name="MCP_DT", font_file="romans.shx")
        r = await call("manage_dim_style", name="MCP_BIG", based_on="MCP_D50", text_height=7, text_style="MCP_DT")
        assert r["created"] and not r["current"] and r["text_height"] == 7.0 and r["overall_scale"] == 50.0, r
        ls, by = await styles(call)
        assert ls["current_dim_style"] == "MCP_D50" and by["MCP_BIG"]["text_height"] == 7.0
        assert by["MCP_BIG"]["text_style"] == "MCP_DT" and by["MCP_D50"]["text_height"] == 3.5
        assert math.isclose(float(await lisp_value(call, '(getvar "DIMTXT")')), 3.5)

        # changing an existing style changes the dimensions that use it
        r = await call("manage_dim_style", name="MCP_D50", text_height=5)
        assert not r["created"] and r["text_height"] == 5.0 and r["arrow_size"] == 2.0, r
        assert math.isclose(float(await lisp_value(call, height)), 5.0)

        # "Annotative" keeps DXF defaults, which entget leaves out: those groups are appended, not replaced
        r = await call("manage_dim_style", name="MCP_ANN", based_on="Annotative", text_height=4, decimals=3)
        assert r["created"] and (r["text_height"], r["decimals"], r["arrow_size"]) == (4.0, 3, 0.18), r
        assert (await call("undo", steps=1))["undone_steps"] == 1  # creation undone: the style is gone
        assert "MCP_ANN" not in (await styles(call))[1]

        await call.expect_error("manage_dim_style", "based_on", name="MCP_X", based_on="NOPE")
        await call.expect_error("manage_dim_style", "only applies", name="MCP_D50", based_on="MCP_BIG")
        await call.expect_error("manage_dim_style", "text style", name="MCP_X", text_style="NOPE")
        await call.expect_error("manage_dim_style", "nothing to change", name="MCP_D50")
        assert "MCP_X" not in (await styles(call))[1]

        u = await call("undo", steps=1)  # undoes the change to 5
        assert u["undone_steps"] == 1 and (await styles(call))[1]["MCP_D50"]["text_height"] == 3.5

    run_session(Config(command_timeout=5), body)


def test_layouts_viewports_and_pdf(tmp_path):
    from acad_mcp.config import Config
    from acad_mcp.tools.layouts import pdf_page_mm

    async def layouts(call):
        ll = await call("list_layouts")
        return ll, {x["name"]: x for x in ll["layouts"]}

    async def body(call, tmp):
        await call("draw_batch", items=[
            {"type": "rectangle", "corner1": [0, 0], "corner2": [1000, 500]},
            {"type": "text", "text": "IN MODEL", "insertion": [100, 100], "height": 50},
        ])
        ll, by = await layouts(call)
        assert ll["current"] == "Model" and by["Model"]["model"] and by["Model"]["entities"] == 2
        sheets_before = [x["name"] for x in ll["layouts"] if not x["model"]]

        r = await call("manage_layout", action="create", name="MCP_A3", paper="A3", plot_style="monochrome.ctb",
                       plot_area="layout", plot_scale="1:1")
        assert r["device"] == "DWG To PDF.pc3" and r["paper_size_mm"] == [420.0, 297.0], r
        assert r["plot_scale"] == "1:1" and r["plot_area"] == "layout" and r["plot_style"] == "monochrome.ctb", r
        assert any("DWG To PDF" in n for n in r["notes"])

        vp = await call("create_viewport", layout="MCP_A3", center=[210, 148.5], width=380, height=260, scale="1:5",
                        model_center=[500, 250], locked=True, layer="MCP_VP")
        assert math.isclose(vp["scale"], 0.2) and vp["scale_text"] == "1:5" and vp["locked"], vp
        assert vp["layer"] == "MCP_VP" and vp["layout"] == "MCP_A3"
        assert math.isclose(vp["model_center"][0], 500, abs_tol=0.01) and math.isclose(vp["model_center"][1], 250, abs_tol=0.01)
        # the view centre and height really stored in the viewport (DXF 12 and 45)
        dxf = await lisp_value(call, f'(mapcar (function (lambda (k) (cdr (assoc k (entget (handent "{vp["handle"]}"))))))'
                                     ' (list 12 45))')
        nums = [float(x) for x in dxf.replace("(", " ").replace(")", " ").split()]
        assert math.isclose(nums[0], 500, abs_tol=0.01) and math.isclose(nums[1], 250, abs_tol=0.01), dxf
        assert math.isclose(nums[-1], 260 / 0.2, rel_tol=1e-6), dxf

        ll, by = await layouts(call)
        assert ll["current"] == "MCP_A3"
        mine = [v for v in by["MCP_A3"]["viewports"] if v["handle"] == vp["handle"]]
        assert len(mine) == 1 and math.isclose(mine[0]["scale"], 0.2) and mine[0]["width"] == 380.0, by["MCP_A3"]

        # things drawn on the sheet, and the layout filter
        await call("draw_text", text="SHEET-A", insertion=[20, 20], height=5, space="paper")
        assert (await call("query_entities", layout="MCP_A3", types=["text"]))["total"] == 1
        assert (await call("query_entities", layout="Model", types=["text"]))["total"] == 1
        assert (await call("query_entities", space="all", types=["text"]))["total"] == 2
        q = await call("quantities", layout="MCP_A3", types=["text"])
        assert q["entities_considered"] == 1, q

        # a viewport with no scale shows the whole drawing; undo removes it
        n0 = len(by["MCP_A3"]["viewports"])
        await call("create_viewport", center=[100, 60], width=80, height=60)
        assert len((await layouts(call))[1]["MCP_A3"]["viewports"]) == n0 + 1
        assert (await call("undo", steps=1))["undone_steps"] == 1
        assert len((await layouts(call))[1]["MCP_A3"]["viewports"]) == n0

        # copy, rename, page setup of the copy
        c = await call("manage_layout", action="copy", name="MCP_A3", new_name="MCP_COPY")
        assert c["paper_size_mm"] == [420.0, 297.0] and len(c["viewports"]) == n0, c
        r = await call("manage_layout", action="rename", name="MCP_COPY", new_name="MCP_A1")
        assert r["name"] == "MCP_A1"
        r = await call("manage_layout", action="setup", name="MCP_A1", paper="A1", orientation="portrait")
        assert r["paper_size_mm"] == [594.0, 841.0] and r["device"] == "DWG To PDF.pc3", r
        r = await call("manage_layout", action="setup", name="Model", paper="A3", plot_area="extents", plot_scale="fit")
        assert r["plot_scale"] == "fit" and r["plot_area"] == "extents", r

        # one PDF per sheet, with the real page size
        out = str(tmp_path / "pdfs")
        p = await call("plot_layouts", layouts=["MCP_A3", "MCP_A1", "Model"], output_folder=out)
        assert p["written"] == 3 and p["failed"] == 0, p
        sizes = {f["layout"]: f["page_mm"] for f in p["files"]}
        assert sizes["MCP_A3"] == [420.0, 297.0] and sizes["MCP_A1"] == [594.0, 841.0], sizes
        assert sorted(sizes["Model"]) == [297.0, 420.0], sizes
        assert all(os.path.getsize(f["pdf"]) > 500 and "warning" not in f for f in p["files"]), p
        assert pdf_page_mm(p["files"][0]["pdf"]) == [420.0, 297.0]
        assert (await layouts(call))[0]["current"] == "MCP_A3"  # plotting does not leave another tab active

        await call.expect_error("plot_layouts", "already exist", layouts=["MCP_A3"], output_folder=out)
        p = await call("plot_layouts", layouts=["MCP_A3"], output_folder=out, overwrite=True)
        assert p["written"] == 1

        # every sheet: the ones never set up are plotted with a warning
        p = await call("plot_layouts", output_folder=str(tmp_path / "all"))
        assert p["written"] == len(sheets_before) + 2, p
        assert all(("warning" in f) == (f["layout"] in sheets_before) for f in p["files"]), p
        await call.expect_error("plot_layouts", "output_folder", layouts=["MCP_A3"])  # drawing never saved

        d = await call("manage_layout", action="delete", name="MCP_A1")
        assert "MCP_A1" not in d["layouts"] and "MCP_A3" in d["layouts"]

        await call.expect_error("manage_layout", "already exists", action="create", name="MCP_A3")
        await call.expect_error("manage_layout", "new_name", action="copy", name="MCP_A3")
        await call.expect_error("manage_layout", "does not exist", action="activate", name="NOPE")
        await call.expect_error("manage_layout", "not found", action="setup", name="MCP_A3", paper="Z9")
        await call.expect_error("manage_layout", "", action="delete", name="Model")
        await call.expect_error("create_viewport", "sheet", layout="Model", center=[0, 0], width=10, height=10)
        await call.expect_error("create_viewport", "scale", layout="MCP_A3", center=[0, 0], width=10, height=10,
                                scale="one to fifty")

        r = await call("manage_layout", action="activate", name="Model")
        assert r["current"] and (await layouts(call))[0]["current"] == "Model"

    run_session(Config(command_timeout=8), body, str(tmp_path))


def test_batch_pdf_per_sheet_and_title_block_by_layout(tmp_path):
    """Console engine: one PDF per sheet of every file; fill_title_block restricted to one sheet."""
    from acad_mcp.acad.util import P
    from acad_mcp.acad.worker import ComWorker
    from acad_mcp.config import Config
    from acad_mcp.tools.layouts import apply_page_setup, pdf_page_mm

    src = tmp_path / "dwgs"
    src.mkdir()

    def mk(app):
        doc = app.Documents.Add()
        try:
            doc.ModelSpace.AddCircle(P([50, 50]), 40)
            blk = doc.Blocks.Add(P([0, 0]), "STAMP")
            blk.AddCircle(P([0, 0]), 1)
            blk.AddAttribute(2.0, 0, "REV?", P([2, 0]), "REV", "A")
            for name, paper in (("S1", "A3"), ("S2", "A4")):
                lay = doc.Layouts.Add(name)
                apply_page_setup(lay, None, paper, None, "monochrome.ctb", "layout", "1:1")
                lay.Block.InsertBlock(P([20, 20]), "STAMP", 1, 1, 1, 0)
            doc.SaveAs(str(src / "sheets.dwg"))
        finally:
            doc.Close(False)

    ComWorker(busy_timeout=30).call(mk)

    async def wait_job(call, job_id):
        for _ in range(300):
            st = await call("batch_status", job_id=job_id)
            if st["status"] != "running":
                return st
            await asyncio.sleep(1)
        raise AssertionError("batch did not finish")

    async def body(call, tmp):
        pdfs = tmp_path / "pdfs"
        pdfs.mkdir()
        job = await call("batch_process", folder=str(src), routine="plot_pdf",
                         args={"out_dir": str(pdfs).replace("\\", "/"), "layouts": "S*"},
                         output_folder=str(tmp_path / "out_pdf"))
        st = await wait_job(call, job["job_id"])
        made = {p.name: pdf_page_mm(str(p)) for p in pdfs.glob("*.pdf")}
        assert st["ok"] == 1, st
        assert made == {"sheets-S1.pdf": [420.0, 297.0], "sheets-S2.pdf": [297.0, 210.0]}, (made, st)

        csvp = str(tmp_path / "attrs.csv")
        job = await call("batch_process", folder=str(src), routine="fill_title_block",
                         args={"block": "STAMP", "layout": "S2", "REV": "B"}, output_folder=str(tmp_path / "out_fill"))
        st = await wait_job(call, job["job_id"])
        assert st["ok"] == 1, st
        job = await call("batch_process", folder=str(tmp_path / "out_fill"), routine="export_attributes",
                         args={"csv": csvp, "block": "STAMP"}, output_folder=str(tmp_path / "out_attrs"))
        st = await wait_job(call, job["job_id"])
        rows = open(csvp, encoding="mbcs").read().splitlines()
        assert st["ok"] == 1 and sorted(r.split("REV=")[1][:1] for r in rows) == ["A", "B"], rows

    run_session(Config(), body, str(tmp_path))


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="AutoCAD sample dynamic blocks not installed")
def test_dynamic_blocks():
    from acad_mcp.config import Config

    async def body(call, tmp):
        lib = await call("insert_block", name=SAMPLE, insertion=[50000, 0])  # brings the definitions in
        await call("erase_entities", handles=[lib["handle"]])

        r = await call("insert_block", name="Door - Metric", insertion=[0, 0],
                       dynamic_properties={"Door Size": 900, "Opening Angle": "Open 90º", "hinge": 1})
        props = {p["name"]: p for p in r["dynamic_properties"]}
        assert r["name"] == "Door - Metric" and r["dynamic"], r
        assert props["Door Size"]["value"] == 900.0 and props["Hinge"]["value"] == 1, props
        assert props["Opening Angle"]["value"] == "Open 90º" and "Origin" not in props

        e = await call("get_entity", handle=r["handle"])
        props = {p["name"]: p for p in e["dynamic_properties"]}
        assert e["dynamic"] and 900.0 in props["Door Size"]["allowed_values"]
        assert props["Opening Angle"]["allowed_values"][-1] == "Closed" and props["Door Size"]["description"]

        s = await call("set_dynamic_block_properties", handle=r["handle"], values={"door size": 777, "Swing": 1})
        assert s["applied"] == {"Door Size": 800.0, "Swing": 1}, s  # 777 is not allowed: AutoCAD snaps to 800
        props = {p["name"]: p["value"] for p in s["dynamic_properties"]}
        assert props["Door Size"] == 800.0 and props["Hinge"] == 1 and props["Opening Angle"] == "Open 90º"

        await call.expect_error("set_dynamic_block_properties", "unknown", handle=r["handle"], values={"Nope": 1})
        await call.expect_error("set_dynamic_block_properties", "one of", handle=r["handle"],
                                values={"Opening Angle": "sideways"})
        await call.expect_error("set_dynamic_block_properties", "one of", handle=r["handle"], values={"Hinge": 7})
        await call.expect_error("insert_block", "unknown", name="Door - Metric", insertion=[0, 0],
                                dynamic_properties={"Nope": 1})
        line = await call("draw_line", start=[0, 0], end=[1, 1])
        await call.expect_error("set_dynamic_block_properties", "not a block", handle=line["handle"], values={"a": 1})
        await call("create_block", name="MCP_PLAIN", handles=[line["handle"]], base_point=[0, 0])
        plain = await call("insert_block", name="MCP_PLAIN", insertion=[10, 10])
        assert "dynamic" not in plain
        await call.expect_error("set_dynamic_block_properties", "not a dynamic", handle=plain["handle"], values={"a": 1})

        doors = await call("block_report", block_name="Door*")
        assert doors["total_found"] == 1, doors  # found by its real name although the reference is now anonymous

        assert (await call("undo", steps=3))["undone_steps"] == 3  # plain insert, create_block, line... back to the door
        u = await call("undo", steps=1)                             # the change to 800
        assert u["undone_steps"] == 1
        e = await call("get_entity", handle=r["handle"])
        assert {p["name"]: p["value"] for p in e["dynamic_properties"]}["Door Size"] == 900.0

    run_session(Config(command_timeout=5), body)
