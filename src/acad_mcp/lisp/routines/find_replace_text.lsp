;;; name: find_replace_text
;;; desc-en: Find and replace text in TEXT, MTEXT and block attribute values.
;;; desc-pt: Localiza e substitui texto em TEXT, MTEXT e valores de atributos de blocos.
;;; args: find (text), replace (text), case_sensitive ("yes"/"no", default "yes")

(defun mcp-r:find_replace_text (args / find repl cs ss i e ed n cnt s new)
  (setq find (mcp:arg args "find" nil) repl (mcp:arg args "replace" "")
        cs (/= (strcase (mcp:arg args "case_sensitive" "yes")) "NO") cnt 0)
  (if (not find) (progn (princ "\nfind is required") (exit)))
  (defun mcp:rep (s / p out ls lf)
    (setq out "" ls (if cs s (strcase s)) lf (if cs find (strcase find)))
    (while (setq p (vl-string-search lf ls))
      (setq out (strcat out (substr s 1 p) repl)
            s (substr s (+ p 1 (strlen find))) ls (substr ls (+ p 1 (strlen find)))))
    (strcat out s))
  (defun mcp:fix (en code / ed old new)
    (setq ed (entget en) old (cdr (assoc code ed)))
    (if (and old (/= old (setq new (mcp:rep old))))
      (progn (entmod (subst (cons code new) (assoc code ed) ed)) (entupd en) (setq cnt (1+ cnt)))))
  (if (setq ss (ssget "_X" '((0 . "TEXT,MTEXT,INSERT"))))
    (progn
      (setq i 0)
      (repeat (sslength ss)
        (setq e (ssname ss i) i (1+ i) ed (entget e))
        (cond
          ((= (cdr (assoc 0 ed)) "INSERT")
           (foreach a (mcp:attribs e) (mcp:fix (cdr a) 1)))
          (T (mcp:fix e 1))))))
  (strcat "replaced in " (itoa cnt) " object(s)"))

(defun c:MCP_FIND_REPLACE (/ f r)
  (setq f (getstring T "\nFind: ") r (getstring T "\nReplace with: "))
  (princ (strcat "\n" (mcp-r:find_replace_text (list (cons "find" f) (cons "replace" r)))))
  (princ))
