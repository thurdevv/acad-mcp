"""acad-mcp tools, grouped by subject."""

from . import (
    advanced,
    batch,
    documents,
    draw,
    edit,
    extract,
    files,
    layers,
    query,
    recreate,
    routines,
    standards,
    view,
)
from .common import Ctx

MODULES = [documents, query, view, draw, edit, layers, extract, standards, files, recreate, routines, batch, advanced]


def register_all(ctx: Ctx) -> None:
    for m in MODULES:
        m.register(ctx)
