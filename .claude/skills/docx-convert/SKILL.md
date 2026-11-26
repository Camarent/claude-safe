---
name: docx-convert
description: Convert Word and other Office documents — .docx, .doc, .odt, .rtf, .xlsx, .pptx — into PDF, or back out into an editable/redactable format like Markdown. Use whenever an Office file has to change shape: "turn this Word doc into a PDF", "convert the contract", "I need to edit/redact this .docx", "make these slides a PDF", "batch-convert this folder". Runs fully offline via LibreOffice headless — no Google Docs, no upload.
---

# Office documents → PDF (and back)

## The fast path

```bash
scripts/docx-convert.sh contract.docx            # -> contract.pdf, layout intact
scripts/docx-convert.sh --to md contract.docx    # -> contract.md, editable
scripts/docx-convert.sh -o out/ *.docx           # batch
```

| flag | use |
| --- | --- |
| `--to FMT` | `pdf` (default), `docx`, `odt`, `md`, `txt`, `html`, `rtf`, `xlsx`, `csv`, `pptx` |
| `-o DIR` | output directory (default: next to the input) |
| `--pdf-a` | PDF/A-2b, for archival or filing |
| `--range 2-5` | export only these pages |
| `--force` | overwrite an existing output file (refused by default) |
| `--timeout N` | per-file ceiling, default 180s |

## Which route for which job

The script picks the engine for you, but knowing which one ran tells you what
to trust in the output.

- **Handing the document to someone, printing, filing, attaching** — `--to pdf`.
  Goes through LibreOffice, which lays the document out the way Word does:
  page breaks, headers/footers, column widths, floating images, numbering.
- **Reading, editing, redacting, diffing, feeding into another document** —
  `--to md`. Goes through pandoc, which reads the `.docx` XML directly and
  gives real headings, pipe tables, footnotes and links. Layout is *discarded*
  by design — that is the point. Embedded images land in `<stem>-media/`.
  From there, [[md-to-pdf]] takes the edited Markdown back to PDF.
- **Quoting text with no structure needed** — `--to txt`.
- **Spreadsheets** — `.xlsx`/`.csv` go through Calc; `--to csv` exports **only
  the first sheet**. For a multi-sheet workbook, convert to `.xlsx` and read it
  with a library instead.

## Gotchas

**The exit code is not the test — the output file is.** `soffice` exits 0 when
its import filter bails and writes nothing at all. The script already checks
that a non-empty file appeared; if you call `soffice` by hand, check too.

**Never run two `soffice` processes against one user profile.** The second one
silently attaches to the first and no-ops, or hangs until killed. The script
gives every invocation a private `-env:UserInstallation` profile in a temp dir.
This is also why batching several files in *one* call is right and looping the
script in a shell `for` is wrong.

**Fonts decide pagination.** The image ships Carlito and Caladea, metric clones
of Calibri and Cambria, so ordinary Office documents paginate correctly. Any
*other* missing font (Garamond, Futura, a corporate face) gets substituted and
the page count can shift. Check `soffice`'s choice by looking at the result —
never assert "the clause is on page 4" from a converted PDF without rendering
that page.

**Tracked changes and comments render as they are currently displayed** — that
usually means markup bars and balloons land in the PDF. Accept or reject the
changes in the source first if you want clean output. Going `--to md` drops
comments entirely and silently: never treat a Markdown conversion as evidence
that a document has no comments.

**`.doc` (pre-2007) and `.rtf` import approximately.** Complex old documents
lose text boxes and field codes. Render a page and look before trusting one.

**`--to md` next to the source Markdown will collide.** `report.docx` converts
to `report.md` — the exact name a hand-written `report.md` beside it already
has. The script refuses to overwrite and tells you to pass `--force` or `-o`;
take the `-o` unless you are certain the existing file is disposable.

**Redaction means deleting text, not covering it.** If the ask is "black out
these names", drawing boxes over a PDF leaves the text extractable underneath.
Do it properly: `--to md`, delete the words, then [[md-to-pdf]] back to PDF.

**Always look at the result:**
```bash
pdftoppm -r 100 -png -f 1 -l 2 contract.pdf page   # then Read page-1.png
```
Substituted fonts, overflowing tables and dropped images only show up visually.

## Toolchain present in this image

`soffice` (LibreOffice Writer/Calc/Impress, headless, no Java) · `pandoc 3.11`
for the text-shaped targets · Carlito + Caladea metric fonts · poppler
(`pdftoppm`, `pdfinfo`) for checking output.

Reading a PDF's contents is [[pdf-extract]]. Markdown → PDF is [[md-to-pdf]].
