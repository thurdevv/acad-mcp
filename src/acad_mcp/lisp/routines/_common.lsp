;;; Shared helpers for acad-mcp routines (pure AutoLISP: works in AutoCAD and in accoreconsole).
;;; Every routine is a function mcp-r:<name> taking one argument: an association list of
;;; parameters, e.g. '(("find" . "OLD") ("replace" . "NEW")). It returns a short result string.

(defun mcp:arg (args key default / v)
  (if (setq v (assoc key args)) (cdr v) default))

(defun mcp:csv-cell (s / r)
  ;; quote a CSV cell: "a;b" -> "\"a;b\""
  (setq s (if (= (type s) 'STR) s (vl-princ-to-string s)))
  (if (or (vl-string-search ";" s) (vl-string-search "," s) (vl-string-search "\"" s))
    (strcat "\"" (vl-string-subst-all "\"\"" "\"" s) "\"")
    s))

(defun vl-string-subst-all (new old s / p)
  (setq p 0)
  (while (setq p (vl-string-search old s p))
    (setq s (strcat (substr s 1 p) new (substr s (+ p 1 (strlen old))))
          p (+ p (strlen new))))
  s)

(defun mcp:dwg-name () (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")))

(defun mcp:append-line (path line / f)
  (setq f (open path "a"))
  (write-line line f)
  (close f))

(defun mcp:inserts (pattern / ss)
  ;; block references whose name matches pattern (wildcards); dynamic blocks are matched by
  ;; their effective name when ActiveX is available (AutoCAD), by internal name in the console
  (setq ss (ssget "_X" (list (cons 0 "INSERT"))))
  (if ss (mcp:filter-inserts ss pattern) nil))

(defun mcp:effname (e / r)
  (setq r (vl-catch-all-apply '(lambda () (vla-get-EffectiveName (vlax-ename->vla-object e)))))
  (if (vl-catch-all-error-p r) (cdr (assoc 2 (entget e))) r))

(defun mcp:filter-inserts (ss pattern / i e out)
  (setq i 0)
  (repeat (sslength ss)
    (setq e (ssname ss i) i (1+ i))
    (if (wcmatch (strcase (mcp:effname e)) (strcase pattern)) (setq out (cons e out))))
  (reverse out))

(defun mcp:attribs (e / n ed out)
  (setq n (entnext e))
  (while (and n (= "ATTRIB" (cdr (assoc 0 (setq ed (entget n))))))
    (setq out (cons (cons (cdr (assoc 2 ed)) n) out) n (entnext n)))
  (reverse out))
