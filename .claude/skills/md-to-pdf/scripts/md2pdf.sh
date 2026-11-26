#!/usr/bin/env bash
# Render Markdown to PDF via pandoc + typst.
#
#   md2pdf.sh [options] FILE.md [-o OUT.pdf]
#
#   --title T          document title (overrides YAML front matter)
#   --author A         author line (repeatable)
#   --date D           date line; "today" expands to the current date
#   --toc              table of contents
#   --number-sections  numbered headings (1, 1.1, 1.1.1)
#   --no-page-numbers  drop page numbers (they are on by default)
#   --papersize P      a4 (default) | us-letter | a5 ...
#   --margin M         page margin, typst units, default 2.5cm
#   --fontsize N       body size, default 11pt
#   --font NAME        body font; must be installed (see `typst fonts`)
#   --landscape        rotate the page
#   --columns N        multi-column body
#   --theme NAME       syntax highlighting theme, default pygments
#   --keep-typst       also write the intermediate .typ next to the PDF
#   -- ...             everything after -- is passed straight to pandoc
#
# Output defaults to FILE.pdf beside the input.
set -euo pipefail

title=""; date=""; toc=false; numbered=false; pagenums=true
papersize="a4"; margin="2.5cm"; fontsize="11pt"; font=""
landscape=false; columns=""; theme="pygments"; keep_typst=false
out=""; authors=(); passthru=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)           title="${2:?}"; shift 2 ;;
        --author)          authors+=("${2:?}"); shift 2 ;;
        --date)            date="${2:?}"; shift 2 ;;
        --toc)             toc=true; shift ;;
        --number-sections) numbered=true; shift ;;
        --no-page-numbers) pagenums=false; shift ;;
        --papersize)       papersize="${2:?}"; shift 2 ;;
        --margin)          margin="${2:?}"; shift 2 ;;
        --fontsize)        fontsize="${2:?}"; shift 2 ;;
        --font)            font="${2:?}"; shift 2 ;;
        --landscape)       landscape=true; shift ;;
        --columns)         columns="${2:?}"; shift 2 ;;
        --theme)           theme="${2:?}"; shift 2 ;;
        --keep-typst)      keep_typst=true; shift ;;
        -o)                out="${2:?}"; shift 2 ;;
        -h|--help)         sed -n '2,22p' "$0" | sed 's/^# \?//'; exit 0 ;;
        --)                shift; passthru=("$@"); break ;;
        -*)                echo "unknown option: $1" >&2; exit 1 ;;
        *)                 src="$1"; shift ;;
    esac
done

[[ -n "${src:-}" ]] || { echo "usage: md2pdf.sh [options] FILE.md [-o OUT.pdf]" >&2; exit 1; }
[[ -r "$src" ]]     || { echo "cannot read: $src" >&2; exit 1; }
[[ -n "$out" ]]     || out="${src%.*}.pdf"

[[ "$date" == "today" ]] && date="$(date +%Y-%m-%d)"

# `margin` is a typst dictionary; -V cannot express one, so it goes through a
# metadata file (pandoc exposes metadata to the template as variables).
meta="$(mktemp --suffix=.yaml)"
trap 'rm -f "$meta"' EXIT
printf 'margin:\n  x: %s\n  y: %s\n' "$margin" "$margin" > "$meta"

args=(
    "$src"
    --from=markdown+pipe_tables+task_lists+footnotes+tex_math_dollars+fenced_divs
    --pdf-engine=typst
    --syntax-highlighting="$theme"
    --metadata-file="$meta"
    # Images and includes resolve relative to the .md, not the shell's cwd.
    --resource-path="$(dirname "$src"):."
    -V papersize="$papersize"
    -V fontsize="$fontsize"
)

$pagenums          && args+=(-V page-numbering="1")
$toc               && args+=(--toc --toc-depth=3)
$numbered          && args+=(-V section-numbering="1.1.1")
[[ -n "$title" ]]  && args+=(-M title="$title")
[[ -n "$date" ]]   && args+=(-M date="$date")
[[ -n "$font" ]]   && args+=(-V mainfont="$font")
[[ -n "$columns" ]] && args+=(-V columns="$columns")
for a in ${authors+"${authors[@]}"}; do args+=(-M author="$a"); done

# typst has no landscape flag; swap the paper for its rotated twin.
if $landscape; then
    args+=(-V header-includes="#set page(flipped: true)")
fi

if $keep_typst; then
    pandoc "${args[@]}" ${passthru+"${passthru[@]}"} -t typst -o "${out%.pdf}.typ"
    echo "wrote ${out%.pdf}.typ" >&2
fi

pandoc "${args[@]}" ${passthru+"${passthru[@]}"} -o "$out"
echo "wrote $out ($(du -h "$out" | cut -f1), $(pdfinfo "$out" 2>/dev/null | awk '/^Pages/{print $2" pages"}'))" >&2
