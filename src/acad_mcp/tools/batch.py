"""Batch processing of a folder of DWGs. Originals are never touched: each file is COPIED to an output
folder first and only the copy is processed.

Engines (measured on AutoCAD 2027):
- "console": accoreconsole.exe (AutoCAD without a window), ~7 s per file including start-up, several in
  parallel. Pure AutoLISP and vlax-curve-* work; vla-* (ActiveX) does NOT. Plotting to PDF and QSAVE work.
- "gui": the running AutoCAD opens, processes, saves and closes each copy (needed for vla-* routines and for
  JSON standards). Sequential, uses the visible window.
"""

from __future__ import annotations

import csv
import glob
import os
import shutil
import subprocess
import threading
import time
import uuid
import winreg
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from ..acad import commands as C
from ..acad.errors import AcadError
from ..acad.util import without_undo_marker
from ..i18n import t
from .common import ADD, RO, Ctx
from .routines import call_expr, routine_code
from .standards import apply as apply_standard
from .standards import load_standard


@dataclass
class Job:
    id: str
    folder: str
    output: str
    engine: str
    total: int
    task: str
    status: str = "running"
    done: int = 0
    ok: int = 0
    failed: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    report: str | None = None
    error: str | None = None


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def find_accoreconsole() -> Path:
    env = os.environ.get("ACAD_MCP_ACCORECONSOLE")
    if env and Path(env).is_file():
        return Path(env)
    found: list[tuple[str, Path]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\AutoCAD") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                rel = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, rel) as rk:
                    for j in range(winreg.QueryInfoKey(rk)[0]):
                        prod = winreg.EnumKey(rk, j)
                        try:
                            with winreg.OpenKey(rk, prod) as pk:
                                loc = winreg.QueryValueEx(pk, "AcadLocation")[0]
                                exe = Path(loc) / "accoreconsole.exe"
                                if exe.is_file():
                                    found.append((rel, exe))
                        except OSError:
                            pass
    except OSError:
        pass
    if not found:
        raise AcadError(t("accoreconsole.exe not found (needs full AutoCAD; LT has none). Use engine='gui' or set "
                          "ACAD_MCP_ACCORECONSOLE.",
                          "accoreconsole.exe não encontrado (precisa do AutoCAD completo; o LT não tem). Use "
                          "engine='gui' ou defina ACAD_MCP_ACCORECONSOLE."))
    return sorted(found)[-1][1]


def _files(folder: Path, pattern: str, recursive: bool, exclude: Path) -> list[Path]:
    pat = str(folder / ("**" if recursive else "") / pattern)
    out = []
    for p in glob.glob(pat, recursive=recursive):
        pp = Path(p)
        if pp.is_file() and exclude not in pp.parents:
            out.append(pp)
    return sorted(out)


def _finish(job: Job) -> None:
    rep = Path(job.output) / "report.csv"
    with open(rep, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "seconds", "result", "error"])
        for r in job.rows:
            w.writerow([r["file"], r["status"], r["seconds"], r.get("result", ""), r.get("error", "")])
    job.report = str(rep)
    job.finished = time.time()
    job.status = "done" if job.failed == 0 else "done_with_errors"


def _record(job: Job, row: dict[str, Any]) -> None:
    with _LOCK:
        job.rows.append(row)
        job.done += 1
        if row["status"] == "ok":
            job.ok += 1
        else:
            job.failed += 1


def _per_file_args(args: dict[str, str] | None, index: int) -> tuple[dict[str, str] | None, str | None, str | None]:
    """Parallel processes appending to one CSV lose rows (seen in tests: 3 files, 2 rows). Each file writes
    its own part; parts are merged in file order at the end. Returns (args, part_path, final_path)."""
    if not args or "csv" not in args:
        return args, None, None
    final = args["csv"]
    part = f"{final}.part{index:05d}"
    return {**args, "csv": part}, part, final


def _merge_parts(parts: list[tuple[str | None, str | None]]) -> None:
    by_final: dict[str, list[str]] = {}
    for part, final in parts:
        if part and final:
            by_final.setdefault(final, []).append(part)
    for final, plist in by_final.items():
        with open(final, "ab") as out:
            for part in plist:
                if os.path.exists(part):
                    with open(part, "rb") as fh:
                        out.write(fh.read())
                    os.remove(part)


