"""Names of the public-words gate: the pipeline.json setting that lists words the public docs must not contain, and the environment variable that overrides it."""

PUBLIC_FORBIDDEN_WORDS_KEY = "public_forbidden_words"  # pipeline.json key: list of words no tracked *.md file may contain (case-insensitive)
ENV_PUBLIC_FORBIDDEN_WORDS = "PIPELINE_PUBLIC_FORBIDDEN_WORDS"  # comma-separated words; set: overrides the pipeline.json key
PUBLIC_FORBIDDEN_WORDS_DEFAULT: list[str] = []  # the key when pipeline.json does not set it: no words, so the gate finds nothing
PUBLIC_DOC_GLOB = "*.md"  # the tracked files the scan reads (pathspec of git ls-files)
