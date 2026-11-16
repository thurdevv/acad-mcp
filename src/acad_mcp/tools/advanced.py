"""Escape hatches: command line, AutoLISP, undo, cancel."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from ..acad import commands as C
from ..acad.errors import AcadError
from ..acad.util import pending_undo_steps, read_undo_marker
from ..acad.winutil import cancel_everything, find_acad_main, is_minimized
from ..i18n import t
from .common import EDIT, Ctx

# The undo tool counts steps with the marker kept in the drawing (acad/util.py), not by reading prompts.
MAX_U = 400      # hard cap of U commands in one call
MAX_IDLE = 80    # U commands in a row that undid no server step (query records, user's own actions)

# Legacy mode (ACAD_MCP_UNDO_MARKER=0): records that do not change the drawing, recognized by English prompts.
NOISE_MARKERS = ("OLEAPPLICATION",)


def _undo_by_prompt(app: Any, doc: Any, steps: int, before: int) -> dict[str, Any]:
    undone: list[str] = []
    skipped = 0
    for _ in range(steps + 200):
        if len(undone) >= steps:
            break
        C.send(app, doc, "_.U\n", 30)
        prompt = str(doc.GetVariable("LASTPROMPT") or "").strip()
        if any(m in prompt.upper() for m in NOISE_MARKERS):
            skipped += 1
            continue
        undone.append(prompt)
        if "everything" in prompt.lower() or "nothing to undo" in prompt.lower():
            break
    return {"undone": undone, "internal_records_skipped": skipped, "mode": "prompt (English AutoCAD only)",
            "modelspace_entities_before": before, "modelspace_entities_after": doc.ModelSpace.Count}


def register(ctx: Ctx) -> None:
    cfg = ctx.cfg

    @ctx.tool(EDIT, title=t("AutoCAD command", "Comando do AutoCAD"), enabled=cfg.allow_commands, pt=(
        "Executa qualquer comando do AutoCAD pela linha de comando — para o que não tem ferramenta própria (FILLET, "
        "TRIM, EXTEND, CHAMFER, STRETCH, JOIN, BOUNDARY, PEDIT, MATCHPROP...). O comando precisa receber TODAS as "
        "respostas; se ficar esperando seleção ou ponto, é cancelado após o tempo limite. Para seleção use run_lisp "
        "com (ssget) ou (handent \"handle\"). Diálogos de arquivo ficam desligados (FILEDIA=0) durante o comando."))
    def run_command(
        command: Annotated[str, Field(description=t(
            "Text exactly as typed at the command line. Use \\n for ENTER. Prefix commands with '_.' (English name, "
            "works in any language) and prefer dialog-free '-' versions (e.g. '_.-LAYER'). Examples: "
            "'_.FILLET _R 5\\n', '_.-PURGE _A * _N\\n'.",
            "Texto exatamente como digitado na linha de comando. Use \\n para ENTER. Prefixe comandos com '_.' (nome "
            "em inglês, funciona em qualquer idioma) e prefira versões sem diálogo com '-' (ex.: '_.-LAYER'). "
            "Exemplos: '_.FILLET _R 5\\n', '_.-PURGE _A * _N\\n'."))],
        timeout_s: Annotated[float | None, Field(ge=1, le=600, description=t(
            "Cancel (ESC) if the command keeps waiting for input longer than this.",
            "Cancela (ESC) se o comando ficar esperando entrada por mais tempo."))] = None,
    ) -> dict[str, Any]:
        """Runs any AutoCAD command from the command line — for what has no dedicated tool (FILLET, TRIM, EXTEND,
        CHAMFER, STRETCH, JOIN, BOUNDARY, PEDIT, MATCHPROP...). The command must get ALL its answers; if it keeps
        waiting for a selection or point it is cancelled after the timeout. For selections use run_lisp with
        (ssget) or (handent "handle"). File dialogs are off (FILEDIA=0) during the command."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            C.check_command(command, cfg.blocked_commands)
            if "(" in command:
                if not cfg.allow_lisp:
                    raise AcadError(t("LISP expressions are disabled on this server.",
                                      "Expressões LISP estão desligadas neste servidor."))
                C.check_lisp(command, cfg.blocked_lisp)
            return C.run_command_step(app, doc, command, timeout_s or cfg.command_timeout)

        return ctx.doc(f)

    @ctx.tool(EDIT, title="AutoLISP", enabled=cfg.allow_lisp, pt=(
        "Executa AutoLISP/Visual LISP no desenho ativo e devolve o resultado. É a ferramenta mais poderosa: qualquer "
        "automação (ssget com filtros, entmod, vla-*, comandos com seleção por handle). Erros voltam em 'error'. "
        "Código longo é aceito; um único texto entre aspas tem limite de ~4000 caracteres (limite do AutoLISP)."))
    def run_lisp(
        code: Annotated[str, Field(description=t(
            "One or more AutoLISP expressions. The value of the LAST one is returned. E.g. "
            "'(sslength (ssget \"X\" (list (cons 8 \"WALL\"))))'. Entity by handle: (handent \"2A3\"). "
            "Run commands: (command \"_.FILLET\" e1 e2).",
            "Uma ou mais expressões AutoLISP. O valor da ÚLTIMA é devolvido. Ex.: "
            "'(sslength (ssget \"X\" (list (cons 8 \"PAREDE\"))))'. Entidade pelo handle: (handent \"2A3\"). "
            "Executar comandos: (command \"_.FILLET\" e1 e2)."))],
        timeout_s: Annotated[float | None, Field(ge=1, le=600)] = None,
    ) -> dict[str, Any]:
        """Runs AutoLISP/Visual LISP in the active drawing and returns the result. The most powerful tool: any
        automation (ssget with filters, entmod, vla-*, commands with selection by handle). Errors come back in
        'error'. Long code is fine; a single quoted string is limited to ~4000 characters (AutoLISP limit)."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            C.check_lisp(code, cfg.blocked_lisp)
            return C.run_lisp_step(app, doc, code, timeout_s or cfg.command_timeout)

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Undo", "Desfazer"), pt=(
        "Desfaz as últimas N alterações feitas POR ESTE SERVIDOR no desenho ativo (cada ferramenta de desenho/edição, "
        "e cada run_lisp/run_command que mudou algo, é 1 passo). O que o usuário fez depois dessas alterações "
        "também é desfeito. Se não houver nada deste servidor para desfazer, não faz nada."))
    def undo(
        steps: Annotated[int, Field(ge=1, le=50, description=t("How many steps to undo.", "Quantos passos desfazer."))] = 1,
    ) -> dict[str, Any]:
        """Undoes the last N changes made THROUGH THIS SERVER in the active drawing (each drawing/editing tool, and
        each run_lisp/run_command that changed something, is one step). Anything the user did after those changes
        is undone too. If nothing from this server is left to undo, it does nothing."""

        def f(app: Any, doc: Any) -> dict[str, Any]:
            from ..acad import util

            before = doc.ModelSpace.Count
            if not util.UNDO_MARKER:
                return _undo_by_prompt(app, doc, steps, before)
            pending = pending_undo_steps(doc)
            if pending <= 0:
                return {"undone_steps": 0, "pending_steps": 0, "note": t(
                    "Nothing done through this server is left to undo in this drawing, so nothing was undone. "
                    "(The user can still press Ctrl+Z in AutoCAD for their own actions.)",
                    "Não há nada feito por este servidor para desfazer neste desenho; nada foi desfeito. "
                    "(O usuário ainda pode usar Ctrl+Z no AutoCAD para as ações dele.)")}
            target = max(pending - steps, 0)
            current, sent, idle = pending, 0, 0
            while current > target and sent < MAX_U and idle < MAX_IDLE:
                C.send(app, doc, "_.U\n", cfg.command_timeout)
                sent += 1
                now = read_undo_marker(doc)[0]
                idle = 0 if now < current else idle + 1
                current = min(current, now)
            out: dict[str, Any] = {
                "undone_steps": pending - current, "requested": steps, "pending_steps": current,
                "u_commands_sent": sent,
                "modelspace_entities_before": before, "modelspace_entities_after": doc.ModelSpace.Count,
            }
            if current > target:
                out["note"] = t("Stopped before reaching the requested steps (undo history exhausted).",
                                "Parei antes de chegar aos passos pedidos (histórico de desfazer esgotado).")
            return out

        return ctx.doc(f)

    @ctx.tool(EDIT, title=t("Cancel command", "Cancelar comando"), pt=(
        "Cancela o que estiver em andamento no AutoCAD (como apertar ESC) e fecha diálogos abertos. Use quando outra "
        "ferramenta disser que o AutoCAD está ocupado."))
    def cancel_command() -> dict[str, Any]:
        """Cancels whatever is running in AutoCAD (like pressing ESC) and closes open dialogs. Use it when another
        tool says AutoCAD is busy."""
        hwnd = ctx.worker.main_hwnd or find_acad_main()
        if not hwnd:
            raise AcadError(t("AutoCAD window not found.", "Não encontrei a janela do AutoCAD."))
        if is_minimized(hwnd):
            raise AcadError(C.MINIMIZED_MSG)
        return {"actions": cancel_everything(hwnd)}
