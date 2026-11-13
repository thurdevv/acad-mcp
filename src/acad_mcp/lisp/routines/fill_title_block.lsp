;;; name: fill_title_block
;;; desc-en: Set attribute values on every insert of a block (title blocks, stamps, tags).
;;; desc-pt: Preenche valores de atributos em todas as inserções de um bloco (carimbos, selos, tags).
;;; args: block (name, wildcards ok), then one key per TAG to set, e.g. ("REV" . "B") ("DATE" . "2026-11-13")

(defun mcp-r:fill_title_block (args / pat refs cnt ed)
  (setq pat (mcp:arg args "block" nil) cnt 0)
  (if (not pat) (progn (princ "\nblock is required") (exit)))
  (foreach e (mcp:inserts pat)
    (foreach a (mcp:attribs e)
      (foreach kv args
        (if (and (/= (car kv) "block") (= (strcase (car kv)) (strcase (car a))))
          (progn
            (setq ed (entget (cdr a)))
            (entmod (subst (cons 1 (cdr kv)) (assoc 1 ed) ed))
            (entupd (cdr a))
            (setq cnt (1+ cnt)))))))
  (strcat "set " (itoa cnt) " attribute value(s)"))

(defun c:MCP_FILL_TITLE (/ b tag val)
  (setq b (getstring T "\nBlock name: ") tag (getstring "\nTag: ") val (getstring T "\nValue: "))
  (princ (strcat "\n" (mcp-r:fill_title_block (list (cons "block" b) (cons tag val)))))
  (princ))
