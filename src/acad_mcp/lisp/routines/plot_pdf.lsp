;;; name: plot_pdf
;;; desc-en: Plot model space extents to a PDF next to the drawing (or in out_dir), fit to paper.
;;; desc-pt: Plota a extensão do espaço modelo em PDF ao lado do desenho (ou em out_dir), ajustado ao papel.
;;; args: out_dir (folder, default = drawing folder), paper (default "ISO full bleed A3 (420.00 x 297.00 MM)"),
;;;       ctb (default "monochrome.ctb"), orientation ("L"/"P", default "L")

(defun mcp-r:plot_pdf (args / dir paper ctb ori pdf)
  (setq dir (mcp:arg args "out_dir" (getvar "DWGPREFIX"))
        paper (mcp:arg args "paper" "ISO full bleed A3 (420.00 x 297.00 MM)")
        ctb (mcp:arg args "ctb" "monochrome.ctb")
        ori (mcp:arg args "orientation" "L"))
  (if (not (wcmatch dir "*[\\/]")) (setq dir (strcat dir "/")))
  (setq pdf (strcat dir (vl-filename-base (getvar "DWGNAME")) ".pdf"))
  (command "_.-PLOT" "_Y" "Model" "DWG To PDF.pc3" paper "_M" ori "_N" "_E" "_F" "_C" "_Y" ctb "_Y" "_A"
           pdf "_N" "_Y")
  (if (findfile pdf) (strcat "pdf: " pdf) "plot failed"))

(defun c:MCP_PLOT_PDF () (princ (strcat "\n" (mcp-r:plot_pdf nil))) (princ))
