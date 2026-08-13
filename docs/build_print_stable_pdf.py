#!/usr/bin/env python3
"""Build the print-stable trySearch TRD PDF from a rendered DOCX PDF.

LibreOffice can place continuation-page content too close to the physical page
edge. Ghostscript first normalizes every body page into a fixed safe area.
LaTeX then includes those pages as intact PDF objects and overlays the running
header, avoiding any rewrite of the source page resource dictionaries.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


BODY_SCALE = 0.88


TEX_DOCUMENT = r"""\documentclass[letterpaper]{article}
\usepackage[margin=0in]{geometry}
\usepackage{graphicx}
\usepackage{pdfpages}
\usepackage{tikz}
\usepackage{xcolor}
\usepackage[pdfusetitle,hidelinks]{hyperref}
\pagestyle{empty}
\definecolor{tryorange}{HTML}{FF6A13}
\definecolor{trymuted}{HTML}{5F6368}
\hypersetup{
  pdftitle={trySearch Technical Requirements Document},
  pdfauthor={trySearch Engineering},
  pdfsubject={Complete website and evidence-backed AEO/GEO analytics platform},
  pdfkeywords={trySearch, TRD, AEO, GEO, analytics, Flask, PostgreSQL, Perplexity, Google Search Console}
}
\newcommand{\TRDOverlay}{%
  \begin{tikzpicture}[remember picture,overlay]
    % Supply a page number when the office renderer omits one on a
    % continuation page; the scaled source PDF retains its running header.
    \fill[white]
      (current page.south west) rectangle
      ([yshift=0.38in]current page.south east);
    \node[anchor=south east] at
      ([xshift=-0.85in,yshift=0.16in]current page.south east) {%
      \sffamily\fontsize{6.5}{7.5}\selectfont\color{trymuted}
      trySearch TRD\quad|\quad\thepage%
    };
  \end{tikzpicture}%
}
\begin{document}
\includepdf[pages=1,fitpaper=true,pagecommand={\thispagestyle{empty}}]{source.pdf}
\includepdf[pages=2-,fitpaper=true,pagecommand={\thispagestyle{empty}},picturecommand={\TRDOverlay}]{normalized.pdf}
\end{document}
"""


def command_path(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Required command not found: {name}")
    return path


def normalize_pdf(source: Path, destination: Path, logo_path: Path) -> None:
    ghostscript = command_path("gs")
    pdflatex = command_path("pdflatex")

    with tempfile.TemporaryDirectory(prefix="trysearch-trd-pdf-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        local_source = temp_dir / "source.pdf"
        local_logo = temp_dir / "logo.png"
        normalized = temp_dir / "normalized.pdf"
        tex_path = temp_dir / "print.tex"
        shutil.copy2(source, local_source)
        shutil.copy2(logo_path, local_logo)

        # Rewrite page streams and center the full original page inside a stable
        # safe rectangle. The cover is later taken from local_source unscaled.
        x_offset = 612 * (1 - BODY_SCALE) / 2
        y_offset = 792 * (1 - BODY_SCALE) / 2
        subprocess.run(
            [
                ghostscript,
                "-q",
                "-dBATCH",
                "-dNOPAUSE",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.7",
                "-dFIXEDMEDIA",
                "-dDEVICEWIDTHPOINTS=612",
                "-dDEVICEHEIGHTPOINTS=792",
                f"-sOutputFile={normalized}",
                "-c",
                f"<</Install {{{x_offset:.3f} {y_offset:.3f} translate {BODY_SCALE} {BODY_SCALE} scale}}>> setpagedevice",
                "-f",
                str(local_source),
            ],
            check=True,
        )

        tex_path.write_text(TEX_DOCUMENT, encoding="utf-8")
        subprocess.run(
            [
                pdflatex,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(temp_dir),
                str(tex_path),
            ],
            cwd=temp_dir,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        built_pdf = temp_dir / "print.pdf"
        if not built_pdf.is_file():
            raise SystemExit("PDF composition completed without producing print.pdf")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built_pdf, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Rendered source PDF")
    parser.add_argument("destination", type=Path, help="Final print-stable PDF")
    parser.add_argument("--logo", required=True, type=Path, help="Header logo image")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for path in (args.source, args.logo):
        if not path.is_file():
            raise SystemExit(f"Required file not found: {path}")
    normalize_pdf(args.source.resolve(), args.destination.resolve(), args.logo.resolve())


if __name__ == "__main__":
    main()
