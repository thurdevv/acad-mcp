# Changelog

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
