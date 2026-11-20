---
name: pdf-extract
description: Read the contents of a PDF — text, tables, page images, metadata — so it can be quoted, summarized, searched, fact-checked or converted. Use whenever a .pdf file needs its content read: "what does this PDF say", "extract the text", "pull the table out of this report", "summarize this paper", "is this invoice signed". Covers scanned/image-only PDFs via OCR, and records which extraction routes work.
---

# PDF → text

## The fast path

```bash
scripts/pdf-extract.sh report.pdf > report.txt
```

It tries the text layer first (`pdftotext`) and silently falls back to OCR
(`pdftoppm` + `tesseract`) when the PDF is a scan. Options:

| flag | use |
| --- | --- |
| `--layout` | preserve columns/tables — **always use this for tabular pages** |
| `--pages 3-7` | one range only; do this before OCR-ing a 200-page document |
| `--ocr` | force OCR even when a text layer exists (it may be garbage) |
| `--no-ocr` | fail loudly instead of OCR-ing — use when speed matters |
| `--lang fra` / `--lang eng+fra` | OCR language; image only ships `eng` and `fra` |
| `--dpi 400` | raise for small print, lower to 200 for speed |
| `-o out.txt` | write to a file instead of stdout |

## Before extracting, look at what you have

```bash
pdfinfo report.pdf              # page count, producer, encryption, page size
pdftotext -f 1 -l 1 report.pdf - | head -40   # is there a text layer at all?
```

`pdfinfo` costs nothing and tells you whether the job is 3 pages or 300, and
whether it's encrypted. A PDF whose first page yields nothing but whitespace is
a scan — go straight to `--ocr`.

## Choosing the right tool

- **Prose** — plain `pdf-extract.sh`. Default `pdftotext` reflows columns in
  reading order.
- **Tables, invoices, financial statements, anything in columns** — `--layout`.
  Without it, columns interleave and numbers land on the wrong row. Verify a
  couple of extracted rows against the page before trusting a table.
- **Figures, charts, stamps, signatures, handwriting, "what does this page look
  like"** — do not extract text, *look* at the page. Render it and read the
  image:
  ```bash
  pdftoppm -r 150 -png -f 4 -l 4 report.pdf page   # -> page-04.png
  ```
  Then open `page-04.png` with the Read tool. This is the only way to judge
  layout, charts or anything visual — and it is often the fastest way to answer
  "is there a signature on page 12".
- **Embedded images at original resolution** — `pdfimages -png report.pdf img`.
- **Forms** — `pdftotext` drops field values in some PDFs; if a filled form
  extracts empty, render the page and read it instead.

## Gotchas

**Check the character count, not just "did it error".** `pdftotext` exits 0 on
an image-only PDF and prints only form feeds. The script already guards on this
(≥32 alphanumeric characters); if you call `pdftotext` yourself, guard too.

**OCR a 300-page scan only after narrowing the range.** ~1–3 s/page at 300 DPI.
Find the relevant pages first — OCR page 1 and the table of contents, or render
a few pages and look.

**OCR mangles proper nouns, digits and money.** `1` vs `l`, `0` vs `O`,
`5` vs `S`. Never quote an OCR'd figure, name or reference number as exact
without rendering that page and checking it visually. Say when a number came
from OCR.

**Encrypted PDFs**: `pdfinfo` shows `Encrypted: yes`. If it is only
owner-password protected, `pdftotext -upw ""` usually still works. Do not try to
break a user password — ask for it.

**Page numbers**: `pdftoppm`/`pdftotext` count physical pages from 1; that often
differs from the number printed on the page. When citing, say which you mean.

**Ligatures**: `ﬁ`/`ﬂ` come through as single code points from some producers.
Normalize before grepping for "file"/"final" — `sed 's/ﬁ/fi/g; s/ﬂ/fl/g'`.

## Toolchain present in this image

`pdftotext`, `pdfinfo`, `pdfimages`, `pdftoppm`, `pdfseparate`, `pdfunite`
(poppler-utils) · `tesseract` with `eng`+`fra` · `pandoc` for converting the
extracted text onward.

For structured extraction beyond this (word coordinates, ruling lines), grab
`pdfplumber` on demand — `uv` is installed:
`uv run --with pdfplumber python -c '...'`.
