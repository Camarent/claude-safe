"""Static pin checks for the image Dockerfiles: digests, versions, checksums, no latest fallback, no pipe to a shell."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(TOOLS))
DOCKERFILES = ["Dockerfile", "profiles/rust.Dockerfile"]
PROFILE = "profiles/rust.Dockerfile"
LOCAL_BASE_REF = "${BASE}"
LOCAL_BASE_DEFAULT = "ARG BASE=claude-safe:latest"
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
FROM_RE = re.compile(r"^\s*FROM\s+(\S+)")
SHA_CHECK = "sha256sum -c"
# Number of checksum verifications per file: one per downloaded tarball or installer.
EXPECTED_CHECKS = {"Dockerfile": 3, PROFILE: 2}
# Values from pins.md; each must appear in the file that uses it.
PINS = {
    "Dockerfile": [
        "abe7e5a82b61a8ef67b315b85497c9e65173387ccf2f4a029248184b8e020be5",  # node base digest
        "51781aafe6950cc659f79873e1bdca55fbb9da6e0ef650bf98093a0f0aaaf453",  # uv installer
        "59659bea445f95be73b3084c249653c973148bac30674861aa35d8446bf92bf5",  # snip amd64
        "255eb1a9cdf764f211a58a653e93d9ce5744a01de293ca9ba25b5255ac1498f9",  # snip arm64
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",  # gitleaks x64
        "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",  # gitleaks arm64
    ],
    PROFILE: [
        "b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55",  # rustup-init x86_64
        "9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282",  # rustup-init aarch64
        "410101009739b9d01c7d5affaaad6e9c9a24b4cba6791a4805db8e89b9bf7af2",  # wasm-pack x86_64
    ],
}
# Lines that fetch a moving target or run remote code without a checksum.
VIOLATION_RES = [
    re.compile(r"@latest\b"),
    re.compile(r":-\$\(curl"),
    re.compile(r"\|\s*(sh|bash)\b"),
]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def froms(text):
    return [m.group(1) for m in (FROM_RE.match(ln) for ln in text.splitlines()) if m]


def violations(text):
    return [ln for ln in text.splitlines() if any(rx.search(ln) for rx in VIOLATION_RES)]


class ImagePins(unittest.TestCase):
    def test_dockerfile_base_pinned_by_digest(self):
        refs = froms(read("Dockerfile"))
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, DIGEST_RE)

    def test_profile_from_is_only_local_base(self):
        text = read(PROFILE)
        self.assertIn(LOCAL_BASE_DEFAULT, text.splitlines())
        refs = froms(text)
        self.assertEqual([r for r in refs if not DIGEST_RE.search(r)], [LOCAL_BASE_REF])

    def test_no_latest_or_latest_fallback_or_pipe_to_shell(self):
        for rel in DOCKERFILES:
            self.assertEqual(violations(read(rel)), [], rel)

    def test_claude_code_pinned_by_arg(self):
        text = read("Dockerfile")
        self.assertIn("ARG CLAUDE_CODE_VERSION=2.1.332", text.splitlines())
        self.assertIn('npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"', text)

    def test_pinned_checksums_present(self):
        for rel, shas in PINS.items():
            text = read(rel)
            for sha in shas:
                self.assertIn(sha, text, rel)

    def test_every_download_is_checksum_verified(self):
        for rel, count in EXPECTED_CHECKS.items():
            self.assertEqual(read(rel).count(SHA_CHECK), count, rel)

    def test_detector_flags_mutations(self):
        # C4 mutation: each unsafe form must be caught by the same detector the file test uses.
        for bad in [
            'RUN npm install -g "@anthropic-ai/claude-code@latest"',
            'RUN v="${SNIP_VERSION:-$(curl -fsSLI https://github.com/x/releases/latest)}"',
            "RUN curl -LsSf https://astral.sh/uv/install.sh | sh",
        ]:
            self.assertEqual(violations(bad), [bad])


if __name__ == "__main__":
    unittest.main()
