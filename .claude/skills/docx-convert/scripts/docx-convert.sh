#!/usr/bin/env bash
#
# docx-convert.sh — Office document -> PDF (or an editable format), offline.
#
# Layout-faithful conversions go through LibreOffice headless. Text-only
# targets (md/txt) go through pandoc, which produces far cleaner markup than
# LibreOffice's own writer filters.
#
set -uo pipefail

progName="$(basename "$0")"

die() { echo -e "\033[31m$progName: $*\033[0m" >&2; exit 1; }
warn() { echo -e "\033[33m$progName: $*\033[0m" >&2; }

usage() {
    cat <<'USAGE'
Usage: docx-convert.sh [options] <file> [file...]

  --to FMT        pdf (default) | docx | odt | md | txt | html | rtf | xlsx | csv | pptx
  -o, --outdir D  where to write (default: alongside the input file)
  --pdf-a         PDF/A-2b archival output (pdf target only)
  --range 2-5     export only these pages (pdf target only)
  --quiet         suppress the per-file progress line
  -f, --force     overwrite an existing output file (refused by default)
  --timeout N     seconds to allow per file (default 180)

Examples:
  docx-convert.sh contract.docx                   # -> contract.pdf
  docx-convert.sh --to md contract.docx           # -> contract.md (editable)
  docx-convert.sh -o out/ *.docx                  # batch
  docx-convert.sh --pdf-a --range 1-3 report.doc
USAGE
}

target="pdf"
outDir=""
pdfA=false
pageRange=""
quiet=false
force=false
timeoutSecs=180
files=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --to)       target="${2:-}"; [[ -n "$target" ]] || die "--to needs a format"; shift 2 ;;
        -o|--outdir) outDir="${2:-}"; [[ -n "$outDir" ]] || die "--outdir needs a path"; shift 2 ;;
        --pdf-a)    pdfA=true; shift ;;
        --range)    pageRange="${2:-}"; shift 2 ;;
        --quiet)    quiet=true; shift ;;
        -f|--force) force=true; shift ;;
        --timeout)  timeoutSecs="${2:-}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        --)         shift; files+=("$@"); break ;;
        -*)         die "unknown option '$1' (see --help)" ;;
        *)          files+=("$1"); shift ;;
    esac
done

[[ ${#files[@]} -gt 0 ]] || { usage >&2; exit 1; }
[[ "$pdfA" == true && "$target" != "pdf" ]] && die "--pdf-a only applies to --to pdf"
[[ -n "$pageRange" && "$target" != "pdf" ]] && die "--range only applies to --to pdf"

# LibreOffice refuses to run two instances against one profile and will either
# hang or silently no-op. Give every invocation a private, disposable profile.
profileDir="$(mktemp -d)"
cleanup() { rm -rf "$profileDir"; }
trap cleanup EXIT

# Pandoc handles the text-shaped targets; everything else is LibreOffice.
usePandoc=false
case "$target" in
    md|markdown|txt|text) usePandoc=true ;;
esac

# LibreOffice filter names: the bare extension works for most, but PDF options
# and the "which HTML writer" question need the explicit filter.
lo_filter() {
    case "$1" in
        pdf)  echo "pdf" ;;
        docx) echo "docx:MS Word 2007 XML" ;;
        odt)  echo "odt" ;;
        rtf)  echo "rtf:Rich Text Format" ;;
        html) echo "html:HTML (StarWriter)" ;;
        xlsx) echo "xlsx:Calc MS Excel 2007 XML" ;;
        csv)  echo "csv" ;;
        pptx) echo "pptx:Impress MS PowerPoint 2007 XML" ;;
        *)    echo "$1" ;;
    esac
}

