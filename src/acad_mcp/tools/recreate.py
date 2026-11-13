"""Recreate drawings from images: raster tracing (scans, photos, catalog pictures) and a guided prompt.

Vector PDFs from CAD go through import_file (action import_pdf) with real measurements. This module is
for pictures: it finds straight strokes with OpenCV and draws them as LINE entities on a TRACE layer, as a
DRAFT to be checked and cleaned. Clean line drawings trace well; skewed photos need the 4 corners for
perspective correction.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from ..acad.errors import AcadError
from ..acad.util import P, apply_props, target_space, undo_group
from ..i18n import LANG, t
from .common import ADD, Ctx, Pt
from .files import IMG_EXT, receive, validate

MAX_SIDE = 2400  # px; larger images are downscaled (scale is corrected accordingly)

Segment = tuple[float, float, float, float]


# ------------------------------------------------------------------ pure image functions (tested offline)


def load_gray(path: str | Path) -> np.ndarray:
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)  # works with non-ASCII paths
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise AcadError(t("Could not read the image.", "Não consegui ler a imagem."))
    return img


def warp_perspective(gray: np.ndarray, corners: list[list[float]]) -> np.ndarray:
    """corners in pixels: top-left, top-right, bottom-right, bottom-left."""
    import cv2

    if len(corners) != 4:
        raise AcadError(t("perspective_corners needs 4 points: TL, TR, BR, BL.",
                          "perspective_corners precisa de 4 pontos: TL, TR, BR, BL."))
    tl, tr, br, bl = [np.array(c[:2], dtype=np.float32) for c in corners]
    w = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    h = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    src = np.array([tl, tr, br, bl], dtype=np.float32)
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)


def binarize(gray: np.ndarray) -> np.ndarray:
    """Strokes become white (255) on black, whatever the background."""
    import cv2

    if float(gray.mean()) < 127:  # dark background (e.g. screenshot of AutoCAD): invert first
        gray = 255 - gray
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    b = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 10)
    return cv2.morphologyEx(b, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def skeleton(binary: np.ndarray) -> np.ndarray:
    """Morphological skeleton: thick strokes -> 1 px centre lines (so each stroke gives one line, not two)."""
    import cv2

    img = binary.copy()
    skel = np.zeros_like(img)
    el = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(200):
        eroded = cv2.erode(img, el)
        opened = cv2.dilate(eroded, el)
        skel = cv2.bitwise_or(skel, cv2.subtract(img, opened))
        img = eroded
        if cv2.countNonZero(img) == 0:
            break
    return cv2.dilate(skel, np.ones((2, 2), np.uint8))  # reconnect the 1 px gaps the skeleton leaves


def detect_segments(skel: np.ndarray, min_len_px: float, gap_px: float) -> list[Segment]:
    import cv2

    lines = cv2.HoughLinesP(skel, 1, math.pi / 360, threshold=max(10, int(min_len_px / 2)),
                            minLineLength=min_len_px, maxLineGap=gap_px)
    if lines is None:
        return []
    # OpenCV 4 returns shape (N, 1, 4); OpenCV 5 returns (N, 4)
    return [tuple(map(float, row)) for row in np.asarray(lines).reshape(-1, 4)]


def _angle(s: Segment) -> float:
    return math.degrees(math.atan2(s[3] - s[1], s[2] - s[0])) % 180.0


def merge_segments(segs: list[Segment], angle_tol: float = 2.0, dist_tol: float = 4.0,
                   gap: float = 8.0) -> list[Segment]:
    """Merge segments that lie on the same line (Hough returns many overlapping pieces)."""
    remaining = sorted(segs, key=lambda s: -math.hypot(s[2] - s[0], s[3] - s[1]))
    out: list[Segment] = []
    used = [False] * len(remaining)
    for i, s in enumerate(remaining):
        if used[i]:
            continue
        used[i] = True
        a = math.radians(_angle(s))
        d = (math.cos(a), math.sin(a))
        nrm = (-d[1], d[0])
        ox, oy = s[0], s[1]
        ts = [0.0, (s[2] - ox) * d[0] + (s[3] - oy) * d[1]]
        intervals = [(min(ts), max(ts))]
        for j in range(i + 1, len(remaining)):
            if used[j]:
                continue
            o = remaining[j]
            da = abs(_angle(o) - _angle(s))
            if min(da, 180 - da) > angle_tol:
                continue
            if max(abs((o[0] - ox) * nrm[0] + (o[1] - oy) * nrm[1]),
                   abs((o[2] - ox) * nrm[0] + (o[3] - oy) * nrm[1])) > dist_tol:
                continue
            t1 = (o[0] - ox) * d[0] + (o[1] - oy) * d[1]
            t2 = (o[2] - ox) * d[0] + (o[3] - oy) * d[1]
            intervals.append((min(t1, t2), max(t1, t2)))
            used[j] = True
        intervals.sort()
        cur_a, cur_b = intervals[0]
        for ia, ib in intervals[1:]:
            if ia <= cur_b + gap:
                cur_b = max(cur_b, ib)
            else:
                out.append((ox + d[0] * cur_a, oy + d[1] * cur_a, ox + d[0] * cur_b, oy + d[1] * cur_b))
                cur_a, cur_b = ia, ib
        out.append((ox + d[0] * cur_a, oy + d[1] * cur_a, ox + d[0] * cur_b, oy + d[1] * cur_b))
    return out


def snap_ortho(segs: list[Segment], tol_deg: float = 1.5) -> list[Segment]:
    out = []
    for x1, y1, x2, y2 in segs:
        a = _angle((x1, y1, x2, y2))
        if min(a, 180 - a) <= tol_deg:  # horizontal
            y = (y1 + y2) / 2
            out.append((x1, y, x2, y))
        elif abs(a - 90) <= tol_deg:  # vertical
            x = (x1 + x2) / 2
            out.append((x, y1, x, y2))
        else:
            out.append((x1, y1, x2, y2))
    return out


def trace(gray: np.ndarray, min_len_px: float = 25, gap_px: float = 6, ortho: bool = True) -> tuple[list[Segment], float]:
    """Full pipeline. Returns (segments in px of the possibly downscaled image, downscale factor)."""
    import cv2

    f = 1.0
    if max(gray.shape) > MAX_SIDE:
        f = MAX_SIDE / max(gray.shape)
        gray = cv2.resize(gray, (int(gray.shape[1] * f), int(gray.shape[0] * f)), interpolation=cv2.INTER_AREA)
    segs = detect_segments(skeleton(binarize(gray)), min_len_px * f, gap_px * f)
    segs = merge_segments(segs, dist_tol=max(3.0, 4 * f), gap=max(gap_px * f, 4.0))
    if ortho:
        segs = snap_ortho(segs)
    return segs, f


def to_drawing(segs: list[Segment], img_h: float, scale: float, origin: list[float]) -> list[list[list[float]]]:
    """Pixel segments (y down) -> drawing coordinates (y up), lower-left of the image at origin."""
    ox, oy = origin[0], origin[1]
    return [[[ox + x1 * scale, oy + (img_h - y1) * scale], [ox + x2 * scale, oy + (img_h - y2) * scale]]
            for x1, y1, x2, y2 in segs]


# ------------------------------------------------------------------ tools and prompt


PROMPT_EN = """Recreate the drawing I attached directly in AutoCAD, step by step:
1. Describe what the drawing is and list EVERY dimension you can read in a table (element, value, unit). Mark
   values you are inferring from proportions as "estimated".
