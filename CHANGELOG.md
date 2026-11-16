# Changelog

## 0.2.2 — 2026-11-16

- Works on **Python 3.10 to 3.14** (was 3.13+). The offline suite runs on every version in CI; the live AutoCAD
  suite is still run on 3.13 only.
- Docs: install with `py -m pip install acad-mcp` and `py -m acad_mcp install-claude`, which works when Python's
  `Scripts` folder is not on PATH.
- Lighter install: the unused `mcp[cli]` extra is no longer required.

## 0.2.1 — 2026-11-13

- Connect to Claude Desktop without the repo: after `pip install acad-mcp`, double-click `acad-mcp.exe` (or run
  `acad-mcp install-claude`). It checks the install, asks the language, closes Claude (after asking), writes the
  config with a backup and reopens Claude. MCP clients still get the server (they start it through pipes).
- `acad-mcp uninstall-claude` disconnects it the same way.
- `INSTALAR.bat` uses the same code.
- `--stdio` forces server mode from a console.

## 0.2.0 — 2026-11-13

First public version.

- 61 tools and the `recreate_drawing` prompt; English by default, Portuguese with `ACAD_MCP_LANG=pt`.
- Fast reads through in-process LISP for large drawings (measured 50,000 entities in ~15 s vs ~22 min over COM),
  with automatic fallback to COM.
- Batch processing of folders with `accoreconsole` (parallel) or the open AutoCAD; originals never modified.
- Built-in reusable LISP routines, `save_routine`, and export of all routines as `MCP_...` AutoCAD commands.
- `import_file`: DXF (validated with ezdxf before AutoCAD sees it), DWG, vector PDF (`-PDFIMPORT`), images.
- `trace_image`: raster to lines with OpenCV (perspective correction, calibration by two points).
- `get_view_image(grid=true)`: coordinate grid and pixel-to-coordinate mapping.
- Robustness measured on AutoCAD 2027: COM message filter (no tool re-runs), command watchdog (ESC to the drawing
  area), minimized-window guard, long LISP through files, undo skipping internal records.
- Claude Desktop extension (`.mcpb`, uv runtime) and MCP registry metadata.
