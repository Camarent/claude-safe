#!/usr/bin/env bash
# Extract text from a PDF, falling back to OCR when there is no text layer.
#
#   pdf-extract.sh [options] FILE.pdf
#
#   --layout        keep visual column/table layout (pdftotext -layout)
#   --pages A-B     only this page range (e.g. --pages 3-7)
#   --ocr           force OCR even if a text layer exists
#   --no-ocr        never OCR; fail loudly on an image-only PDF
#   --lang L        tesseract language(s), default eng (e.g. fra, eng+fra)
#   --dpi N         rasterisation DPI for OCR, default 300
#   -o FILE         write to FILE instead of stdout
#
# Exit codes: 0 ok, 1 usage/IO error, 2 no text could be extracted.
set -uo pipefail

layout=""; first=""; last=""; force_ocr=false; allow_ocr=true
lang="eng"; dpi=300; out=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --layout)  layout="-layout"; shift ;;
        --pages)   range="${2:?--pages needs a value}"
                   first="${range%%-*}"; last="${range##*-}"
                   [[ "$range" != *-* ]] && last="$first"
                   shift 2 ;;
        --ocr)     force_ocr=true; shift ;;
        --no-ocr)  allow_ocr=false; shift ;;
        --lang)    lang="${2:?--lang needs a value}"; shift 2 ;;
        --dpi)     dpi="${2:?--dpi needs a value}"; shift 2 ;;
        -o)        out="${2:?-o needs a value}"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \?//'; exit 0 ;;
        -*)        echo "unknown option: $1" >&2; exit 1 ;;
        *)         pdf="$1"; shift ;;
    esac
done

[[ -n "${pdf:-}" ]] || { echo "usage: pdf-extract.sh [options] FILE.pdf" >&2; exit 1; }
[[ -r "$pdf" ]]     || { echo "cannot read: $pdf" >&2; exit 1; }

pagelimit=()
[[ -n "$first" ]] && pagelimit=(-f "$first" -l "$last")

emit() { if [[ -n "$out" ]]; then cat > "$out"; else cat; fi; }

# How many pages are we actually reading? Used to scale the "is there a text
# layer" test — a 1-page memo legitimately holds few characters, a 40-page scan
# holds none.
pagecount="$(pdfinfo "$pdf" 2>/dev/null | awk '/^Pages:/{print $2}')"
[[ "$pagecount" =~ ^[0-9]+$ ]] || pagecount=1
if [[ -n "$first" ]]; then
    span=$(( last - first + 1 ))
    (( span > 0 )) && pagecount=$span
fi

# ---- 1. text layer ---------------------------------------------------------
if [[ "$force_ocr" == false ]]; then
    text="$(pdftotext $layout "${pagelimit[@]}" "$pdf" - 2>/dev/null)"
    # A scanned page yields only form feeds and the odd speck of noise, while a
    # genuine text layer yields dozens of characters per page at minimum. Judge
    # per page, not in absolute terms, or short documents get needlessly OCR'd.
    chars="$(printf '%s' "$text" | tr -cd '[:alnum:]' | wc -c)"
    if (( chars > 0 && chars >= pagecount * 8 )); then
        printf '%s\n' "$text" | emit
        exit 0
    fi
    if [[ "$allow_ocr" == true ]]; then
        echo "pdf-extract: no usable text layer — falling back to OCR" >&2
    fi
fi

# ---- 2. OCR ----------------------------------------------------------------
if [[ "$allow_ocr" == false ]]; then
    echo "pdf-extract: image-only PDF and --no-ocr was given" >&2
    exit 2
fi
command -v tesseract >/dev/null || { echo "pdf-extract: tesseract not installed" >&2; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pdftoppm -r "$dpi" -gray -png "${pagelimit[@]}" "$pdf" "$tmp/page" || {
    echo "pdf-extract: could not rasterise $pdf" >&2; exit 1; }

shopt -s nullglob
pages=("$tmp"/page-*.png)
[[ ${#pages[@]} -gt 0 ]] || { echo "pdf-extract: no pages rendered" >&2; exit 2; }

{
    for page in "${pages[@]}"; do
        n="${page##*page-}"; n="${n%.png}"
        echo "--- page ${n#0} ---"
        tesseract "$page" - -l "$lang" 2>/dev/null
        echo
    done
} | emit

exit 0
