"""Data extraction: quantities and block/attribute report, with CSV output. Fast on large drawings."""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from typing import Annotated, Any, Literal

from pydantic import Field

from ..acad.errors import AcadError
from ..acad.util import rnd
from ..i18n import LANG, t
from .common import RO, Ctx, Pt
from .query import fetch

CsvPath = Annotated[
    str | None,
    Field(description=t(
        "If given, writes a NEW CSV at this path. Never overwrites an existing file.",
        "Se informado, grava um CSV NOVO neste caminho. Nunca sobrescreve arquivo existente.")),
]
CsvFormat = Annotated[
    Literal["auto", "excel-comma-decimal", "standard"],
    Field(description=t(
        "'standard' = ',' separator and '.' decimals; 'excel-comma-decimal' = ';' and ',' (Excel in pt-BR, "
        "de, fr...); 'auto' = by server language.",
        "'standard' = separador ',' e decimal '.'; 'excel-comma-decimal' = ';' e ',' (Excel em português); "
        "'auto' = conforme o idioma do servidor.")),
]
CLOSED_AREA_TYPES = {"Circle", "Hatch", "Region", "Ellipse"}


def write_csv(path: str, header: list[str], rows: list[list[Any]], read_only: bool, fmt: str = "auto") -> str:
    if read_only:
        raise AcadError(t("Server is read-only: it does not write files. Run without csv_path.",
                          "Servidor em modo somente leitura: não grava arquivos. Rode sem csv_path."))
    full = os.path.abspath(path)
    if not full.lower().endswith(".csv"):
        full += ".csv"
    folder = os.path.dirname(full)
    if not os.path.isdir(folder):
        raise AcadError(t("Folder does not exist: {p}", "Pasta não existe: {p}", p=folder))
    if os.path.exists(full):
        raise AcadError(t("{p} already exists. Pick another name (I never overwrite files).",
                          "Já existe {p}. Escolha outro nome (não sobrescrevo arquivos).", p=full))
    comma = fmt == "excel-comma-decimal" or (fmt == "auto" and LANG == "pt")

    def cell(v: Any) -> Any:
        if isinstance(v, float):
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            return s.replace(".", ",") if comma else s
        return v

    with open(full, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";" if comma else ",")
        w.writerow(header)
        for r in rows:
            w.writerow([cell(v) for v in r])
    return full


def length_of(e: dict) -> float:
    return float(e.get("length") or e.get("circumference") or 0.0)


def area_of(e: dict) -> float:
    if e["type"] == "Polyline" and not e.get("closed"):
        return 0.0
    if e["type"] in CLOSED_AREA_TYPES or e["type"] in ("Polyline", "2dPolyline"):
        return float(e.get("area") or 0.0)
    return 0.0


