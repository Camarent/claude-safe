---
name: md-to-pdf
description: Turn Markdown into a real PDF — reports, notes, memos, papers, invoices, anything to print, hand over or attach. Use whenever a document must leave as a .pdf: "make this a PDF", "export the report", "print-ready version", "send them a PDF of these notes". Uses pandoc + typst (no LaTeX) and encodes the flags, fonts and failure modes that actually matter.
---

# Markdown → PDF

## The fast path

```bash
scripts/md2pdf.sh report.md                  # -> report.pdf
scripts/md2pdf.sh --title "Q3 Review" --author "…" --date today --toc report.md
```

The wrapper drives `pandoc --pdf-engine=typst` with settings that produce a
document someone can hand over without apologising for it. Options:

| flag | effect |
| --- | --- |
| `--title` / `--author` / `--date` | title block; `--date today` fills in the date |
| `--toc` | table of contents, depth 3 |
| `--number-sections` | numbered headings (1, 1.1, 1.1.1) |
| `--no-page-numbers` | page numbers are **on** by default — this removes them |
| `--papersize a4\|us-letter\|a5` | default `a4` |
| `--margin 2cm` · `--fontsize 10pt` | defaults `2.5cm` / `11pt` |
| `--font "Noto Serif"` | body font — must be in `typst fonts` |
| `--landscape` · `--columns 2` | rotated page · multi-column body |
| `--theme kate` | code highlighting theme (`pandoc --list-highlight-styles`) |
| `--keep-typst` | also write the `.typ` — read it when output looks wrong |
| `-- …` | anything after `--` goes straight to pandoc |

Front matter in the `.md` (`title:`, `subtitle:`, `author:`, `abstract:`,
`keywords:`) is picked up too; the flags override it.

## What renders correctly (verified)

Headings · **bold**/*italic*/`code` · pipe tables with alignment · fenced code
with syntax highlighting · ordered/nested/task lists · footnotes · block quotes ·
horizontal rules · links · smart quotes and dashes · `$inline$` and `$$display$$`
math · local images · accented Latin, Greek, Cyrillic · colour emoji.

## Gotchas

**`![caption](img.png)` becomes a numbered figure.** The alt text turns into
"Figure 1: caption" and a tall image floats to a page of its own. For a plain
inline picture, leave the alt empty and size it:
`![](chart.png){width=60%}`.

**Image paths resolve relative to the `.md`**, not your shell — the wrapper sets
`--resource-path` for this. Remote image URLs are **not** fetched; download
first, then reference the local file.

**CJK renders as tofu boxes (□□□).** The image ships Noto for Latin, Greek,
Cyrillic, Arabic, Hebrew, Thai and colour emoji, but not CJK. If a document
needs Chinese/Japanese/Korean:
`apt-get update && apt-get install -y fonts-noto-cjk` then
`--font "Noto Serif CJK JP"`. Check the emoji/script actually landed by
rendering a page to PNG and looking at it (see below) — tofu is invisible in a
text diff.

**`pdfinfo` prints `Syntax Error: Suspects object is wrong type (boolean)` on
every typst-produced PDF.** It is a poppler complaint about a legal-but-unusual
object; the PDF is fine. Ignore it — don't go debugging it.

**No raw LaTeX.** `\newcommand`, `\begin{tabular}` etc. are dropped silently.
Math itself works (typst converts it), but exotic macros do not. If a document
truly needs LaTeX, say so rather than shipping a PDF with pieces missing.

**Always look at the result before handing it over:**
```bash
pdftoppm -r 100 -png -f 1 -l 2 report.pdf page   # then Read page-1.png
```
Missing glyphs, overflowing tables and runaway images only show up visually.
A wide table is the usual offender — fix it with `--landscape`, a smaller
`--fontsize`, or by shortening the column headers.

## Going beyond the wrapper

`--keep-typst` gives you the intermediate `.typ`; `typst compile out.typ` from
there, and typst's own syntax is available for anything the Markdown can't say.
For one-off tweaks without leaving Markdown, inject raw typst:

```bash
scripts/md2pdf.sh doc.md -- -V header-includes='#set par(justify: true)'
```

Useful ones: `#set par(justify: true)` (justified text),
`#set page(header: [Draft — confidential])`, `#set text(hyphenate: true)`.

## Toolchain present in this image

`pandoc 3.11` · `typst 0.15.1` · poppler (`pdftoppm`, `pdfinfo`) for checking
the output · fonts: DejaVu, Liberation, Libertinus, New Computer Modern, Noto
(core + colour emoji).

Other directions: `pandoc doc.md -o doc.docx` / `.html` / `.epub` need no extra
tooling. Reading an existing PDF is the [[pdf-extract]] skill.
