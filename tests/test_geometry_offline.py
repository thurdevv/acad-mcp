"""Walls and pipes geometry: areas, lengths and shapes checked against hand calculations. No AutoCAD needed."""

import math

import pytest

from acad_mcp.acad.errors import AcadError
from acad_mcp.geometry.pipes import pipe_geometry, polyline_length
from acad_mcp.geometry.walls import (
    build_walls,
    centerlines,
    door_symbol,
    locate_opening,
    overlaps_opening,
    window_symbol,
)

ROOM = [[0, 0], [4000, 0], [4000, 3000], [0, 3000], [0, 0]]  # counter-clockwise, closed


def shoelace(ring):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))) / 2.0


def bbox(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


# ------------------------------------------------------------------ walls


def test_room_is_one_polygon_with_a_hole():
    w = build_walls([ROOM], 150)
    assert len(w["polygons"]) == 1 and len(w["polygons"][0]["holes"]) == 1
    p = w["polygons"][0]
    assert bbox(p["outer"]) == [-75, -75, 4075, 3075] and bbox(p["holes"][0]) == [75, 75, 3925, 2925]
    assert len(p["outer"]) == 4 and len(p["holes"][0]) == 4          # no leftover points on the straight sides
    assert math.isclose(w["area"], 4150 * 3150 - 3850 * 2850) and math.isclose(w["area"], 14000 * 150)
    assert math.isclose(w["length"], 14000) and math.isclose(shoelace(p["outer"]) - shoelace(p["holes"][0]), w["area"])


def test_same_room_from_four_separate_lines():
    sides = [[ROOM[i], ROOM[i + 1]] for i in range(4)]
    w = build_walls(sides, 150)
    assert len(w["polygons"]) == 1 and math.isclose(w["area"], 14000 * 150)  # corners filled, nothing doubled
    assert bbox(w["polygons"][0]["outer"]) == [-75, -75, 4075, 3075] and len(w["polygons"][0]["outer"]) == 4


def test_l_t_and_x_junctions():
    ell = build_walls([[[0, 0], [1000, 0]], [[1000, 0], [1000, 1000]]], 100)
    assert len(ell["polygons"]) == 1 and len(ell["polygons"][0]["outer"]) == 6
    assert math.isclose(ell["area"], 2000 * 100) and bbox(ell["polygons"][0]["outer"]) == [0, -50, 1050, 1000]

    tee = build_walls([[[0, 0], [2000, 0]], [[1000, 0], [1000, 1000]]], 100)
    assert len(tee["polygons"]) == 1 and len(tee["polygons"][0]["outer"]) == 8
    assert math.isclose(tee["area"], 2000 * 100 + 950 * 100)

    cross = build_walls([[[0, 0], [2000, 0]], [[1000, -1000], [1000, 1000]]], 100)
    assert len(cross["polygons"]) == 1 and len(cross["polygons"][0]["outer"]) == 12
    assert math.isclose(cross["area"], 2 * 2000 * 100 - 100 * 100)

    # three walls ending at the same point, and a partition that makes two rooms
    star = build_walls([[[0, 0], [1000, 0]], [[0, 0], [0, 1000]], [[0, 0], [-1000, 0]]], 100)
    assert len(star["polygons"]) == 1 and math.isclose(star["area"], 2000 * 100 + 950 * 100)
    two = build_walls([ROOM, [[2000, 0], [2000, 3000]]], 150)
    assert len(two["polygons"]) == 1 and len(two["polygons"][0]["holes"]) == 2
    assert math.isclose(two["area"], 14000 * 150 + (3000 - 150) * 150)


def test_free_ends_are_square_and_not_longer():
    w = build_walls([[[0, 0], [1000, 0]]], 200)
    assert bbox(w["polygons"][0]["outer"]) == [0, -100, 1000, 100] and math.isclose(w["area"], 200000)
    skew = build_walls([[[0, 0], [300, 400]]], 50)
    assert math.isclose(skew["area"], 500 * 50) and math.isclose(skew["length"], 500)


def test_justification():
    inside = build_walls([ROOM], 150, "left")     # counter-clockwise: left is inside
    assert bbox(inside["polygons"][0]["outer"]) == [0, 0, 4000, 3000]
    assert bbox(inside["polygons"][0]["holes"][0]) == [150, 150, 3850, 2850]
    assert math.isclose(inside["area"], 4000 * 3000 - 3700 * 2700)
    outside = build_walls([ROOM], 150, "right")
    assert bbox(outside["polygons"][0]["outer"]) == [-150, -150, 4150, 3150]
    assert bbox(outside["polygons"][0]["holes"][0]) == [0, 0, 4000, 3000]
    clockwise = build_walls([ROOM[::-1]], 150, "right")  # same wall as "left" on the counter-clockwise ring
    assert math.isclose(clockwise["area"], inside["area"])
    line = build_walls([[[0, 0], [1000, 0]]], 100, "left")
    assert bbox(line["polygons"][0]["outer"]) == [0, 0, 1000, 100]
    bent = build_walls([[[0, 0], [1000, 0], [1000, 1000]]], 100, "left")
    assert bbox(bent["polygons"][0]["outer"]) == [0, 0, 1000, 1000] and math.isclose(bent["area"], 1900 * 100)
    assert centerlines([[[0, 0], [1000, 0]]], 100, "right")[0][0] == [(0.0, -50.0), (1000.0, -50.0)]
    # the middle line of a closed room keeps the direction and the first corner of the axis that was given
    assert centerlines([ROOM], 150, "right")[0][0] == [(-75.0, -75.0), (4075.0, -75.0), (4075.0, 3075.0), (-75.0, 3075.0)]
    assert centerlines([ROOM], 150, "left")[0][0] == [(75.0, 75.0), (3925.0, 75.0), (3925.0, 2925.0), (75.0, 2925.0)]
    assert centerlines([ROOM[::-1]], 150, "left")[0][0] == [(-75.0, -75.0), (-75.0, 3075.0), (4075.0, 3075.0), (4075.0, -75.0)]
    door = locate_opening(centerlines([ROOM], 150, "right"), [2000, -75], 900, 150)
    assert door["a"] == [1550, -75] and door["b"] == [2450, -75] and door["normal"] == [0, 1]


def test_openings_cut_the_wall():
    lines = centerlines([ROOM], 150)
    door = locate_opening(lines, [1000, 20], 900, 150)          # a point inside the wall, not exactly on the axis
    assert door["a"] == [550, 0] and door["b"] == [1450, 0] and door["normal"] == [0, 1]
    w = build_walls([ROOM], 150, openings=[(door["a"], door["b"])])
    assert math.isclose(w["area"], 14000 * 150 - 900 * 150)
    assert len(w["polygons"]) == 1 and w["polygons"][0]["holes"] == []  # the ring is open now: a single outline
    assert len(w["polygons"][0]["outer"]) == 12

    win = locate_opening(lines, [4000, 1500], 1200, 150)        # on the right wall, which runs upwards
    assert win["a"] == [4000, 900] and win["b"] == [4000, 2100] and win["normal"] == [-1, 0]
    w2 = build_walls([ROOM], 150, openings=[(door["a"], door["b"]), (win["a"], win["b"])])
    assert len(w2["polygons"]) == 2 and math.isclose(w2["area"], 14000 * 150 - 2100 * 150)

    assert overlaps_opening([900, 0], [1800, 0], 150, [(door["a"], door["b"])])
    assert not overlaps_opening([1450, 0], [2350, 0], 150, [(door["a"], door["b"])])  # touching is fine
    with pytest.raises(AcadError, match="does not fit"):
        locate_opening(lines, [200, 0], 900, 150)               # would run past the corner
    with pytest.raises(AcadError, match="away"):
        locate_opening(lines, [2000, 1500], 900, 150)           # middle of the room


def test_door_and_window_symbols():
    d = door_symbol([550, 0], [1450, 0], 150, hinge="start", swing="left")
    assert d["leaf"] == [[550, 75], [550, 975]]
    assert d["arc"] == {"center": [550, 75], "radius": 900, "start_angle_deg": 0, "end_angle_deg": 90}
    d = door_symbol([550, 0], [1450, 0], 150, hinge="end", swing="left")
    assert d["leaf"] == [[1450, 75], [1450, 975]] and (d["arc"]["start_angle_deg"], d["arc"]["end_angle_deg"]) == (90, 180)
    d = door_symbol([550, 0], [1450, 0], 150, hinge="start", swing="right")
    assert d["leaf"] == [[550, -75], [550, -975]] and (d["arc"]["start_angle_deg"], d["arc"]["end_angle_deg"]) == (270, 0)
    d = door_symbol([4000, 900], [4000, 1800], 150, hinge="end", swing="right")  # vertical wall, opens to +x
    assert d["leaf"] == [[4075, 1800], [4975, 1800]] and (d["arc"]["start_angle_deg"], d["arc"]["end_angle_deg"]) == (270, 0)
    for hinge in ("start", "end"):
        for swing in ("left", "right"):  # the arc is always a quarter turn, counter-clockwise from start to end
            arc = door_symbol([0, 0], [300, 400], 100, hinge, swing)["arc"]
            assert math.isclose((arc["end_angle_deg"] - arc["start_angle_deg"]) % 360, 90, abs_tol=1e-5)
            assert math.isclose(arc["radius"], 500)

    w = window_symbol([4000, 900], [4000, 2100], 150)
    assert w["glass"] == [[4000, 900], [4000, 2100]] and bbox(w["frame"]) == [3925, 900, 4075, 2100]


def test_wall_errors():
    for bad_axes in ([], [[[0, 0]]], [[[0, 0], [0, 0]]]):
        with pytest.raises(AcadError):
            build_walls(bad_axes, 100)
    with pytest.raises(AcadError):
        build_walls([ROOM], 0)
    with pytest.raises(AcadError):
        build_walls([ROOM], 100, "middle")
    with pytest.raises(AcadError, match="too thick"):
        build_walls([ROOM], 7000, "left")


# ------------------------------------------------------------------ pipes


def test_single_line_pipe():
    p = pipe_geometry([[0, 0], [1000, 0], [1000, 500]], 50)        # sharp corner by default
    assert p["outline"] is None and p["elbows"] == 1 and p["elbow_radius"] == 0
    assert p["centerline"] == {"points": [[0, 0], [1000, 0], [1000, 500]], "bulges": [0, 0, 0]}
    assert math.isclose(p["length"], 1500)

    p = pipe_geometry([[0, 0], [1000, 0], [1000, 500]], 50, elbow_radius=100)
    c = p["centerline"]
    assert c["points"] == [[0, 0], [900, 0], [1000, 100], [1000, 500]]
    assert math.isclose(c["bulges"][1], math.tan(math.pi / 8), abs_tol=1e-6) and c["bulges"][0] == c["bulges"][2] == 0
    assert math.isclose(p["length"], 900 + 400 + math.pi * 100 / 2)
    assert math.isclose(polyline_length(c["points"], c["bulges"]), p["length"], rel_tol=1e-6)

    right = pipe_geometry([[0, 0], [1000, 0], [1000, -500]], 50, elbow_radius=100)  # turning right: negative bulge
    assert right["centerline"]["bulges"][1] < 0 and math.isclose(right["length"], p["length"])
    straight = pipe_geometry([[0, 0], [500, 0], [1000, 0]], 50, elbow_radius=100)   # a point on a straight run
    assert straight["elbows"] == 0 and straight["centerline"]["points"] == [[0, 0], [1000, 0]]


def test_double_line_pipe():
    p = pipe_geometry([[0, 0], [1000, 0], [1000, 500]], 100, mode="double")       # radius = 1.5 x diameter
    assert p["elbow_radius"] == 150 and p["elbows"] == 1
    assert math.isclose(p["length"], 850 + 350 + math.pi * 150 / 2)
    o = p["outline"]
    assert o["points"] == [[0, 50], [850, 50], [950, 150], [950, 500],          # left wall (inner radius 100)
                           [1050, 500], [1050, 150], [850, -50], [0, -50]]      # right wall back (outer radius 200)
    inner, outer = math.pi * 100 / 2, math.pi * 200 / 2
    assert math.isclose(polyline_length(o["points"], o["bulges"], closed=True),
                        850 + inner + 350 + 100 + 350 + outer + 850 + 100, rel_tol=1e-6)
    assert o["bulges"][1] > 0 and o["bulges"][5] < 0 and math.isclose(o["bulges"][1], -o["bulges"][5])
    assert o["bulges"][3] == 0 and o["bulges"][7] == 0                          # the two square ends

    sharp = pipe_geometry([[0, 0], [1000, 0], [1000, 500]], 100, mode="double", elbow_radius=0)
    assert sharp["outline"]["points"] == [[0, 50], [950, 50], [950, 500], [1050, 500], [1050, -50], [0, -50]]
    assert set(sharp["outline"]["bulges"]) == {0} and math.isclose(sharp["length"], 1500)

    one = pipe_geometry([[0, 0], [0, 800]], 60, mode="double")
    assert one["outline"]["points"] == [[-30, 0], [-30, 800], [30, 800], [30, 0]] and one["elbows"] == 0


def test_pipe_errors():
    with pytest.raises(AcadError, match="does not fit"):
        pipe_geometry([[0, 0], [100, 0], [100, 100]], 50, elbow_radius=200)
    with pytest.raises(AcadError, match="does not fit"):   # two elbows sharing a run that is too short for both
        pipe_geometry([[0, 0], [1000, 0], [1000, 150], [2000, 150]], 50, elbow_radius=100)
    with pytest.raises(AcadError, match="half the diameter"):
        pipe_geometry([[0, 0], [1000, 0], [1000, 500]], 100, mode="double", elbow_radius=40)
    with pytest.raises(AcadError, match="turns back"):
        pipe_geometry([[0, 0], [1000, 0], [500, 0]], 50)
    for bad in ([[0, 0]], [[0, 0], [0, 0]]):
        with pytest.raises(AcadError):
            pipe_geometry(bad, 50)
    with pytest.raises(AcadError):
        pipe_geometry([[0, 0], [1, 0]], 0)
    with pytest.raises(AcadError):
        pipe_geometry([[0, 0], [1, 0]], 10, mode="triple")
