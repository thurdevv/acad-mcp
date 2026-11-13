;;; name: purge_audit
;;; desc-en: Purge unused items (3 passes, including registered apps) and run AUDIT fixing errors.
;;; desc-pt: Remove itens não usados (3 passadas, inclusive apps registrados) e roda AUDIT corrigindo erros.
;;; args: audit ("yes"/"no", default "yes")

(defun mcp-r:purge_audit (args)
  (repeat 3 (command "_.-PURGE" "_A" "*" "_N"))
  (command "_.-PURGE" "_R" "*" "_N")
  (if (/= (strcase (mcp:arg args "audit" "yes")) "NO") (command "_.AUDIT" "_Y"))
  "purged")

(defun c:MCP_PURGE_AUDIT () (princ (strcat "\n" (mcp-r:purge_audit nil))) (princ))
