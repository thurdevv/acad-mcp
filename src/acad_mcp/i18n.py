"""English / Português.

Language comes from ACAD_MCP_LANG ("en" default, "pt" for Portuguese), read once at import.
The CLI flag --lang sets the variable before the tools are imported.

Usage: t("English text {x}", "Texto em português {x}", x=1)
Strings stay side by side at the call site, so a translation is never missing or out of sync.
"""

from __future__ import annotations

import os

LANG = "pt" if os.environ.get("ACAD_MCP_LANG", "en").strip().lower().startswith("pt") else "en"


def t(en: str, pt: str, **kw: object) -> str:
    s = pt if LANG == "pt" else en
    return s.format(**kw) if kw else s