def _console_one(acc: Path, copy: Path, rel: str, lisp_file: str, call: str, timeout: float) -> dict[str, Any]:
    res = copy.with_suffix(".mcp_result.txt")
    scr = copy.with_suffix(".mcp.scr")
    lines = [
        C.eval_file_expr(lisp_file),
        f"(setq #mcp-r (vl-catch-all-apply (function (lambda () {call}))))",
        f"(progn (setq #f (open {C.lisp_path(str(res))} \"w\")) (write-line (if (vl-catch-all-error-p #mcp-r) "
        "(strcat \"ERR:\" (vl-catch-all-error-message #mcp-r)) (vl-princ-to-string #mcp-r)) #f) (close #f) (princ))",
        "_.QSAVE",
        "",
    ]
    scr.write_text("\n".join(lines), encoding="mbcs", errors="replace")
    t0 = time.monotonic()
    row: dict[str, Any] = {"file": rel}
    try:
        # cwd = the copy's folder: accoreconsole drops plot.log and ErrorReports\ in its working directory
        p = subprocess.run([str(acc), "/i", str(copy), "/s", str(scr), "/l", "en-US"], capture_output=True,
                           timeout=timeout, cwd=str(copy.parent))
        log = p.stdout.decode("utf-16-le", errors="replace").replace("\x00", "")
        copy.with_suffix(".mcp_log.txt").write_text(log, encoding="utf-8")
        out = res.read_text(encoding="mbcs", errors="replace").strip() if res.exists() else None
        if out is None:
            row.update(status="error", error=t("no result (see the .mcp_log.txt next to the copy)",
                                               "sem resultado (veja o .mcp_log.txt ao lado da cópia)"))
        elif out.startswith("ERR:"):
            row.update(status="error", error=out[4:])
        else:
            row.update(status="ok", result=out)
    except subprocess.TimeoutExpired:
        row.update(status="error", error=t("timed out", "passou do tempo limite"))
    finally:
        for f in (scr, res):
            try:
                f.unlink()
            except OSError:
                pass
    row["seconds"] = round(time.monotonic() - t0, 1)
    return row


