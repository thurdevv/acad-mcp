# Changelog

## 0.3.0 — 2026-11-16

Drafting features. 68 tools.

- **Hatch with holes**: `draw_hatch(inner_boundary_handles=[...])`; the result has the net area, the outer
  area and the number of holes.
- **Dimension styles**: `manage_dim_style` (text height, arrow size, extension offsets, text gap, overall
  scale, measurement factor, decimals, text style; `based_on` for a new style). `list_styles` now returns
  the values of every dimension style. Written through the style record so that `undo` really reverts it.
- **Sheets**: `list_layouts`, `manage_layout` (create, copy, rename, delete, activate, page setup with paper
  by size such as `A3`, orientation, device, plot style, plot area and scale), `create_viewport` (scale and
  the model point at its centre), `plot_layouts` (one PDF per sheet, never over an existing file without
  `overwrite=true`, page size read back from the PDF).
- Routine `plot_pdf`: new `layouts` argument (`all` or names with wildcards) for one PDF per sheet in batch,
  also with the console engine. Routine `fill_title_block`: new `layout` argument.
- `query_entities`, `quantities` and `block_report`: new `layout` filter.
- **Dynamic blocks**: `get_entity` lists the dynamic properties with allowed values;
  `set_dynamic_block_properties` changes them; `insert_block(dynamic_properties=...)`.
- The compatibility test (`diagnostico --teste`) has 18 steps now (hatch with a hole, dimension style,
  sheet + viewport + PDF).
- Demo GIF re-recorded with hatched walls.
- Not verified: same as before (only AutoCAD 2027 in English). Dynamic blocks were tested with AutoCAD's
  sample blocks only; block properties of point type cannot be set.

## 0.2.3 — 2026-11-16

Support release.

- **`acad-mcp diagnostico`** (alias `diagnose`): writes a report on the Desktop (versions, PATH, AutoCAD in the
  registry and its window state, console engine, Claude configs, self-test, latest errors). No drawing names or
  contents. `--teste` also runs a 15-step compatibility test in AutoCAD on a new drawing closed without saving.
  New read-only tool `server_diagnostics` (62 tools now); `status` reports the server version.
- **Operation log** per PC in `%LOCALAPPDATA%\acad-mcp\logs` (JSON Lines, rotating): tool, duration, ok or
  error, and summarized arguments (long texts, LISP code and file contents only as a size).
  `ACAD_MCP_LOG=0`, `ACAD_MCP_LOG_ARGS=none|summary|full`, `ACAD_MCP_LOG_DIR`.
- **`undo` no longer reads AutoCAD's prompts**, so it does not depend on AutoCAD's language: a counter stored in
  the drawing (dictionary `ACAD_MCP`) is bumped inside every write's undo group. `run_lisp`/`run_command` are now
  one undo step each and only count when they changed the drawing; `set_current`, `load_linetype`, PDF import
  and `purge_and_audit` are counted steps too. With nothing of the server left to undo, `undo` does nothing.
  `purge_and_audit(remove_server_marker=true)` removes the dictionary; `ACAD_MCP_UNDO_MARKER=0` restores the old
  prompt-based undo.
- A received file that fails validation is deleted instead of being left in the received folder.
- Not verified: AutoCAD 2021–2026 and non-English AutoCAD (only AutoCAD 2027 in English was available). The
  compatibility test is there for whoever has them.

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
