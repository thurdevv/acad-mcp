"""Open, create, activate, save and close drawings."""

from __future__ import annotations

import os
import time
from typing import Annotated, Any

from pydantic import Field

from ..acad.errors import AcadError
from ..acad.util import active_doc
from ..i18n import t
from .common import ADD, EDIT, RO, Ctx

INSUNITS = {
    0: t("unitless", "sem unidade"), 1: t("inches", "polegadas"), 2: t("feet", "pés"), 3: t("miles", "milhas"),
    4: t("millimeters", "milímetros"), 5: t("centimeters", "centímetros"), 6: t("meters", "metros"),
    7: t("kilometers", "quilômetros"), 14: t("decimeters", "decímetros"),
}
WINDOW = {1: "normal", 2: "minimized", 3: "maximized"}


def units_of(doc: Any) -> Any:
    v = doc.GetVariable("INSUNITS")
    return INSUNITS.get(v, v)


def doc_summary(doc: Any, active_name: str | None = None) -> dict[str, Any]:
    return {
        "name": doc.Name,
        "path": doc.FullName or None,
        "unsaved_changes": not bool(doc.Saved),
        "read_only": bool(doc.ReadOnly),
        "active": doc.Name == active_name,
    }


def find_doc(app: Any, name: str) -> Any:
    target = name.strip().lower()
    for i in range(app.Documents.Count):
        d = app.Documents.Item(i)
        if d.Name.lower() == target or (d.FullName or "").lower() == target:
            return d
    names = [app.Documents.Item(i).Name for i in range(app.Documents.Count)]
    raise AcadError(t("Drawing '{n}' is not open. Open: {o}", "Desenho '{n}' não está aberto. Abertos: {o}",
                      n=name, o=names))


