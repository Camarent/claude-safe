ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: LibreOffice, pandoc, typst, tesseract (eng+fra), poppler

# PDF toolchain:
#   poppler-utils  -> pdftotext / pdfinfo / pdfimages / pdftoppm (text-layer extraction)
#   tesseract-ocr  -> OCR fallback for scanned, image-only PDFs (eng + fra)
#   fonts-*        -> typst/pandoc need real fonts on disk for PDF output
#   xz-utils       -> unpack the typst release tarball (absent from node:*-slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    xz-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    fonts-dejavu-core \
    fonts-liberation2 \
    fonts-noto-core \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# pandoc (markdown -> PDF driver). Debian ships an older pandoc than the
# --pdf-engine=typst support needs, so install the upstream .deb (pinned
# 2026-11-05; empty PANDOC_VERSION = latest GitHub release).
ARG PANDOC_VERSION=3.14
RUN arch="$(dpkg --print-architecture)" \
    && PANDOC_VERSION="${PANDOC_VERSION:-$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/jgm/pandoc/releases/latest | sed 's|.*/tag/||')}" \
    && curl -fsSL -o /tmp/pandoc.deb \
       "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${arch}.deb" \
    && dpkg -i /tmp/pandoc.deb \
    && rm -f /tmp/pandoc.deb

# typst — the actual PDF renderer behind pandoc. Single static binary, no LaTeX.
# Pinned 2026-11-05; empty TYPST_VERSION = latest GitHub release.
ARG TYPST_VERSION=v0.15.1
RUN TYPST_VERSION="${TYPST_VERSION:-$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/typst/typst/releases/latest | sed 's|.*/tag/||')}" \
    && case "$(dpkg --print-architecture)" in \
        amd64) typstArch=x86_64 ;; \
        arm64) typstArch=aarch64 ;; \
        *) echo "unsupported arch for typst: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/typst.tar.xz \
       "https://github.com/typst/typst/releases/download/${TYPST_VERSION}/typst-${typstArch}-unknown-linux-musl.tar.xz" \
    && tar -xJf /tmp/typst.tar.xz -C /tmp \
    && install -m 755 "/tmp/typst-${typstArch}-unknown-linux-musl/typst" /usr/local/bin/typst \
    && rm -rf /tmp/typst.tar.xz "/tmp/typst-${typstArch}-unknown-linux-musl"

# Office toolchain (docx/doc/odt/xlsx/pptx -> PDF, and back out to editable
# formats). LibreOffice headless is the only offline route that reproduces the
# real Word layout: page breaks, headers/footers, table widths, tracked changes.
#   --no-install-recommends here matters: it keeps out the whole X/GNOME stack.
#   carlito/caladea are metric-compatible clones of Calibri/Cambria — without
#   them every Office default-font document silently re-paginates.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    libreoffice-core \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

# Warm the LibreOffice user profile at build time so the first real conversion
# isn't paying ~10s of one-off setup.
RUN soffice --headless --terminate_after_init >/dev/null 2>&1 || true
