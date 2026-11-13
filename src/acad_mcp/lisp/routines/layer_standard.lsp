;;; name: layer_standard
;;; desc-en: Create/fix layers from a CSV (name;color;linetype;lineweight_mm;plot yes/no). Erases nothing.
;;; desc-pt: Cria/acerta layers a partir de um CSV (nome;cor;tipo_de_linha;espessura_mm;plota sim/nao). Não apaga nada.
;;; args: csv (input path; first line may be a header), sep (";" default)

(defun mcp:split (s sep / p out)
  (while (setq p (vl-string-search sep s))
    (setq out (cons (substr s 1 p) out) s (substr s (+ p 1 (strlen sep)))))
  (reverse (cons s out)))

(defun mcp-r:layer_standard (args / csv sep f line cols name col lt lw plot cnt)
  (setq csv (mcp:arg args "csv" nil) sep (mcp:arg args "sep" ";") cnt 0)
  (if (or (not csv) (not (findfile csv))) (progn (princ "\ncsv not found") (exit)))
  (setq f (open csv "r"))
  (while (setq line (read-line f))
    (setq cols (mcp:split line sep) name (nth 0 cols))
    (if (and name (/= name "") (not (wcmatch (strcase name) "NAME,NOME,LAYER")))
      (progn
        (setq col (nth 1 cols) lt (nth 2 cols) lw (nth 3 cols) plot (nth 4 cols))
        (command "_.-LAYER" "_M" name "")
        (if (and col (/= col "")) (command "_.-LAYER" "_C" col name ""))
        (if (and lt (/= lt "") (/= (strcase lt) "CONTINUOUS"))
          (progn
            (if (not (tblsearch "LTYPE" lt))
              (command "_.-LINETYPE" "_L" lt (if (= (getvar "MEASUREMENT") 1) "acadiso.lin" "acad.lin") ""))
            (if (tblsearch "LTYPE" lt) (command "_.-LAYER" "_L" lt name ""))))
        (if (and lw (/= lw "")) (command "_.-LAYER" "_LW" (vl-string-translate "," "." lw) name ""))
        (if (and plot (wcmatch (strcase plot) "N*"))
          (command "_.-LAYER" "_P" "_N" name "")
          (if plot (command "_.-LAYER" "_P" "_P" name "")))
        (setq cnt (1+ cnt)))))
  (close f)
  (strcat "processed " (itoa cnt) " layer(s)"))

(defun c:MCP_LAYER_STANDARD (/ f)
  (setq f (getfiled "Layer standard CSV" "" "csv" 0))
  (if f (princ (strcat "\n" (mcp-r:layer_standard (list (cons "csv" f))))))
  (princ))
