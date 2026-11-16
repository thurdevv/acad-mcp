"""Live tests for what 0.4.0 added: walls with doors and windows, and pipes. Everything drawn is read back from
AutoCAD and compared with hand calculations. Same rules as test_live.py: own drawings only, closed without saving."""

from __future__ import annotations

import math

import pytest
from test_live import Session, run_session  # skips the module when AutoCAD is missing or minimized

pytestmark = pytest.mark.live

ROOM = [[0, 0], [4000, 0], [4000, 3000], [0, 3000], [0, 0]]
BASE = 14000 * 150 + (3000 - 150) * 150  # the room plus a partition from wall to wall, 150 thick


def _inside(pt, ring) -> bool:
    x, y = pt
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


async def outline_area(call: Session, handles: list[str]) -> float:
    """Area enclosed by the outlines as AutoCAD reports them: a ring that lies inside another one is a hole."""
    ents = [await call("get_entity", handle=h) for h in handles]
    assert all(e["type"] == "Polyline" and e["closed"] for e in ents), ents
    rings = [[v[:2] for v in e["vertices"]] for e in ents]
    total = 0.0
    for i, e in enumerate(ents):
        # a vertex of a hole is strictly inside its outer ring, which is all that is needed
        hole = any(j != i and _inside(rings[i][0], r) for j, r in enumerate(rings))
        total += -e["area"] if hole else e["area"]
    return total


async def hatch_area(call: Session, layer: str) -> float:
    q = await call("query_entities", types=["hatch"], layers=[layer])
    return sum(e["area"] for e in q["entities"])


