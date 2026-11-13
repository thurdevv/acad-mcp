;;; name: count_blocks
;;; desc-en: Count block inserts by name and append drawing;block;count rows to a CSV.
;;; desc-pt: Conta inserções de blocos por nome e acrescenta linhas desenho;bloco;quantidade num CSV.
;;; args: csv (output path, appended), block (name filter, default "*"), sep (";" default)

(defun mcp-r:count_blocks (args / csv pat sep counts nm v)
  (setq csv (mcp:arg args "csv" nil) pat (mcp:arg args "block" "*") sep (mcp:arg args "sep" ";"))
  (foreach e (mcp:inserts pat)
    (setq nm (mcp:effname e))
    (if (setq v (assoc nm counts))
      (setq counts (subst (cons nm (1+ (cdr v))) v counts))
      (setq counts (cons (cons nm 1) counts))))
  (if csv
    (foreach c counts
      (mcp:append-line csv (strcat (mcp:csv-cell (mcp:dwg-name)) sep (mcp:csv-cell (car c)) sep (itoa (cdr c))))))
  (vl-prin1-to-string counts))

(defun c:MCP_COUNT_BLOCKS ()
  (princ (strcat "\n" (mcp-r:count_blocks nil)))
  (princ))