2. Ask me for any dimension that is missing or unreadable before drawing. Do not invent measurements.
3. If it is a vector PDF from CAD, use import_file (import_pdf) instead of redrawing. If it is a scan or photo
   saved on my PC, you may run trace_image first as a draft and then clean it.
4. Draw it with draw_batch (layers for outlines, axes, dimensions, text), then add dimensions.
5. Check with get_view_image(grid=true), compare with the original and fix differences.
6. Finish with a short list of what was verified and what is still an assumption.
{extra}"""

PROMPT_PT = """Recrie no AutoCAD o desenho que anexei, passo a passo:
1. Diga o que é o desenho e liste TODAS as medidas que conseguir ler numa tabela (elemento, valor, unidade).
   Marque como "estimado" o que estiver deduzindo pela proporção.
2. Pergunte as medidas que faltam ou estão ilegíveis antes de desenhar. Não invente medidas.
3. Se for um PDF vetorial de CAD, use import_file (import_pdf) em vez de redesenhar. Se for um scan ou foto salvo
   no meu PC, pode rodar trace_image antes como rascunho e depois limpar.
4. Desenhe com draw_batch (layers para contorno, eixos, cotas, texto) e depois cote.
5. Confira com get_view_image(grid=true), compare com o original e corrija as diferenças.
6. Termine com uma lista curta do que foi conferido e do que ainda é suposição.
{extra}"""


def register(ctx: Ctx) -> None:
    @ctx.tool(ADD, title=t("Trace image (raster to lines)", "Rastrear imagem (raster para linhas)"), pt=(
        "Transforma os traços retos de uma imagem (scan, foto, desenho de catálogo) em LINHAS no AutoCAD, num layer "
        "TRACE, como RASCUNHO para conferir e limpar. Escala pela largura real (real_width) ou por dois pontos "
        "conhecidos (calibration). Foto torta: informe os 4 cantos (perspective_corners). Para PDF vetorial de CAD "
        "use import_file, que é exato."))
    def trace_image(
        source_path: Annotated[str | None, Field(description=t("Image already on this PC.", "Imagem que já está neste PC."))] = None,
        content_base64: Annotated[str | None, Field(description=t("OR the image in base64 (needs filename).",
                                                                  "OU a imagem em base64 (precisa de filename)."))] = None,
        filename: str | None = None,
        real_width: Annotated[float | None, Field(gt=0, description=t(
            "Real width of the (corrected) image in drawing units.",
            "Largura real da imagem (corrigida) em unidades do desenho."))] = None,
        calibration: Annotated[dict[str, Any] | None, Field(description=t(
            "Two pixel points and their real distance: {'p1': [x, y], 'p2': [x, y], 'distance': 1000}.",
            "Dois pontos em pixels e a distância real: {'p1': [x, y], 'p2': [x, y], 'distance': 1000}."))] = None,
        perspective_corners: Annotated[list[list[float]] | None, Field(description=t(
            "Pixel corners of the sheet in a skewed photo: TL, TR, BR, BL.",
            "Cantos da folha em pixels numa foto torta: TL, TR, BR, BL."))] = None,
        insertion: Annotated[Pt, Field(description=t("Where the image's lower-left corner goes.",
                                                     "Onde fica o canto inferior esquerdo da imagem."))] = [0.0, 0.0],
        min_length_px: Annotated[float, Field(ge=3, description=t("Ignore strokes shorter than this (pixels).",
                                                                  "Ignorar traços menores que isto (pixels)."))] = 25,
        snap_orthogonal: Annotated[bool, Field(description=t("Straighten almost horizontal/vertical lines.",
                                                             "Endireitar linhas quase horizontais/verticais."))] = True,
        layer: str = "TRACE",
        dry_run: Annotated[bool, Field(description=t("Only report what would be drawn.",
                                                     "Só informar o que seria desenhado."))] = False,
    ) -> dict[str, Any]:
        """Turns the straight strokes of an image (scan, photo, catalog drawing) into LINES in AutoCAD on a TRACE
        layer, as a DRAFT to check and clean. Scale from the real width (real_width) or two known points
        (calibration). Skewed photo: give the 4 corners (perspective_corners). For vector PDFs from CAD use
        import_file, which is exact."""
        path = receive(filename, None, content_base64, source_path)
        if path.suffix.lower() not in IMG_EXT:
            raise AcadError(t("trace_image needs an image (.png, .jpg, .bmp, .tif). For PDF use import_file.",
                              "trace_image precisa de imagem (.png, .jpg, .bmp, .tif). Para PDF use import_file."))
        validate(path)
        gray = load_gray(path)
        if perspective_corners:
            gray = warp_perspective(gray, perspective_corners)
        h_px, w_px = gray.shape[:2]
        segs, f = trace(gray, min_length_px, 6, snap_orthogonal)
        h_work = h_px * f
        if calibration:
            try:
                (x1, y1), (x2, y2), dist = calibration["p1"][:2], calibration["p2"][:2], float(calibration["distance"])
            except (KeyError, TypeError, ValueError):
                raise AcadError(t("calibration = {'p1': [x, y], 'p2': [x, y], 'distance': value}",
                                  "calibration = {'p1': [x, y], 'p2': [x, y], 'distance': valor}")) from None
            px = math.hypot(x2 - x1, y2 - y1)
            if px <= 0:
                raise AcadError(t("Calibration points must differ.", "Os pontos de calibração precisam ser diferentes."))
            scale = dist / px / f  # per working pixel
        elif real_width:
            scale = real_width / w_px / f
        else:
            scale = 1.0 / f
        lines = to_drawing(segs, h_work, scale, insertion)
        out: dict[str, Any] = {
            "file": str(path), "image_px": [w_px, h_px], "segments": len(lines),
            "scale_units_per_image_px": round(scale * f, 6),
            "note": t("Draft: check against the image (get_view_image grid=true) and fix; curves and text are not "
                      "traced." + ("" if (real_width or calibration) else " No scale given: 1 unit = 1 pixel."),
                      "Rascunho: confira com a imagem (get_view_image grid=true) e corrija; curvas e textos não são "
                      "rastreados." + ("" if (real_width or calibration) else " Sem escala: 1 unidade = 1 pixel.")),
        }
        if dry_run or not lines:
            out["preview"] = lines[:50]
            return out

        def f_draw(app: Any, doc: Any) -> dict[str, Any]:
            sp = target_space(doc, "model")
            handles = []
            with undo_group(doc):
                for a, b in lines:
                    e = sp.AddLine(P(a), P(b))
                    apply_props(app, doc, e, layer)
                    handles.append(e.Handle)
            xs = [p[0] for ln in lines for p in ln]
            ys = [p[1] for ln in lines for p in ln]
            out.update(handles=handles[:200], extents=[[min(xs), min(ys)], [max(xs), max(ys)]])
            return out

        return ctx.doc(f_draw)

    @ctx.mcp.prompt(name="recreate_drawing", title=t("Recreate a drawing", "Recriar um desenho"),
                    description=t("Step-by-step guide to recreate an attached drawing in AutoCAD (measurements first).",
                                  "Roteiro para recriar no AutoCAD um desenho anexado (medidas primeiro)."))
    def recreate_drawing(notes: str = "") -> str:
        extra = (t("Extra notes: ", "Observações: ") + notes) if notes else ""
        return (PROMPT_PT if LANG == "pt" else PROMPT_EN).format(extra=extra)
