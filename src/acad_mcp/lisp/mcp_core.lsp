;;; acad-mcp core - fast in-process reading of entities.
;;; Pure AutoLISP + vlax-curve-* (both work in AutoCAD and in accoreconsole).
;;; vla-* is used only inside vl-catch-all-apply (unavailable in accoreconsole).
;;; Output: one JSON object per line (JSON Lines). DIMZIN 8 keeps numbers valid JSON.
;;; Pieces are written straight to the file (no big strcat chains): much faster.

(setq mcp:version "0.4")

(defun mcp:o (x) (princ x mcp:f))

(defun mcp:esc (s)
  (if (or (vl-string-position 34 s) (vl-string-position 92 s)
          (vl-string-position 9 s) (vl-string-position 10 s) (vl-string-position 13 s))
    (vl-list->string
      (apply 'append
             (mapcar '(lambda (c)
                        (cond ((= c 34) '(92 34)) ((= c 92) '(92 92)) ((< c 32) '(32)) (T (list c))))
                     (vl-string->list s))))
    s))

(defun mcp:n (v) (rtos v 2 6))
(defun mcp:p (p) (mcp:o "[") (mcp:o (mcp:n (car p))) (mcp:o ",") (mcp:o (mcp:n (cadr p))) (mcp:o "]"))
(defun mcp:k (k) (mcp:o ",\"") (mcp:o k) (mcp:o "\":"))
(defun mcp:ks (k v) (mcp:k k) (mcp:o "\"") (mcp:o (mcp:esc v)) (mcp:o "\""))
(defun mcp:kn (k v) (mcp:k k) (mcp:o (mcp:n v)))
(defun mcp:kp (k p) (mcp:k k) (mcp:p p))

(defun mcp:curve-len (e / r)
  (setq r (vl-catch-all-apply 'vlax-curve-getDistAtParam (list e (vlax-curve-getEndParam e))))
  (if (vl-catch-all-error-p r) nil r))

(defun mcp:curve-area (e / r)
  (setq r (vl-catch-all-apply 'vlax-curve-getArea (list e)))
  (if (vl-catch-all-error-p r) nil r))

(defun mcp:effname (e / r)
  (setq r (vl-catch-all-apply '(lambda () (vla-get-EffectiveName (vlax-ename->vla-object e)))))
  (if (vl-catch-all-error-p r) nil r))

(defun mcp:attribs (e / first n ed)
  (mcp:k "att") (mcp:o "{")
  (setq first T n (entnext e))
  (while (and n (= "ATTRIB" (cdr (assoc 0 (setq ed (entget n))))))
    (if first (setq first nil) (mcp:o ","))
    (mcp:o "\"") (mcp:o (mcp:esc (cdr (assoc 2 ed)))) (mcp:o "\":\"") (mcp:o (mcp:esc (cdr (assoc 1 ed)))) (mcp:o "\"")
    (setq n (entnext n)))
  (mcp:o "}"))

(defun mcp:mtext (ed / s)
  (setq s "")
  (foreach x ed (if (= (car x) 3) (setq s (strcat s (cdr x)))))
  (strcat s (cdr (assoc 1 ed))))

;;; mode "light": handle/type/layer/color (+ block name); "full": geometry, lengths, areas, attributes
(defun mcp:rec (e mode / ed typ v nv closed first full)
  (setq ed (entget e) typ (cdr (assoc 0 ed)) full (= mode "full"))
  (mcp:o "{\"h\":\"") (mcp:o (cdr (assoc 5 ed))) (mcp:o "\",\"t\":\"") (mcp:o typ) (mcp:o "\"")
  (mcp:ks "l" (cdr (assoc 8 ed)))
  (if (setq v (assoc 62 ed)) (progn (mcp:k "c") (mcp:o (itoa (cdr v)))))
  (if (setq v (assoc 420 ed)) (progn (mcp:k "rgb") (mcp:o (itoa (cdr v)))))
  (if (/= (cdr (assoc 410 ed)) "Model") (mcp:ks "lay" (cdr (assoc 410 ed))))
  (if (= typ "INSERT")
    (progn
      (mcp:ks "n" (cdr (assoc 2 ed)))
      (if (and (wcmatch (cdr (assoc 2 ed)) "`**") (setq v (mcp:effname e))) (mcp:ks "en" v))))
  (if full
    (cond
      ((= typ "LINE") (mcp:kp "a" (cdr (assoc 10 ed))) (mcp:kp "b" (cdr (assoc 11 ed))))
      ((member typ '("CIRCLE" "ARC"))
       (mcp:kp "ctr" (cdr (assoc 10 ed))) (mcp:kn "r" (cdr (assoc 40 ed)))
       (if (= typ "ARC") (progn (mcp:kn "a0" (cdr (assoc 50 ed))) (mcp:kn "a1" (cdr (assoc 51 ed))))))
      ((= typ "LWPOLYLINE")
       (setq closed (= 1 (logand 1 (cdr (assoc 70 ed)))) nv (cdr (assoc 90 ed)))
       (mcp:k "cl") (mcp:o (if closed "true" "false"))
       (mcp:k "nv") (mcp:o (itoa nv))
       (if (<= nv 50)
         (progn
           (mcp:k "v") (mcp:o "[") (setq first T)
           (foreach x ed
             (if (= (car x) 10) (progn (if first (setq first nil) (mcp:o ",")) (mcp:p (cdr x)))))
           (mcp:o "]"))))
      ((member typ '("TEXT" "MTEXT"))
       (mcp:ks "x" (if (= typ "MTEXT") (mcp:mtext ed) (cdr (assoc 1 ed))))
       (mcp:kp "p" (cdr (assoc 10 ed))) (mcp:kn "ht" (cdr (assoc 40 ed)))
       (mcp:kn "rot" (if (assoc 50 ed) (cdr (assoc 50 ed)) 0.0)))
      ((= typ "INSERT")
       (mcp:kp "p" (cdr (assoc 10 ed))) (mcp:kn "rot" (cdr (assoc 50 ed))) (mcp:kn "sc" (cdr (assoc 41 ed)))
       (if (= 1 (cdr (assoc 66 ed))) (mcp:attribs e)))
      ((= typ "DIMENSION") (mcp:kn "m" (cdr (assoc 42 ed))))
      ((= typ "HATCH") (mcp:ks "pat" (cdr (assoc 2 ed))))
      ((= typ "POINT") (mcp:kp "p" (cdr (assoc 10 ed))))
    ))
  (if (and full (member typ '("LINE" "LWPOLYLINE" "POLYLINE" "CIRCLE" "ARC" "ELLIPSE" "SPLINE")))
    (progn
      (if (setq v (mcp:curve-len e)) (mcp:kn "len" v))
      (if (and (or (member typ '("CIRCLE" "ELLIPSE")) closed
                   (and (= typ "POLYLINE") (= 1 (logand 1 (cdr (assoc 70 ed))))))
               (setq v (mcp:curve-area e)))
        (mcp:kn "ar" v))))
  (if (and full (= typ "HATCH"))
    (progn
      (setq v (vl-catch-all-apply '(lambda () (vla-get-Area (vlax-ename->vla-object e)))))
      (if (not (vl-catch-all-error-p v)) (mcp:kn "ar" v))))
  (mcp:o "}\n"))

;;; Query: flt = ssget filter list (or nil), path = output file, mode = "light"/"full",
;;; skip/limit = page (limit 0 = all). Returns the total matched.
(defun mcp:query (flt path mode skip limit / ss i n total oldz)
  ;; DIMZIN bit 4 suppresses leading zeros (".5" is invalid JSON); only touch it then,
  ;; because changing a variable adds an undo record.
  (setq oldz (getvar "DIMZIN"))
  (if (= 4 (logand oldz 4)) (setvar "DIMZIN" (- oldz 4)))
  (setq ss (if flt (ssget "_X" flt) (ssget "_X")))
  (setq total (if ss (sslength ss) 0) mcp:f (open path "w") i skip n 0)
  (if ss
    (while (and (< i total) (or (= limit 0) (< n limit)))
      (mcp:rec (ssname ss i) mode)
      (setq i (1+ i) n (1+ n))))
  (close mcp:f)
  (setq mcp:f nil)
  (if (/= oldz (getvar "DIMZIN")) (setvar "DIMZIN" oldz))
  total)

(princ)