def register(ctx: Ctx) -> None:
    @ctx.tool(RO, title=t("Quantities", "Quantitativos"), pt=(
        "Contagens e somas por grupo: quantidade, comprimento total (linhas, polilinhas, arcos, círculos) e área "
        "total (polilinhas fechadas, círculos, hachuras). Base para quantitativos e listas de material. Valores nas "
        "unidades do desenho. Rápido mesmo em desenhos grandes."))
    def quantities(
        group_by: Annotated[Literal["layer", "type", "layer_type", "block", "color"],
                            Field(description=t("How to group.", "Como agrupar."))] = "layer_type",
        types: Annotated[list[str] | None, Field(description=t("Filter types (line, polyline, circle, block, hatch...).",
                                                               "Filtrar tipos (line, polyline, circle, block, hatch...)."))] = None,
        layers: Annotated[list[str] | None, Field(description=t("Filter layers (wildcards allowed).",
                                                                "Filtrar layers (curingas aceitos)."))] = None,
        block_name: str | None = None,
        within: Annotated[list[Pt] | None, Field(description=t("Only inside region [[x1,y1],[x2,y2]].",
                                                               "Só dentro da região [[x1,y1],[x2,y2]]."))] = None,
        space: Literal["model", "paper", "all"] = "model",
        layout: Annotated[str | None, Field(description=t(
            "Only this layout (sheet), by name (wildcards allowed); replaces space. 'Model' = model space.",
            "Só esta folha (layout), pelo nome (aceita curingas); substitui space. 'Model' = espaço modelo."))] = None,
        csv_path: CsvPath = None,
        csv_format: CsvFormat = "auto",
    ) -> dict[str, Any]:
        """Counts and sums per group: count, total length (lines, polylines, arcs, circles) and total area (closed
        polylines, circles, hatches). Basis for quantity take-offs and bills of materials. Values in drawing units.
        Fast even on large drawings."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            r = fetch(ctx, app, doc, mode="full", types=types, layers=layers, space=space, block_name=block_name,
                      within=within, layout=layout)
            groups: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "length": 0.0, "area": 0.0})
            for e in r["entities"]:
                typ = e["type"]
                if group_by == "layer":
                    key = e["layer"]
                elif group_by == "type":
                    key = typ
                elif group_by == "block":
                    if typ != "BlockReference":
                        continue
                    key = e.get("name") or "?"
                elif group_by == "color":
                    key = str(e.get("color"))
                else:
                    key = f"{e['layer']} | {typ}"
                g = groups[key]
                g["count"] += 1
                g["length"] += length_of(e)
                g["area"] += area_of(e)
            rows = [{"group": k, "count": int(v["count"]), "total_length": rnd(v["length"], 4),
                     "total_area": rnd(v["area"], 4)} for k, v in sorted(groups.items())]
            out: dict[str, Any] = {
                "group_by": group_by,
                "entities_considered": r["total"],
                "engine": r["engine"],
                "seconds": r["seconds"],
                "rows": rows,
                "totals": {
                    "count": sum(x["count"] for x in rows),
                    "total_length": rnd(sum(x["total_length"] for x in rows), 4),
                    "total_area": rnd(sum(x["total_area"] for x in rows), 4),
                },
            }
            if csv_path:
                out["csv"] = write_csv(
                    csv_path,
                    [t("group", "grupo"), t("count", "quantidade"), t("total_length", "comprimento_total"),
                     t("total_area", "area_total")],
                    [[x["group"], x["count"], x["total_length"], x["total_area"]] for x in rows],
                    ctx.cfg.read_only, csv_format,
                )
            return out

        return ctx.doc(f)

    @ctx.tool(RO, title=t("Block and attribute report", "Relatório de blocos e atributos"), pt=(
        "Lista cada bloco inserido com nome, layer, posição, rotação, escala e TODOS os atributos (tags de "
        "equipamentos, carimbos, numeração). Inclui resumo por nome. Pode gravar CSV. Rápido em desenhos grandes."))
    def block_report(
        block_name: Annotated[str | None, Field(description=t("Only these blocks (wildcards and commas). Empty = all.",
                                                              "Só estes blocos (curingas e vírgula). Vazio = todos."))] = None,
        layers: list[str] | None = None,
        space: Literal["model", "paper", "all"] = "all",
        layout: Annotated[str | None, Field(description=t(
            "Only this layout (sheet), by name (wildcards allowed); replaces space. 'Model' = model space.",
            "Só esta folha (layout), pelo nome (aceita curingas); substitui space. 'Model' = espaço modelo."))] = None,
        limit: Annotated[int, Field(ge=1, le=50000)] = 2000,
        csv_path: CsvPath = None,
        csv_format: CsvFormat = "auto",
    ) -> dict[str, Any]:
        """Lists every inserted block with name, layer, position, rotation, scale and ALL attributes (equipment
        tags, title blocks, numbering). Includes a summary by name. Can write a CSV. Fast on large drawings."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            r = fetch(ctx, app, doc, mode="full", types=["INSERT"], layers=layers, space=space,
                      block_name=block_name or "*", layout=layout)
            items = []
            tags_all: list[str] = []
            for e in r["entities"][:limit]:
                atts = e.get("attributes") or {}
                for tag in atts:
                    if tag not in tags_all:
                        tags_all.append(tag)
                sc = e.get("scale")
                items.append({
                    "handle": e["handle"], "name": e.get("name"), "layer": e["layer"],
                    "insertion": e.get("insertion"), "rotation_deg": e.get("rotation_deg"),
                    "scale": sc[0] if isinstance(sc, list) else sc, "attributes": atts,
                })
            summary: dict[str, int] = defaultdict(int)
            for e in r["entities"]:
                summary[e.get("name") or "?"] += 1
            out: dict[str, Any] = {
                "total_found": r["total"], "returned": len(items), "engine": r["engine"], "seconds": r["seconds"],
                "summary_by_name": dict(sorted(summary.items())), "blocks": items,
            }
            if csv_path:
                header = ["handle", t("block", "bloco"), "layer", "x", "y", t("rotation", "rotacao"),
                          t("scale", "escala")] + tags_all
                rows = []
                for it in items:
                    p = it["insertion"] or [None, None]
                    rows.append([it["handle"], it["name"], it["layer"], p[0], p[1], it["rotation_deg"], it["scale"]]
                                + [it["attributes"].get(tag, "") for tag in tags_all])
                out["csv"] = write_csv(csv_path, header, rows, ctx.cfg.read_only, csv_format)
            return out

        return ctx.doc(f)
