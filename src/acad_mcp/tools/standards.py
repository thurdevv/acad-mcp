"""Standards: check and fix layers, styles, units and title block against a JSON standard."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import pywintypes
from pydantic import Field

from ..acad import commands as C
from ..acad.errors import AcadError
from ..acad.util import color_out, ensure_layer, parse_color, parse_lineweight, select, undo_group
from ..i18n import t
from .common import EDIT, RO, Ctx
from .layers import configure_layer
from .query import fetch

UNITS = {"none": 0, "inch": 1, "feet": 2, "mm": 4, "cm": 5, "m": 6}
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "standards"
USER_DIR = Path(os.environ.get("ACAD_MCP_STANDARDS_DIR", Path.home() / "Documents" / "AutoCAD MCP" / "standards"))

StdName = Annotated[str | None, Field(description=t("Name of a saved standard (see list_standards).",
                                                    "Nome de um padrão salvo (veja list_standards)."))]
StdPath = Annotated[str | None, Field(description=t("Path of a standard .json file.",
                                                    "Caminho de um arquivo .json de padrão."))]
StdInline = Annotated[dict[str, Any] | None, Field(description=t("Standard written inline (same format as the JSON).",
                                                                 "Padrão escrito direto (mesmo formato do JSON)."))]


def standard_files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for folder in (BUILTIN_DIR, USER_DIR):  # user folder wins on name clash
        if folder.is_dir():
            out.update({p.stem: p for p in folder.glob("*.json")})
    return out


def load_standard(name: str | None, path: str | None, inline: dict | None) -> dict[str, Any]:
    if inline:
        return inline
    if name:
        p = standard_files().get(name[:-5] if name.endswith(".json") else name)
        if p is None:
            raise AcadError(t("Standard '{n}' not found. Available: {a}", "Padrão '{n}' não encontrado. Disponíveis: {a}",
                              n=name, a=sorted(standard_files())))
    elif path:
        p = Path(path)
    else:
        raise AcadError(t("Give standard_name, standard_path or standard.",
                          "Informe standard_name, standard_path ou standard."))
    if not p.is_file():
        raise AcadError(t("Standard not found: {p}", "Padrão não encontrado: {p}", p=p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcadError(t("Invalid JSON in {p}: {e}", "JSON inválido em {p}: {e}", p=p, e=exc)) from None


def _norm_color(c: Any) -> Any:
    if c is None:
        return None
    if isinstance(c, str) and c.lower() in ("bylayer", "byblock"):
        return c.lower()
    kind, val = parse_color(c)
    return val if kind == "aci" else f"{val[0]},{val[1]},{val[2]}"


def check(ctx: Ctx, app: Any, doc: Any, std: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def add(code: str, msg: str, fixable: bool, sev: str = "warning", **extra: Any) -> None:
        issues.append({"code": code, "severity": sev, "message": msg, "auto_fix": fixable, **extra})

    if "units" in std:
        want = UNITS.get(str(std["units"]).lower())
        have = doc.GetVariable("INSUNITS")
        if want is not None and want != have:
            add("units", t("Drawing units are {h}; the standard wants {u} ({w}).",
                           "Unidade do desenho é {h}; o padrão pede {u} ({w}).", h=have, u=std["units"], w=want), True)

    ents = fetch(ctx, app, doc, mode="light", space="all")["entities"]
    by_layer = Counter(e["layer"] for e in ents)
    std_layers: dict[str, dict] = {k: (v or {}) for k, v in (std.get("layers") or {}).items()}
    existing = {}
    for i in range(doc.Layers.Count):
        ly = doc.Layers.Item(i)
        existing[ly.Name.upper()] = ly

    for name, props in std_layers.items():
        ly = existing.get(name.upper())
        if ly is None:
            add("layer_missing", t("Layer '{n}' does not exist.", "Layer '{n}' não existe.", n=name), True, layer=name)
            continue
        if "color" in props and _norm_color(props["color"]) != _norm_color(color_out(ly)):
            add("layer_color", t("Layer '{n}': color {h}, standard {w}.", "Layer '{n}': cor {h}, padrão {w}.",
                                 n=name, h=color_out(ly), w=props["color"]), True, layer=name)
        if "linetype" in props and str(props["linetype"]).upper() != ly.Linetype.upper():
            add("layer_linetype", t("Layer '{n}': linetype {h}, standard {w}.", "Layer '{n}': tipo de linha {h}, padrão {w}.",
                                    n=name, h=ly.Linetype, w=props["linetype"]), True, layer=name)
        if "lineweight" in props and parse_lineweight(props["lineweight"]) != ly.Lineweight:
            add("layer_lineweight", t("Layer '{n}': lineweight {h}, standard {w}.", "Layer '{n}': espessura {h}, padrão {w}.",
                                      n=name, h=ly.Lineweight, w=props["lineweight"]), True, layer=name)
        if "plottable" in props and bool(props["plottable"]) != bool(ly.Plottable):
            add("layer_plot", t("Layer '{n}': plottable={h}, standard {w}.", "Layer '{n}': plotável={h}, padrão {w}.",
                                n=name, h=bool(ly.Plottable), w=props["plottable"]), True, layer=name)

    if std.get("only_listed_layers"):
        allowed = {n.upper() for n in std_layers} | {"0", "DEFPOINTS"}
        for up, ly in existing.items():
            if up not in allowed:
                n_ents = by_layer.get(ly.Name, 0)
                add("layer_extra", t("Layer '{n}' is not in the standard ({c} entities).",
                                     "Layer '{n}' fora do padrão ({c} entidades).", n=ly.Name, c=n_ents),
                    False, layer=ly.Name, entities=n_ents)

    for name, props in (std.get("text_styles") or {}).items():
        try:
            st = doc.TextStyles.Item(name)
        except pywintypes.com_error:
            add("text_style_missing", t("Text style '{n}' does not exist.", "Estilo de texto '{n}' não existe.", n=name),
                True, style=name)
            continue
        props = props or {}
        if "font_file" in props and str(props["font_file"]).lower() != (st.fontFile or "").lower():
            add("text_style_font", t("Style '{n}': font {h}, standard {w}.", "Estilo '{n}': fonte {h}, padrão {w}.",
                                     n=name, h=st.fontFile, w=props["font_file"]), True, style=name)
        if "width_factor" in props and abs(float(props["width_factor"]) - st.Width) > 1e-6:
            add("text_style_width", t("Style '{n}': width {h}, standard {w}.", "Estilo '{n}': largura {h}, padrão {w}.",
                                      n=name, h=st.Width, w=props["width_factor"]), True, style=name)

    inserts = Counter(e.get("name") for e in ents if e["type"] == "BlockReference")
    for b in std.get("required_blocks") or []:
        if inserts.get(b, 0) == 0:
            add("block_missing", t("Required block '{b}' is not inserted.", "Bloco obrigatório '{b}' não está inserido.", b=b),
                False, "error", block=b)

    tb = std.get("title_block")
    if tb and tb.get("block"):
        refs = fetch(ctx, app, doc, mode="full", types=["INSERT"], space="all", block_name=tb["block"])["entities"]
        if not refs:
            add("title_block_missing", t("Title block '{b}' not found.", "Carimbo '{b}' não encontrado.", b=tb["block"]),
                False, "error")
        for r in refs:
            atts = {k.upper(): v for k, v in (r.get("attributes") or {}).items()}
            for tag in tb.get("required_attributes") or []:
                if not (atts.get(tag.upper()) or "").strip():
                    add("title_block_empty", t("Title block {h}: attribute '{a}' empty or missing.",
                                               "Carimbo {h}: atributo '{a}' vazio ou ausente.", h=r["handle"], a=tag),
                        False, "error", handle=r["handle"], tag=tag)

    if std.get("forbid_entities_on_layer_0") and by_layer.get("0", 0):
        handles = [e["handle"] for e in ents if e["layer"] == "0"][:200]
        add("entities_on_layer_0", t("{c} entity(ies) on layer 0.", "{c} entidade(s) no layer 0.", c=by_layer["0"]),
            False, handles=handles)

    if std.get("forbid_color_override"):
        bad = [e for e in ents if e.get("color") not in ("ByLayer", None)]
        if bad:
            add("color_override", t("{c} entity(ies) with their own color (not ByLayer).",
                                    "{c} entidade(s) com cor própria (não ByLayer).", c=len(bad)),
                True, handles=[e["handle"] for e in bad][:200])
    return issues


def apply(ctx: Ctx, app: Any, doc: Any, std: dict[str, Any], reset_color_overrides: bool = False,
          move_layer0_to: str | None = None) -> dict[str, Any]:
    """Apply the auto-fixable part of a standard to `doc` (one undo step). Used by apply_standards and batch."""
    done: list[str] = []
    with undo_group(doc):
        if "units" in std and str(std["units"]).lower() in UNITS:
            want = UNITS[str(std["units"]).lower()]
            if doc.GetVariable("INSUNITS") != want:
                doc.SetVariable("INSUNITS", want)
                done.append(t("units -> {u}", "unidade -> {u}", u=std["units"]))
        for name, props in (std.get("layers") or {}).items():
            props = props or {}
            created = ensure_layer(doc, name)
            ly = doc.Layers.Item(name)
            configure_layer(app, doc, ly, props.get("color"), props.get("linetype"), props.get("lineweight"),
                            None, None, None, props.get("plottable"), props.get("description"))
            done.append(t("layer {n} {s}", "layer {n} {s}", n=name,
                          s=t("created", "criado") if created else t("checked", "conferido")))
        for name, props in (std.get("text_styles") or {}).items():
            props = props or {}
            try:
                st = doc.TextStyles.Item(name)
            except pywintypes.com_error:
                st = doc.TextStyles.Add(name)
                done.append(t("style {n} created", "estilo {n} criado", n=name))
            if props.get("font_file"):
                st.fontFile = props["font_file"]
            if props.get("width_factor"):
                st.Width = float(props["width_factor"])
        if move_layer0_to:
            ensure_layer(doc, move_layer0_to)
            n = 0
            for e in select(doc, layers=["0"], space="all"):
                e.Layer = move_layer0_to
                n += 1
            done.append(t("{c} entity(ies) from layer 0 -> {l}", "{c} entidade(s) do layer 0 -> {l}",
                          c=n, l=move_layer0_to))
        if reset_color_overrides:
            n = 0
            for e in select(doc, space="all"):
                if color_out(e) not in ("ByLayer", None):
                    tc = e.TrueColor
                    tc.ColorIndex = 256
                    e.TrueColor = tc
                    n += 1
            done.append(t("{c} entity(ies) color -> ByLayer", "{c} entidade(s) com cor -> ByLayer", c=n))
    return {"actions": done, "remaining_issues": check(ctx, app, doc, std)}


def register(ctx: Ctx) -> None:
    @ctx.tool(RO, title=t("List standards", "Listar padrões"), pt=(
        "Lista os padrões de desenho (.json) disponíveis — os que vêm com o servidor e os do usuário em "
        "Documentos\\AutoCAD MCP\\standards — e o formato esperado."))
    def list_standards() -> dict[str, Any]:
        """Lists the drawing standards (.json) available — the ones shipped with the server and the user's in
        Documents\\AutoCAD MCP\\standards — and the expected format."""
        return {
            "user_folder": str(USER_DIR),
            "standards": {k: str(v) for k, v in sorted(standard_files().items())},
            "format": {
                "units": "mm | cm | m | inch | feet",
                "layers": {"NAME": {"color": "ACI or 'R,G,B'", "linetype": "Continuous", "lineweight": 0.35,
                                    "plottable": True, "description": "text"}},
                "only_listed_layers": "true = report layers outside the list",
                "text_styles": {"NAME": {"font_file": "romans.shx", "width_factor": 1.0}},
                "required_blocks": ["BLOCK_NAME"],
                "title_block": {"block": "TITLEBLOCK", "required_attributes": ["TITLE", "NUMBER", "REV"]},
                "forbid_entities_on_layer_0": True,
                "forbid_color_override": False,
            },
        }

    @ctx.tool(RO, title=t("Check standard", "Conferir padrão"), pt=(
        "Confere o desenho ativo contra um padrão: unidades, layers (existência, cor, tipo de linha, espessura), layers "
        "fora do padrão, estilos de texto, blocos obrigatórios, carimbo preenchido, entidades no layer 0 e cores fora "
        "de ByLayer. Devolve os problemas e diz quais apply_standards corrige sozinho."))
    def check_standards(standard_name: StdName = None, standard_path: StdPath = None,
                        standard: StdInline = None) -> dict[str, Any]:
        """Checks the active drawing against a standard: units, layers (existence, color, linetype, lineweight),
        layers outside the standard, text styles, required blocks, filled-in title block, entities on layer 0 and
        non-ByLayer colors. Returns the issues and which ones apply_standards fixes automatically."""
        std = load_standard(standard_name, standard_path, standard)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            issues = check(ctx, app, doc, std)
            return {"standard": std.get("name", standard_name or standard_path or "inline"), "ok": not issues,
                    "issue_count": len(issues), "auto_fixable": sum(1 for i in issues if i["auto_fix"]),
                    "issues": issues}

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Apply standard", "Aplicar padrão"), pt=(
        "Corrige o que dá automaticamente: cria layers que faltam e acerta cor/tipo de linha/espessura/plot, cria e "
        "acerta estilos de texto e unidade. Não apaga nada. Tudo em 1 passo de desfazer. Rode check_standards antes "
        "e depois."))
    def apply_standards(
        standard_name: StdName = None,
        standard_path: StdPath = None,
        standard: StdInline = None,
        reset_color_overrides: Annotated[bool, Field(description=t(
            "Also set ByLayer on entities with their own color (if the standard forbids it).",
            "Também pôr ByLayer nas entidades com cor própria (se o padrão proibir)."))] = False,
        move_layer0_to: Annotated[str | None, Field(description=t("Move entities on layer 0 to this layer.",
                                                                  "Mover entidades do layer 0 para este layer."))] = None,
    ) -> dict[str, Any]:
        """Fixes what can be fixed automatically: creates missing layers and sets color/linetype/lineweight/plot,
        creates and fixes text styles and units. Erases nothing. All in one undo step. Run check_standards before
        and after."""
        std = load_standard(standard_name, standard_path, standard)
        return ctx.doc(lambda app, doc: apply(ctx, app, doc, std, reset_color_overrides, move_layer0_to))

    @ctx.tool(EDIT, title=t("Purge and audit", "Limpar (purge) e auditar"), pt=(
        "Limpa itens não usados (PURGE) e/ou verifica e corrige a integridade do desenho (AUDIT)."))
    def purge_and_audit(
        purge: Annotated[bool, Field(description=t("Remove unused layers, blocks, styles and linetypes.",
                                                   "Remover layers, blocos, estilos e tipos de linha não usados."))] = True,
        audit: Annotated[bool, Field(description=t("Run AUDIT fixing errors (needs the window not minimized).",
                                                   "Rodar AUDIT corrigindo erros (requer janela não minimizada)."))] = False,
    ) -> dict[str, Any]:
        """Removes unused items (PURGE) and/or checks and fixes drawing integrity (AUDIT)."""

        def counts(doc: Any) -> dict[str, int]:
            return {"layers": doc.Layers.Count, "blocks": doc.Blocks.Count, "text_styles": doc.TextStyles.Count,
                    "linetypes": doc.Linetypes.Count, "dim_styles": doc.DimStyles.Count}

        def f(app: Any, doc: Any) -> dict[str, Any]:
            before = counts(doc)
            out: dict[str, Any] = {"before": before}
            if purge:
                for _ in range(3):  # nested items only go away on later passes
                    doc.PurgeAll()
            if audit:
                out["audit"] = C.send(app, doc, "_.AUDIT _Y\n", ctx.cfg.command_timeout)
                try:
                    out["audit"]["last_prompt"] = doc.GetVariable("LASTPROMPT")
                except pywintypes.com_error:
                    pass
            out["after"] = counts(doc)
            out["removed"] = {k: before[k] - out["after"][k] for k in before}
            return out

        return ctx.doc(f)
