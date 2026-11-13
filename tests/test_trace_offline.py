"""Raster tracing accuracy on synthetic images (no AutoCAD needed)."""

import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from acad_mcp.tools.recreate import merge_segments, snap_ortho, to_drawing, trace, warp_perspective  # noqa: E402


def _length(s):
    return math.hypot(s[2] - s[0], s[3] - s[1])


def _sheet():
    img = np.full((600, 1000), 255, np.uint8)
    cv2.rectangle(img, (100, 100), (900, 500), 0, 4)  # 800 x 400 px rectangle, 4 px strokes
    cv2.line(img, (100, 500), (900, 100), 0, 3)  # diagonal
    return img


def test_rectangle_and_diagonal_traced_within_1_percent():
    segs, f = trace(_sheet())
    assert f == 1.0
    horiz = sorted((s for s in segs if abs(s[1] - s[3]) < 1), key=_length, reverse=True)[:2]
    vert = sorted((s for s in segs if abs(s[0] - s[2]) < 1), key=_length, reverse=True)[:2]
    diag = [s for s in segs if abs(s[1] - s[3]) > 50 and abs(s[0] - s[2]) > 50]
    assert len(horiz) == 2 and len(vert) == 2 and diag, segs
    for s in horiz:
        assert abs(_length(s) - 800) / 800 < 0.01, s
    for s in vert:
        assert abs(_length(s) - 400) / 400 < 0.01, s
    assert abs(max(map(_length, diag)) - math.hypot(800, 400)) / math.hypot(800, 400) < 0.02
    ys = sorted(round((s[1] + s[3]) / 2) for s in horiz)
    assert abs(ys[0] - 100) <= 2 and abs(ys[1] - 500) <= 2  # stroke centre lines, not edges


def test_dark_background_is_handled():
    segs, _ = trace(255 - _sheet())
    assert any(abs(_length(s) - 800) / 800 < 0.01 for s in segs)


def test_merge_and_snap():
    pieces = [(0, 0, 100, 0.5), (95, 0.2, 200, 1.0), (300, 0, 400, 0)]
    merged = merge_segments(pieces, gap=8)
    assert len(merged) == 2
    snapped = snap_ortho([(0, 0, 100, 1.5)])
    assert snapped[0][1] == snapped[0][3]


def test_scale_and_y_flip():
    lines = to_drawing([(0, 0, 100, 0)], img_h=50, scale=10, origin=[1000, 2000])
    assert lines == [[[1000, 2500], [2000, 2500]]]


def test_perspective_correction_restores_proportions():
    img = _sheet()
    src = np.float32([[0, 0], [999, 0], [999, 599], [0, 599]])
    skew = np.float32([[60, 30], [940, 0], [999, 599], [0, 560]])
    m = cv2.getPerspectiveTransform(src, skew)
    photo = cv2.warpPerspective(img, m, (1000, 600), borderValue=255)
    fixed = warp_perspective(photo, skew.tolist())
    segs, _ = trace(fixed)
    horiz = sorted((s for s in segs if abs(s[1] - s[3]) < 1), key=_length, reverse=True)[:2]
    vert = sorted((s for s in segs if abs(s[0] - s[2]) < 1), key=_length, reverse=True)[:2]
    ratio = np.mean([_length(s) for s in horiz]) / np.mean([_length(s) for s in vert])
    assert abs(ratio - 2.0) < 0.05, ratio
