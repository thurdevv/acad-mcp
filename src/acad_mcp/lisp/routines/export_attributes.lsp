;;; name: export_attributes
;;; desc-en: Append every block insert with its attributes to a CSV (one row per insert, with the drawing name).
;;; desc-pt: Acrescenta cada inserção de bloco com seus atributos num CSV (uma linha por inserção, com o nome do desenho).
;;; args: csv (output path, appended), block (name filter, default "*"), sep (";" default)

(defun mcp-r:export_attributes (args / csv pat sep cnt p row)
  (setq csv (mcp:arg args "csv" nil) pat (mcp:arg args "block" "*") sep (mcp:arg args "sep" ";") cnt 0)
  (if (not csv) (progn (princ "\ncsv is required") (exit)))
  (foreach e (mcp:inserts pat)
    (setq p (cdr (assoc 10 (entget e)))
          row (strcat (mcp:csv-cell (mcp:dwg-name)) sep (cdr (assoc 5 (entget e))) sep
                      (mcp:csv-cell (mcp:effname e)) sep (mcp:csv-cell (cdr (assoc 8 (entget e)))) sep
                      (rtos (car p) 2 4) sep (rtos (cadr p) 2 4)))
    (foreach a (mcp:attribs e)
      (setq row (strcat row sep (mcp:csv-cell (car a)) "=" (mcp:csv-cell (cdr (assoc 1 (entget (cdr a))))))))
    (mcp:append-line csv row)
    (setq cnt (1+ cnt)))
  (strcat "exported " (itoa cnt) " insert(s)"))

(defun c:MCP_EXPORT_ATTRIBUTES (/ f)
  (setq f (getfiled "CSV file" (strcat (getvar "DWGPREFIX") "attributes.csv") "csv" 1))
  (if f (princ (strcat "\n" (mcp-r:export_attributes (list (cons "csv" f))))))
  (princ))