def _run_console(job: Job, pairs: list[tuple[Path, str]], code: str, routine: str, args: dict | None,
                 workers: int, timeout: float) -> None:
    parts: list[tuple[str | None, str | None]] = []
    try:
        acc = find_accoreconsole()
        lisp_file = C.write_lisp_file(code, str(Path(job.output) / "_routine.lsp"))
        work = []
        for i, (copy, rel) in enumerate(pairs):
            a, part, final = _per_file_args(args, i)
            parts.append((part, final))
            work.append((copy, rel, call_expr(routine, a)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for row in ex.map(lambda w: _console_one(acc, w[0], w[1], lisp_file, w[2], timeout), work):
                _record(job, row)
        try:
            os.remove(lisp_file)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001
        job.error = str(exc)
    _merge_parts(parts)
    _finish(job)


def _run_gui(ctx: Ctx, job: Job, pairs: list[tuple[Path, str]], code: str | None, routine: str | None,
             args: dict | None, std: dict | None, timeout: float) -> None:
    parts: list[tuple[str | None, str | None]] = []
    for i, (copy, rel) in enumerate(pairs):
        a, part, final = _per_file_args(args, i)
        parts.append((part, final))
        call = call_expr(routine, a) if routine else None
        t0 = time.monotonic()
        row: dict[str, Any] = {"file": rel}

        def one(app: Any) -> str:
            with without_undo_marker():
                return process(app)

        def process(app: Any) -> str:
            doc = app.Documents.Open(str(copy))
            try:
                if std is not None:
                    r = apply_standard(ctx, app, doc, std)
                    res = t("{a} action(s), {i} issue(s) left", "{a} ação(ões), {i} problema(s) restantes",
                            a=len(r["actions"]), i=len(r["remaining_issues"]))
                else:
                    r = C.run_lisp(app, doc, code + "\n" + call, timeout)
                    if r.get("error") or r.get("cancelled_after_timeout"):
                        raise AcadError(r.get("error") or r.get("note"))
                    res = r.get("result") or ""
                doc.Save()
                return res
            finally:
                doc.Close(False)

        try:
            row.update(status="ok", result=ctx.worker.call(one, timeout=timeout + 60))
        except Exception as exc:  # noqa: BLE001
            row.update(status="error", error=str(exc)[:300])
        row["seconds"] = round(time.monotonic() - t0, 1)
        _record(job, row)
    _merge_parts(parts)
    _finish(job)


def register(ctx: Ctx) -> None:
    cfg = ctx.cfg

    @ctx.tool(ADD, title=t("Batch process a folder", "Processar pasta em lote"), pt=(
        "Roda uma rotina (list_routines) ou aplica um padrão JSON em TODOS os DWGs de uma pasta. Os originais nunca "
        "são tocados: cada arquivo é copiado para uma pasta de saída e só a cópia é processada. Roda em segundo "
        "plano: devolve job_id na hora; acompanhe com batch_status. engine='console' usa o AutoCAD sem janela "
        "(rápido, em paralelo; só LISP puro); 'gui' usa o AutoCAD aberto (necessário para padrões JSON e rotinas "
        "com vla-*). Use dry_run=true para ver a lista de arquivos antes."))
    def batch_process(
        folder: Annotated[str, Field(description=t("Folder with the DWGs.", "Pasta com os DWGs."))],
        routine: Annotated[str | None, Field(description=t("Routine name (list_routines).",
                                                           "Nome da rotina (list_routines)."))] = None,
        args: Annotated[dict[str, str] | None, Field(description=t("Routine parameters.", "Parâmetros da rotina."))] = None,
        standard_name: Annotated[str | None, Field(description=t(
            "OR a JSON standard to apply (list_standards). Needs engine='gui'.",
            "OU um padrão JSON para aplicar (list_standards). Requer engine='gui'."))] = None,
        pattern: Annotated[str, Field(description=t("File pattern.", "Padrão de arquivos."))] = "*.dwg",
        recursive: bool = False,
        engine: Annotated[Literal["console", "gui"], Field(description=t(
            "'console' = AutoCAD without a window (fast, parallel); 'gui' = the open AutoCAD.",
            "'console' = AutoCAD sem janela (rápido, paralelo); 'gui' = o AutoCAD aberto."))] = "console",
        output_folder: Annotated[str | None, Field(description=t(
            "Where the processed copies go. Default: <folder>\\output_<date-time>.",
            "Onde vão as cópias processadas. Padrão: <pasta>\\saida_<data-hora>."))] = None,
        workers: Annotated[int, Field(ge=1, le=8, description=t("Parallel files (console).",
                                                                "Arquivos em paralelo (console)."))] = 2,
        timeout_per_file_s: Annotated[float, Field(ge=10, le=3600)] = 300,
        dry_run: Annotated[bool, Field(description=t("Only list the files that would be processed.",
                                                     "Só listar os arquivos que seriam processados."))] = False,
    ) -> dict[str, Any]:
        """Runs a routine (list_routines) or applies a JSON standard on EVERY DWG in a folder. Originals are never
        touched: each file is copied to an output folder and only the copy is processed. Runs in the background:
        returns a job_id right away; follow it with batch_status. engine='console' uses AutoCAD without a window
        (fast, parallel; pure LISP only); 'gui' uses the open AutoCAD (needed for JSON standards and vla-*
        routines). Use dry_run=true to see the file list first."""
        src = Path(folder)
        if not src.is_dir():
            raise AcadError(t("Folder not found: {p}", "Pasta não encontrada: {p}", p=folder))
        if bool(routine) == bool(standard_name):
            raise AcadError(t("Give exactly one: routine or standard_name.",
                              "Informe exatamente um: routine ou standard_name."))
        if standard_name and engine != "gui":
            raise AcadError(t("JSON standards need engine='gui'.", "Padrões JSON precisam de engine='gui'."))
        if routine and not cfg.allow_lisp:
            raise AcadError(t("Routines are LISP, which is disabled on this server.",
                              "Rotinas são LISP, que está desligado neste servidor."))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = Path(output_folder) if output_folder else src / f"{t('output', 'saida')}_{stamp}"
        files = _files(src, pattern, recursive, out)
        if not files:
            raise AcadError(t("No files matching {p} in {f}.", "Nenhum arquivo {p} em {f}.", p=pattern, f=folder))
        if dry_run:
            return {"would_process": [str(p) for p in files], "count": len(files), "output_folder": str(out)}

        code = std = None
        if routine:
            code = routine_code(routine, cfg.blocked_lisp)
        else:
            std = load_standard(standard_name, None, None)

        out.mkdir(parents=True, exist_ok=True)
        pairs: list[tuple[Path, str]] = []
        for p in files:
            rel = str(p.relative_to(src))
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            pairs.append((dest, rel))

        job = Job(id=uuid.uuid4().hex[:8], folder=str(src), output=str(out), engine=engine, total=len(pairs),
                  task=routine or f"standard:{standard_name}")
        JOBS[job.id] = job
        if engine == "console":
            th = threading.Thread(target=_run_console,
                                  args=(job, pairs, code, routine, args, workers, timeout_per_file_s), daemon=True)
        else:
            th = threading.Thread(target=_run_gui,
                                  args=(ctx, job, pairs, code, routine, args, std, timeout_per_file_s), daemon=True)
        th.start()
        return {"job_id": job.id, "files": len(pairs), "output_folder": str(out), "engine": engine,
                "note": t("Originals untouched. Follow with batch_status.",
                          "Originais intactos. Acompanhe com batch_status.")}

    @ctx.tool(RO, title=t("Batch status", "Andamento do lote"), pt=(
        "Mostra o andamento de um processamento em lote (ou lista todos): quantos foram, erros, resultado por "
        "arquivo e o relatório CSV ao terminar."))
    def batch_status(
        job_id: Annotated[str | None, Field(description=t("Job id. Empty = list all.", "Id da tarefa. Vazio = listar todas."))] = None,
        last: Annotated[int, Field(ge=1, le=1000, description=t("How many per-file rows to show.",
                                                                "Quantas linhas por arquivo mostrar."))] = 50,
    ) -> dict[str, Any]:
        """Shows the progress of a batch job (or lists them all): how many done, errors, per-file results and the
        CSV report when finished."""
        if not job_id:
            return {"jobs": [{"job_id": j.id, "status": j.status, "done": j.done, "total": j.total, "task": j.task}
                             for j in JOBS.values()]}
        j = JOBS.get(job_id)
        if j is None:
            raise AcadError(t("Unknown job {j}.", "Tarefa {j} desconhecida.", j=job_id))
        elapsed = (j.finished or time.time()) - j.started
        return {"job_id": j.id, "status": j.status, "task": j.task, "engine": j.engine, "done": j.done,
                "total": j.total, "ok": j.ok, "failed": j.failed, "seconds": round(elapsed, 1),
                "output_folder": j.output, "report": j.report, "error": j.error, "results": j.rows[-last:]}