def test_walls_doors_and_windows():
    from acad_mcp.config import Config

    async def body(call, tmp):
        w = await call("draw_walls", axes=[ROOM, [[2500, 0], [2500, 3000]]], thickness=150, layer="MCP_WALL",
                       hatch="ANSI31", hatch_scale=20)
        assert w["wall_id"] == "W1" and w["pieces"] == 1 and w["openings"] == 0, w
        assert math.isclose(w["length"], 17000) and math.isclose(w["area"], BASE), w
        assert len(w["outlines"]) == 3 and len(w["hatches"]) == 1  # outside, and the inside of each of the two rooms
        assert math.isclose(await outline_area(call, w["outlines"]), BASE, rel_tol=1e-9)
        assert math.isclose(await hatch_area(call, "MCP_WALL"), BASE, rel_tol=1e-6)
        e = await call("get_entity", handle=w["outlines"][0])
        assert e["object"] == "W1" and e["layer"] == "MCP_WALL" and e["bbox"][0][:2] == [-75.0, -75.0]

        # ---- a door: the wall is redrawn with the gap, the old outlines are gone
        d = await call("add_opening", wall_id="w1", kind="door", position=[1000, 30], width=900, hinge="start",
                       swing="left", layer="MCP_DOOR")
        assert d["opening_id"] == "O1" and d["start"] == [550.0, 0.0] and d["end"] == [1450.0, 0.0], d
        assert math.isclose(d["wall"]["area"], BASE - 900 * 150) and d["wall"]["pieces"] == 1
        assert len(d["wall"]["outlines"]) == 2  # the left room is now open to the outside
        for old in w["outlines"] + w["hatches"]:
            await call.expect_error("get_entity", "", handle=old)
        leaf, arc = [await call("get_entity", handle=h) for h in d["symbol"]]
        assert leaf["type"] == "Line" and leaf["start"][:2] == [550.0, 75.0] and leaf["end"][:2] == [550.0, 975.0]
        assert arc["type"] == "Arc" and arc["center"][:2] == [550.0, 75.0] and math.isclose(arc["radius"], 900)
        assert math.isclose(arc["start_angle_deg"], 0, abs_tol=1e-6) and math.isclose(arc["end_angle_deg"], 90)
        assert leaf["layer"] == arc["layer"] == "MCP_DOOR" and arc["object"] == "W1"
        assert math.isclose(await outline_area(call, d["wall"]["outlines"]), BASE - 900 * 150, rel_tol=1e-9)

        # ---- a window on the right wall
        win = await call("add_opening", wall_id="W1", kind="window", position=[4000, 1500], width=1200)
        assert win["start"] == [4000.0, 900.0] and win["end"] == [4000.0, 2100.0] and win["opening_id"] == "O2"
        frame, glass = [await call("get_entity", handle=h) for h in win["symbol"]]
        assert frame["closed"] and math.isclose(frame["area"], 1200 * 150) and frame["layer"] == "MCP_WALL"
        assert glass["type"] == "Line" and math.isclose(glass["length"], 1200)
        net = BASE - 900 * 150 - 1200 * 150
        assert math.isclose(win["wall"]["area"], net) and win["wall"]["pieces"] == 1 and len(win["wall"]["outlines"]) == 1
        assert math.isclose(await hatch_area(call, "MCP_WALL"), net, rel_tol=1e-6)

        # ---- a plain gap in the partition splits the wall in two pieces
        gap = await call("add_opening", wall_id="W1", kind="gap", position=[2500, 1500], width=800)
        assert gap["symbol"] == [] and gap["start"] == [2500.0, 1100.0] and gap["wall"]["pieces"] == 2
        net2 = net - 800 * 150
        assert math.isclose(await outline_area(call, gap["wall"]["outlines"]), net2, rel_tol=1e-9)
        assert math.isclose(await hatch_area(call, "MCP_WALL"), net2, rel_tol=1e-6)
        assert (await call("query_entities", types=["hatch"], layers=["MCP_WALL"]))["total"] == 2

        await call.expect_error("add_opening", "overlaps", wall_id="W1", kind="door", position=[1300, 0], width=900)
        await call.expect_error("add_opening", "does not fit", wall_id="W1", kind="door", position=[200, 0], width=900)
        await call.expect_error("add_opening", "away", wall_id="W1", kind="door", position=[1000, 1500], width=900)
        await call.expect_error("add_opening", "no wall", wall_id="W9", kind="gap", position=[0, 0], width=100)
        assert math.isclose(await outline_area(call, gap["wall"]["outlines"]), net2, rel_tol=1e-9)  # nothing changed

        full = await call("get_wall", wall_id="W1")
        assert [o["kind"] for o in full["openings_list"]] == ["door", "window", "gap"] and full["openings"] == 3
        assert full["openings_list"][0]["hinge"] == "start" and full["openings_list"][0]["symbol"] == d["symbol"]
        assert math.isclose(full["area"], net2) and full["axes"][0] == ROOM and "erased_entities" not in full

        # ---- undo brings the previous wall back, record included
        assert (await call("undo", steps=1))["undone_steps"] == 1
        back = await call("get_wall", wall_id="W1")
        assert back["openings"] == 2 and back["pieces"] == 1 and back["outlines"] == win["wall"]["outlines"]
        assert math.isclose(await outline_area(call, back["outlines"]), net, rel_tol=1e-9)

        # ---- a second wall, given by the INSIDE measurements of a room (wall outside the axis)
        inner = [[10000, 0], [13000, 0], [13000, 2000], [10000, 2000], [10000, 0]]
        w2 = await call("draw_walls", axes=[inner], thickness=200, justification="right")
        assert w2["wall_id"] == "W2" and "hatches" not in w2 and math.isclose(w2["area"], 3400 * 2400 - 3000 * 2000)
        boxes = sorted([(await call("get_entity", handle=h))["bbox"] for h in w2["outlines"]])
        assert boxes[0][0][:2] == [9800.0, -200.0] and boxes[0][1][:2] == [13200.0, 2200.0]
        assert boxes[1][0][:2] == [10000.0, 0.0] and boxes[1][1][:2] == [13000.0, 2000.0]
        dd = await call("add_opening", wall_id="W2", kind="door", position=[11500, -100], width=800, hinge="end",
                        swing="right")
        # the middle of this wall is 100 below the axis; it runs along +x, so "right" opens downwards (outside)
        leaf = await call("get_entity", handle=dd["symbol"][0])
        assert dd["start"] == [11100.0, -100.0] and leaf["start"][:2] == [11900.0, -200.0] and leaf["end"][:2] == [11900.0, -1000.0]

        listing = await call("get_wall")
        assert [x["wall_id"] for x in listing["walls"]] == ["W1", "W2"] and listing["pipes"] == []

        # ---- an outline erased by hand is reported, and the next opening redraws the wall anyway
        await call("erase_entities", handles=[back["outlines"][0]])
        assert (await call("get_wall", wall_id="W1"))["erased_entities"] == [back["outlines"][0]]
        again = await call("add_opening", wall_id="W1", kind="gap", position=[2000, 3000], width=500)
        assert math.isclose(await outline_area(call, again["wall"]["outlines"]), net - 500 * 150, rel_tol=1e-9)
        await call.expect_error("draw_walls", "pattern", axes=[[[0, 9000], [1000, 9000]]], thickness=100, hatch="NOPE")
        assert (await call("get_wall"))["walls"][-1]["wall_id"] == "W2"  # the failed wall left nothing behind

    run_session(Config(command_timeout=6), body)


