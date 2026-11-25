"""Names of the public-words gate: the pipeline.json setting that lists words the public docs must not contain, and the environment variable that overrides it."""

PUBLIC_FORBIDDEN_WORDS_KEY = "public_forbidden_words"  # pipeline.json key: list of words no tracked text file may contain (case-insensitive)
ENV_PUBLIC_FORBIDDEN_WORDS = "PIPELINE_PUBLIC_FORBIDDEN_WORDS"  # comma-separated words; set: overrides the pipeline.json key
PUBLIC_FORBIDDEN_WORDS_DEFAULT: list[str] = []  # the key when pipeline.json does not set it: no words, so the gate finds nothing
PUBLIC_BINARY_SNIFF_BYTES = 8192  # the scan skips a tracked file whose first this-many bytes hold a NUL byte (binary)
PUBLIC_WORDS_BASELINE_KEY = "public_words_baseline"  # pipeline.json key: path (repo-relative) of the file listing accepted hits, one hit key per line
PUBLIC_WORDS_BASELINE_DEFAULT = "docs/public-words-baseline.txt"  # the baseline when pipeline.json does not set the key; missing file = empty baseline