# PDF export options ride along as a JSON-ish blob appended to the filter name.
pdf_filter() {
    local opts=()
    [[ "$pdfA" == true ]] && opts+=('"SelectPdfVersion":{"type":"long","value":"2"}')
    [[ -n "$pageRange" ]] && opts+=("\"PageRange\":{\"type\":\"string\",\"value\":\"$pageRange\"}")
    if [[ ${#opts[@]} -eq 0 ]]; then
        echo "pdf:writer_pdf_Export"
        return
    fi
    local joined
    joined="$(IFS=,; echo "${opts[*]}")"
    echo "pdf:writer_pdf_Export:{$joined}"
}

failures=0

for src in "${files[@]}"; do
    [[ -f "$src" ]] || { warn "no such file: $src"; failures=$((failures+1)); continue; }

    srcDir="$(cd "$(dirname "$src")" && pwd)"
    base="$(basename "$src")"
    stem="${base%.*}"
    dest="${outDir:-$srcDir}"
    mkdir -p "$dest" || die "cannot create output dir: $dest"
    destAbs="$(cd "$dest" && pwd)"

    ext="${target}"
    case "$target" in markdown) ext="md" ;; text) ext="txt" ;; esac
    out="$destAbs/$stem.$ext"

    # docx -> md lands on "<stem>.md", which is exactly the name a source
    # Markdown file next to it would have. Clobbering that is unrecoverable.
    if [[ -e "$out" && "$force" != true ]]; then
        if [[ "$out" -ef "$srcDir/$base" ]]; then
            warn "$base: input and output are the same file — pick --to or -o"
        else
            warn "$(basename "$out") already exists — pass --force to overwrite"
        fi
        failures=$((failures+1)); continue
    fi

    [[ "$quiet" == true ]] || echo -e "\033[36m$base -> $stem.$ext\033[0m"

    if [[ "$usePandoc" == true ]]; then
        # Extract embedded images next to the output rather than dropping them.
        mediaDir="$destAbs/${stem}-media"
        pandocTo="markdown+pipe_tables-simple_tables-multiline_tables-grid_tables"
        [[ "$target" == "txt" || "$target" == "text" ]] && pandocTo="plain"

        pandocArgs=(--from=docx --to="$pandocTo" --wrap=none -o "$out")
        [[ "$pandocTo" == plain ]] || pandocArgs+=(--extract-media="$mediaDir")

        case "${base,,}" in
            *.docx|*.odt) ;;
            *) warn "$base: pandoc only reads .docx/.odt directly — converting via LibreOffice first"
               tmpDocx="$profileDir/$stem.docx"
               if ! timeout "$timeoutSecs" soffice --headless --norestore \
                        -env:UserInstallation="file://$profileDir/lo" \
                        --convert-to "docx:MS Word 2007 XML" \
                        --outdir "$profileDir" "$srcDir/$base" >/dev/null 2>&1 \
                    || [[ ! -f "$tmpDocx" ]]; then
                   warn "$base: could not normalise to .docx"
                   failures=$((failures+1)); continue
               fi
               src="$tmpDocx"; srcDir="$profileDir" ;;
        esac
        [[ "${src##*.}" == "odt" ]] && pandocArgs[0]="--from=odt"

        if ! pandoc "${pandocArgs[@]}" "$src"; then
            warn "$base: pandoc failed"
            failures=$((failures+1)); continue
        fi
        # An empty media dir is just noise.
        rmdir "$mediaDir" 2>/dev/null || true
    else
        if [[ "$target" == "pdf" ]]; then
            filter="$(pdf_filter)"
        else
            filter="$(lo_filter "$target")"
        fi

        # soffice writes <stem>.<ext> into --outdir and exits 0 even when the
        # filter bailed, so the file's existence is the only real success test.
        rm -f "$out"
        loLog="$profileDir/soffice.log"
        timeout "$timeoutSecs" soffice --headless --norestore --nolockcheck \
            -env:UserInstallation="file://$profileDir/lo" \
            --convert-to "$filter" \
            --outdir "$destAbs" "$srcDir/$base" >"$loLog" 2>&1
        rc=$?

        if [[ $rc -eq 124 ]]; then
            warn "$base: timed out after ${timeoutSecs}s"
            failures=$((failures+1)); continue
        fi
        if [[ ! -s "$out" ]]; then
            warn "$base: conversion produced nothing"
            [[ -s "$loLog" ]] && sed 's/^/    /' "$loLog" >&2
            failures=$((failures+1)); continue
        fi
    fi
done

[[ $failures -eq 0 ]] || die "$failures of ${#files[@]} file(s) failed"