def test_pipes_single_and_double():
    from acad_mcp.config import Config

    async def body(call, tmp):
        p = await call("draw_pipe", path=[[0, 0], [1000, 0], [1000, 500]], diameter=50, layer="MCP_PIPE")
        assert p["pipe_id"] == "P1" and p["mode"] == "single" and p["elbows"] == 1 and p["elbow_radius"] == 0
        assert math.isclose(p["length"], 1500) and "outline" not in p
        e = await call("get_entity", handle=p["centerline"][0])
        assert math.isclose(e["length"], 1500) and not e["closed"] and e["object"] == "P1" and e["layer"] == "MCP_PIPE"

        r = await call("draw_pipe", path=[[0, 1000], [1000, 1000], [1000, 1500], [2000, 1500]], diameter=50,
                       elbow_radius=100)
        expect = 900 + 300 + 900 + 2 * (math.pi * 100 / 2)
        e = await call("get_entity", handle=r["centerline"][0])
        assert r["elbows"] == 2 and math.isclose(r["length"], expect, rel_tol=1e-6), r
        assert math.isclose(e["length"], expect, rel_tol=1e-6) and e["vertex_count"] == 6  # AutoCAD measures the same

        d = await call("draw_pipe", path=[[0, 3000], [1000, 3000], [1000, 3500]], diameter=100, mode="double",
                       layer="MCP_PIPE")
        assert d["elbow_radius"] == 150 and math.isclose(d["length"], 850 + 350 + math.pi * 150 / 2, rel_tol=1e-6)
        o = await call("get_entity", handle=d["outline"][0])
        c = await call("get_entity", handle=d["centerline"][0])
        assert o["closed"] and math.isclose(o["area"], d["length"] * 100, rel_tol=1e-6)  # pipe area = length x diameter
        assert o["bbox"][0][:2] == [0.0, 2950.0] and o["bbox"][1][:2] == [1050.0, 3500.0]
        assert c["linetype"].upper() == "CENTER" and math.isclose(c["length"], d["length"], rel_tol=1e-6)

        s = await call("draw_pipe", path=[[0, 5000], [1000, 5000], [1000, 5500]], diameter=100, mode="double",
                       elbow_radius=0, centerline=False)
        o = await call("get_entity", handle=s["outline"][0])
        assert "centerline" not in s and o["vertex_count"] == 6 and math.isclose(o["area"], 1500 * 100)

        await call.expect_error("draw_pipe", "does not fit", path=[[0, 0], [100, 0], [100, 100]], diameter=50,
                                elbow_radius=200)
        await call.expect_error("draw_pipe", "half the diameter", path=[[0, 0], [1000, 0], [1000, 500]], diameter=100,
                                mode="double", elbow_radius=40)

        listing = await call("get_wall")
        assert [x["pipe_id"] for x in listing["pipes"]] == ["P1", "P2", "P3", "P4"] and listing["walls"] == []
        one = await call("get_wall", wall_id="P3")
        assert one["mode"] == "double" and one["diameter"] == 100 and one["handles"]["outline"] == d["outline"]

        assert (await call("undo", steps=1))["undone_steps"] == 1
        assert [x["pipe_id"] for x in (await call("get_wall"))["pipes"]] == ["P1", "P2", "P3"]
        await call.expect_error("get_entity", "", handle=s["outline"][0])

    run_session(Config(command_timeout=6), body)
