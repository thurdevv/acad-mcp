# Security

acad-mcp gives an AI model control over the AutoCAD running on your computer. Know what that means:

- **AutoLISP is powerful.** `run_lisp`, routines and batch routines can do almost anything AutoCAD can, including
  file access. A deny-list blocks obvious dangers (`startapp`, deleting/renaming files, registry writes, ActiveX
  object creation, loading binaries) but **it is not a sandbox**. Turn LISP off with `ACAD_MCP_ALLOW_LISP=0`, or
  run read-only with `ACAD_MCP_READ_ONLY=1`, when you do not need it.
- **Commands**: `run_command` blocks SHELL, START, NETLOAD, APPLOAD, ARX, VBA*, SCRIPT, QUIT/EXIT, SECURELOAD,
  TRUSTEDPATHS. Add more with `ACAD_MCP_EXTRA_BLOCKED`.
- **Files**: saving never overwrites without `overwrite=true`; CSV exports never overwrite; batch processing works
  on copies only. Received files go to `Documents\AutoCAD MCP\received`.
- **HTTP mode** (`--http`) listens on 127.0.0.1 and validates the Host header. Do **not** put it on a public URL
  without authentication in front of it: anyone who reaches it controls your AutoCAD.
- **Operation log**: each tool call is recorded on the PC in `%LOCALAPPDATA%\acad-mcp\logs` (tool, duration,
  error, PC and Windows user name, summarized arguments — short texts as sent; long texts, LISP code and file
  contents only as a size). It is never sent anywhere. `ACAD_MCP_LOG_ARGS=none` or `ACAD_MCP_LOG=0` to reduce or
  disable it.
- **Trusted paths**: the server never changes AutoCAD's trusted paths or SECURELOAD. Loading the exported routines
  file in AutoCAD is a decision for the user.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository (Security → Report a vulnerability) instead of a public
issue.
