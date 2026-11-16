"""Reusable LISP routines: run without AI, in the active drawing or over a folder (batch.py).

Built-in routines ship in acad_mcp/lisp/routines; the user's (and the ones the model saves) live in
Documents\\AutoCAD MCP\\routines. Each routine defines mcp-r:<name> (taking an association list of
parameters) and usually a command MCP_<NAME> that a person can type in AutoCAD.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from ..acad import commands as C
from ..acad.errors import AcadError
from ..i18n import LANG, t
from .common import ADD, EDIT, RO, Ctx

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "lisp" / "routines"
USER_DIR = Path(os.environ.get("ACAD_MCP_ROUTINES_DIR", Path.home() / "Documents" / "AutoCAD MCP" / "routines"))
COMMON = BUILTIN_DIR / "_common.lsp"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def parse_header(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": path.stem, "path": str(path),
                            "source": "builtin" if path.parent == BUILTIN_DIR else "user"}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = re.match(r"^;;;\s*(name|desc-en|desc-pt|args):\s*(.*)$", line.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip()
    meta["description"] = meta.get("desc-pt" if LANG == "pt" else "desc-en") or meta.get("desc-en") or ""
    cmd = re.search(r"\(defun\s+c:(MCP_[A-Z0-9_]+)", text, re.I)
    meta["autocad_command"] = cmd.group(1).upper() if cmd else None
    return meta


def routine_files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for folder in (BUILTIN_DIR, USER_DIR):  # user wins on name clash
        if folder.is_dir():
            out.update({p.stem: p for p in folder.glob("*.lsp") if not p.stem.startswith("_")})
    return out


def routine_code(name: str, blocked: set[str] | None = None) -> str:
    files = routine_files()
    if name not in files:
        raise AcadError(t("Routine '{n}' not found. Available: {a}", "Rotina '{n}' não encontrada. Disponíveis: {a}",
                          n=name, a=sorted(files)))
    code = files[name].read_text(encoding="utf-8", errors="replace")
    if blocked and files[name].parent != BUILTIN_DIR:
        C.check_lisp(code, blocked)
    return COMMON.read_text(encoding="utf-8") + "\n" + code


def args_to_lisp(args: dict[str, Any] | None) -> str:
    if not args:
        return "nil"
    return "(list " + " ".join(f"(cons {C.lisp_str(str(k))} {C.lisp_str(str(v))})" for k, v in args.items()) + ")"


def call_expr(name: str, args: dict[str, Any] | None) -> str:
    return f"(mcp-r:{name} {args_to_lisp(args)})"


def register(ctx: Ctx) -> None:
    cfg = ctx.cfg

    @ctx.tool(RO, title=t("List routines", "Listar rotinas"), pt=(
        "Lista as rotinas LISP reutilizáveis (as que vêm com o servidor e as do usuário): o que fazem, parâmetros e o "
        "comando que a pessoa pode digitar no AutoCAD para rodar sem IA. Elas também rodam em lote numa pasta "
        "(batch_process)."))
    def list_routines() -> dict[str, Any]:
        """Lists the reusable LISP routines (shipped and the user's): what they do, their parameters and the
        command a person can type in AutoCAD to run them without AI. They also run over a folder (batch_process)."""
        return {"user_folder": str(USER_DIR),
                "routines": [parse_header(p) for _, p in sorted(routine_files().items())]}

    @ctx.tool(EDIT, title=t("Run routine", "Rodar rotina"), enabled=cfg.allow_lisp, pt=(
        "Roda uma rotina (veja list_routines) no desenho ativo, com parâmetros. Mais barato e previsível que pedir "
        "à IA para repetir uma tarefa. Para vários arquivos use batch_process."))
    def run_routine(
        name: Annotated[str, Field(description=t("Routine name (list_routines).", "Nome da rotina (list_routines)."))],
        args: Annotated[dict[str, str] | None, Field(description=t(
            "Parameters, e.g. {'find': 'OLD', 'replace': 'NEW'}.", "Parâmetros, ex.: {'find': 'ANTIGO', 'replace': 'NOVO'}."))] = None,
        timeout_s: Annotated[float | None, Field(ge=1, le=3600)] = None,
    ) -> dict[str, Any]:
        """Runs a routine (see list_routines) on the active drawing with parameters. Cheaper and more predictable
        than asking the AI to repeat a task. For many files use batch_process."""
        code = routine_code(name, cfg.blocked_lisp) + "\n" + call_expr(name, args)

        def f(app: Any, doc: Any) -> dict[str, Any]:
            return C.run_lisp_step(app, doc, code, timeout_s or max(cfg.command_timeout, 120))

        return ctx.doc(f)

    @ctx.tool(ADD, title=t("Save routine", "Salvar rotina"), enabled=cfg.allow_lisp, pt=(
        "Salva uma rotina LISP nova para reusar sem IA. Teste antes com run_lisp. O código precisa definir "
        "(defun mcp-r:<name> (args) ...) recebendo uma lista de pares (\"param\" . \"valor\") e devolvendo um texto; "
        "use (mcp:arg args \"param\" padrão). Use só LISP puro + vlax-curve se quiser rodar em lote no "
        "accoreconsole (vla-* não funciona lá). Nunca sobrescreve sem overwrite=true."))
    def save_routine(
        name: Annotated[str, Field(description=t("lowercase_name (letters, digits, _).", "nome_minusculo (letras, números, _)."))],
        code: Annotated[str, Field(description=t("AutoLISP defining (defun mcp-r:<name> (args) ...).",
                                                 "AutoLISP que define (defun mcp-r:<name> (args) ...)."))],
        description: Annotated[str, Field(description=t("What it does (one line).", "O que faz (uma linha)."))],
        args_doc: Annotated[str, Field(description=t("Parameters and defaults (one line).",
                                                     "Parâmetros e padrões (uma linha)."))] = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Saves a new LISP routine to reuse without AI. Test it first with run_lisp. The code must define
        (defun mcp-r:<name> (args) ...) taking a list of ("param" . "value") pairs and returning a string; use
        (mcp:arg args "param" default). Use pure LISP + vlax-curve to run it in batch with accoreconsole (vla-*
        does not work there). Never overwrites without overwrite=true."""
        if not NAME_RE.match(name):
            raise AcadError(t("Invalid name: use lowercase letters, digits and _.",
                              "Nome inválido: use letras minúsculas, números e _."))
        if f"mcp-r:{name}" not in code.lower():
            raise AcadError(t("The code must define (defun mcp-r:{n} (args) ...).",
                              "O código precisa definir (defun mcp-r:{n} (args) ...).", n=name))
        C.check_lisp(code, cfg.blocked_lisp)
        if (BUILTIN_DIR / f"{name}.lsp").exists():
            raise AcadError(t("'{n}' is a built-in routine; pick another name.",
                              "'{n}' é uma rotina embutida; escolha outro nome.", n=name))
        USER_DIR.mkdir(parents=True, exist_ok=True)
        path = USER_DIR / f"{name}.lsp"
        if path.exists() and not overwrite:
            raise AcadError(t("{p} already exists. Use overwrite=true (confirm with the user).",
                              "{p} já existe. Use overwrite=true (confirme com o usuário).", p=path))
        cmd = "MCP_" + name.upper()
        wrapper = "" if f"c:{cmd}".lower() in code.lower() else (
            f"\n(defun c:{cmd} () (princ (strcat \"\\n\" (vl-princ-to-string (mcp-r:{name} nil)))) (princ))\n")
        header = f";;; name: {name}\n;;; desc-en: {description}\n;;; desc-pt: {description}\n;;; args: {args_doc}\n\n"
        path.write_text(header + code.strip() + "\n" + wrapper, encoding="utf-8")
        return {"saved": str(path), "autocad_command": cmd}

    @ctx.tool(ADD, title=t("Export routines for AutoCAD", "Exportar rotinas para o AutoCAD"), pt=(
        "Gera um único arquivo .lsp com todas as rotinas, para a pessoa carregar no AutoCAD (APPLOAD ou Startup Suite) "
        "e digitar os comandos MCP_... sem IA. Devolve o caminho e as instruções."))
    def export_routines_for_autocad() -> dict[str, Any]:
        """Writes one .lsp file with every routine so a person can load it in AutoCAD (APPLOAD or the Startup
        Suite) and type the MCP_... commands without AI. Returns the path and instructions."""
        USER_DIR.mkdir(parents=True, exist_ok=True)
        out = USER_DIR / "acad_mcp_routines.lsp"
        parts = [COMMON.read_text(encoding="utf-8")]
        cmds = []
        for _, p in sorted(routine_files().items()):
            if p.name == out.name:
                continue
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
            meta = parse_header(p)
            if meta["autocad_command"]:
                cmds.append(meta["autocad_command"])
        out.write_text("(vl-load-com)\n" + "\n\n".join(parts) + "\n(princ)\n", encoding="utf-8")
        return {"file": str(out), "commands": cmds, "how_to_load": t(
            "In AutoCAD type APPLOAD, pick this file and click Load (or add it to the Startup Suite to load it "
            "every time). AutoCAD may ask to trust the folder — that is the user's decision.",
            "No AutoCAD digite APPLOAD, escolha este arquivo e clique em Load (ou adicione ao Startup Suite para "
            "carregar sempre). O AutoCAD pode pedir para confiar na pasta — decisão do usuário.")}