def register(ctx: Ctx) -> None:
    @ctx.tool(RO, title=t("AutoCAD status", "Status do AutoCAD"), pt=(
        "Use primeiro: diz se o AutoCAD está aberto, a versão, o desenho ativo, as unidades, se há comando em "
        "andamento e o que este servidor permite (somente leitura, LISP, comandos)."))
    def status() -> dict[str, Any]:
        """Call this first: tells whether AutoCAD is running, its version, the active drawing, units, any
        command in progress and what this server allows (read-only, LISP, commands)."""

        def f(app: Any) -> dict[str, Any]:
            n = app.Documents.Count
            d: dict[str, Any] = {
                "autocad_version": app.Version,
                "window": WINDOW.get(app.WindowState, app.WindowState),
                "open_documents": n,
            }
            if n:
                doc = app.ActiveDocument
                d["active_document"] = doc_summary(doc, doc.Name)
                d["units"] = units_of(doc)
                d["current_layer"] = doc.ActiveLayer.Name
                d["command_in_progress"] = doc.GetVariable("CMDNAMES") or None
                d["active_space"] = "model" if doc.ActiveSpace == 1 else f"paper ({doc.ActiveLayout.Name})"
            if d["window"] == "minimized" and not ctx.cfg.auto_restore:
                d["warning"] = t("Window minimized: commands, LISP and images won't work until it is restored.",
                                 "Janela minimizada: comandos, LISP e imagens não funcionam até restaurar.")
            d["server"] = {
                "read_only": ctx.cfg.read_only,
                "run_lisp": ctx.cfg.allow_lisp and not ctx.cfg.read_only,
                "run_command": ctx.cfg.allow_commands and not ctx.cfg.read_only,
                "auto_restore_window": ctx.cfg.auto_restore,
            }
            return d

        return ctx.run(f)

    @ctx.tool(RO, title=t("List open drawings", "Listar desenhos abertos"), pt=(
        "Lista os desenhos abertos no AutoCAD, qual está ativo e quais têm alterações não salvas."))
    def list_documents() -> dict[str, Any]:
        """Lists the drawings open in AutoCAD, which one is active and which have unsaved changes."""

        def f(app: Any) -> dict[str, Any]:
            n = app.Documents.Count
            active = app.ActiveDocument.Name if n else None
            return {"documents": [doc_summary(app.Documents.Item(i), active) for i in range(n)]}

        return ctx.run(f)

    @ctx.tool(ADD, title=t("New drawing", "Novo desenho"), pt="Cria um desenho novo e o deixa ativo.")
    def new_drawing(
        template: Annotated[str | None, Field(description=t("Path to a .dwt. Empty = default template.",
                                                            "Caminho de um .dwt. Vazio = template padrão."))] = None,
    ) -> dict[str, Any]:
        """Creates a new drawing and makes it active."""

        def f(app: Any) -> dict[str, Any]:
            if template and not os.path.isfile(template):
                raise AcadError(t("Template not found: {p}", "Template não encontrado: {p}", p=template))
            doc = app.Documents.Add(template) if template else app.Documents.Add()
            time.sleep(0.5)
            return {"created": doc_summary(doc, doc.Name), "units": units_of(doc)}

        return ctx.run(f)

    @ctx.tool(ADD, title=t("Open drawing", "Abrir desenho"), pt="Abre um DWG/DXF (ou ativa, se já estiver aberto).")
    def open_drawing(
        path: Annotated[str, Field(description=t("Full path of the .dwg/.dxf.", "Caminho completo do .dwg/.dxf."))],
        read_only: Annotated[bool, Field(description=t("Open read-only.", "Abrir somente leitura."))] = False,
    ) -> dict[str, Any]:
        """Opens a DWG/DXF (or activates it if already open)."""

        def f(app: Any) -> dict[str, Any]:
            full = os.path.abspath(path)
            for i in range(app.Documents.Count):
                d = app.Documents.Item(i)
                if (d.FullName or "").lower() == full.lower():
                    d.Activate()
                    return {"already_open": True, "document": doc_summary(d, d.Name)}
            if not os.path.isfile(full):
                raise AcadError(t("File not found: {p}", "Arquivo não encontrado: {p}", p=full))
            doc = app.Documents.Open(full, read_only)
            return {"opened": doc_summary(doc, doc.Name)}

        return ctx.run(f)

    @ctx.tool(ADD, title=t("Activate drawing", "Ativar desenho"), pt=(
        "Torna ativo outro desenho já aberto (todas as ferramentas agem no desenho ativo)."))
    def activate_drawing(
        name: Annotated[str, Field(description=t("Name (e.g. 'Plan.dwg') or path of an open drawing.",
                                                 "Nome (ex.: 'Planta.dwg') ou caminho de um desenho aberto."))],
    ) -> dict[str, Any]:
        """Makes another open drawing active (every tool acts on the active drawing)."""

        def f(app: Any) -> dict[str, Any]:
            d = find_doc(app, name)
            d.Activate()
            return {"active": doc_summary(d, d.Name)}

        return ctx.run(f)

    @ctx.tool(EDIT, title=t("Save drawing", "Salvar desenho"), pt=(
        "Salva o desenho ativo. Por segurança nunca grava por cima de um arquivo existente sem overwrite=true. "
        "Prefira 'path' com nome novo (ex.: '..._rev1.dwg') ao editar um desenho do usuário."))
    def save_drawing(
        path: Annotated[str | None, Field(description=t(
            "Save as this path (.dwg). Empty = save to the current file.",
            "Salvar como neste caminho (.dwg). Vazio = salvar no arquivo atual."))] = None,
        overwrite: Annotated[bool, Field(description=t(
            "Must be true to write over an existing file.",
            "Precisa ser true para gravar por cima de um arquivo existente."))] = False,
    ) -> dict[str, Any]:
        """Saves the active drawing. For safety it never writes over an existing file without overwrite=true.
        Prefer 'path' with a new name (e.g. '..._rev1.dwg') when editing a user's drawing."""

        def f(app: Any) -> dict[str, Any]:
            doc = active_doc(app)
            if path:
                full = os.path.abspath(path)
                if not full.lower().endswith((".dwg", ".dxf")):
                    full += ".dwg"
                if not os.path.isdir(os.path.dirname(full)):
                    raise AcadError(t("Folder does not exist: {p}", "Pasta não existe: {p}", p=os.path.dirname(full)))
                if os.path.exists(full) and not overwrite:
                    raise AcadError(t(
                        "{p} already exists. Use another name or overwrite=true (confirm with the user).",
                        "Já existe {p}. Use outro nome ou overwrite=true (confirme com o usuário).", p=full))
                doc.SaveAs(full)
                return {"saved_as": doc.FullName}
            if not doc.FullName:
                raise AcadError(t("The drawing was never saved: give 'path'.",
                                  "Desenho nunca foi salvo: informe 'path'."))
            if not overwrite:
                raise AcadError(t(
                    "Saving would write over {p}. Confirm with the user and call with overwrite=true, "
                    "or use 'path' to save a copy.",
                    "Salvar vai gravar por cima de {p}. Confirme com o usuário e chame com overwrite=true, "
                    "ou use 'path' para salvar uma cópia.", p=doc.FullName))
            doc.Save()
            return {"saved": doc.FullName}

        return ctx.run(f)

    @ctx.tool(EDIT, title=t("Close drawing", "Fechar desenho"), pt=(
        "Fecha um desenho. Com alterações não salvas, recusa, a menos que discard_changes=true (confirme com o "
        "usuário) — ou salve antes com save_drawing."))
    def close_drawing(
        name: Annotated[str | None, Field(description=t("Drawing to close. Empty = the active one.",
                                                        "Desenho a fechar. Vazio = o ativo."))] = None,
        discard_changes: Annotated[bool, Field(description=t("true to close discarding unsaved changes.",
                                                             "true para fechar descartando alterações não salvas."))] = False,
    ) -> dict[str, Any]:
        """Closes a drawing. With unsaved changes it refuses unless discard_changes=true (confirm with the
        user first) — or save first with save_drawing."""

        def f(app: Any) -> dict[str, Any]:
            doc = find_doc(app, name) if name else active_doc(app)
            nm = doc.Name
            if not doc.Saved and not discard_changes:
                raise AcadError(t(
                    "'{n}' has unsaved changes. Save with save_drawing or use discard_changes=true.",
                    "'{n}' tem alterações não salvas. Salve com save_drawing ou use discard_changes=true.", n=nm))
            doc.Close(False)
            time.sleep(0.5)
            return {"closed": nm}

        return ctx.run(f)
