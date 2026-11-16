;;; name: plot_pdf
;;; desc-en: Plot to PDF next to the drawing (or in out_dir): model space extents fit to paper, or one PDF per layout (sheet).
;;; desc-pt: Plota em PDF ao lado do desenho (ou em out_dir): a extensão do modelo ajustada ao papel, ou um PDF por folha (layout).
;;; args: out_dir (folder, default = drawing folder), layouts ("" = model space only (default); "all" = every
;;;       sheet; or names with wildcards, comma separated: one <drawing>-<layout>.pdf each),
;;;       paper (default "ISO full bleed A3 (420.00 x 297.00 MM)"), ctb (default "monochrome.ctb"),
;;;       orientation ("L"/"P", default "L"). A sheet already set to a PDF device keeps its own page setup;
;;;       paper, ctb and orientation are used for the model and for sheets on another device (plotted 1:1).

(defun mcp:layouts (/ d l m out)
  ;; sheets as (tab-order name device), in tab order. Reads the layout dictionary: (layoutlist) does not
  ;; exist in accoreconsole.
  (setq d (dictsearch (namedobjdict) "ACAD_LAYOUT"))
  (foreach x d
    (if (= (car x) 350)
      (progn
        (setq l (entget (cdr x)) m (member (quote (100 . "AcDbLayout")) l))
        (if (/= (cdr (assoc 71 m)) 0)
          (setq out (cons (list (cdr (assoc 71 m)) (cdr (assoc 1 m)) (cdr (assoc 2 l))) out))))))
  (vl-sort out (function (lambda (a b) (< (car a) (car b))))))

(defun mcp-r:plot_pdf (args / dir paper ctb ori lays base pdf n bad)
  (setq dir (mcp:arg args "out_dir" (getvar "DWGPREFIX"))
        paper (mcp:arg args "paper" "ISO full bleed A3 (420.00 x 297.00 MM)")
        ctb (mcp:arg args "ctb" "monochrome.ctb")
        ori (mcp:arg args "orientation" "L")
        lays (mcp:arg args "layouts" ""))
  (if (not (wcmatch dir "*[\\/]")) (setq dir (strcat dir "/")))
  (setq base (strcat dir (vl-filename-base (getvar "DWGNAME"))))
  (if (= lays "")
    (progn
      (setq pdf (strcat base ".pdf"))
      (command "_.-PLOT" "_Y" "Model" "DWG To PDF.pc3" paper "_M" ori "_N" "_E" "_F" "_C" "_Y" ctb "_Y" "_A"
               pdf "_N" "_Y")
      (if (findfile pdf) (strcat "pdf: " pdf) "plot failed"))
    (progn
      (setq n 0 bad "")
      (if (= (strcase lays) "ALL") (setq lays "*"))
      (foreach l (mcp:layouts)
        (if (wcmatch (strcase (cadr l)) (strcase lays))
          (progn
            (setq pdf (strcat base "-" (cadr l) ".pdf"))
            (if (wcmatch (strcase (caddr l)) "*PDF*")
              (command "_.-PLOT" "_N" (cadr l) "" (caddr l) pdf "_N" "_Y")
              (command "_.-PLOT" "_Y" (cadr l) "DWG To PDF.pc3" paper "_M" ori "_N" "_L" "1:1" "0,0" "_Y" ctb
                       "_Y" "_N" "_N" "_N" pdf "_N" "_Y"))
            (if (findfile pdf) (setq n (1+ n)) (setq bad (strcat bad " " (cadr l)))))))
      (strcat (itoa n) " pdf(s)" (if (= bad "") "" (strcat "; failed:" bad))))))

(defun c:MCP_PLOT_PDF () (princ (strcat "\n" (mcp-r:plot_pdf nil))) (princ))
