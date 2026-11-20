#!/usr/bin/env python3
"""prereview: local-model gate before the paid reviewer. Best effort, fails open.

    python3 -I /root/.claude/tools/localmodel/prereview.py --stage S --task task-N [--dir D]

File mode (`Type: report` task, or every Predicted path outside the checkout): all deliverable text files go in ONE `lp judgefiles` call against the Done criteria.
Both modes send the whole input in ONE call; over the token budget (PREREVIEW_INPUT_TOKENS, `est_tokens`) = skipped, never chunked or cut. The model lists every violation; each finding is verified against the input.
Brief = .work/S/<task>.md, diff = the committed job-worktree diff (`wt.diff`), else `git diff HEAD` limited to the task's Predicted paths. `lp review` FAIL -> `VERDICT: FAIL` + reason (the dev reworks
it, the paid reviewer is not called). PASS, empty diff, server problem (rc 3) or any error -> `VERDICT: DONE` (the normal reviewer runs)."""

import argparse
import hashlib
import os
import re
import sys
import tempfile

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
from pipeline.configuration import (  # noqa: E402
    DIFF_SHA_LEN,
    DONE,
    FAIL,
    PREREVIEW_DIFF_CHARS,
    PREREVIEW_INPUT_TOKENS,
    PREREVIEW_LP_TIMEOUT_S,
    REASON_CUT,
    REVIEW_SKIP_RC,
    SKIPPED,
    WORK_DIR,
)
from pipeline.plan_files import paths_in, task_type  # noqa: E402
from pipeline.util import est_tokens  # noqa: E402
from pipeline.worktrees import deliverable_texts, files_sha, uncommitted_diff  # noqa: E402
from search.leadparts import definitions, uses_of  # noqa: E402
from search.lpcall import added_ranges, git_text, lp, lp_rc  # noqa: E402

QUOTE_RE = re.compile(r"([\w./-]+\.\w+):\d+")  # a cited `file:line`
CUT_MARK = "[... cut at"  # worktree.diff marks a diff it had to cut


def quote_in_diff(out, diff):
    """True when the evidence of a FAIL (the part after `|`) cites a file named in the diff headers and quotes text that stands on a +/- line of the diff."""
    ev = out.split("|", 1)[1] if "|" in out else out
    m = QUOTE_RE.search(ev)
    if not m:
        return False
    path, text = m.group(1), ev[m.end() :].strip().strip("`'\"")
    if path not in {x.group(1) for x in re.finditer(r"(?m)^\+\+\+ b/(.+?)\s*$", diff)}:
        return False
    return bool(text) and any(text in ln[1:] for ln in diff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))


def cited_lines(out):
    """-> the finding lines of an `lp` FAIL output that cite a file:line (the whole text when it has no `|` at all: the old form)."""
    cited = [ln for ln in out.strip().splitlines() if "|" in ln and QUOTE_RE.search(ln.split("|", 1)[1])]
    return cited or ([out.strip()] if "|" not in out and QUOTE_RE.search(out) else [])


def verified_fail(out, diff=None, text=None):
    """-> (verdict word, note) of a `FAIL` output: findings whose quote is not in the `diff` (or file `text`) are dropped; FAIL only when one is left, listing all of them."""
    cited = cited_lines(out)
    if not cited:  # a FAIL without a cited file:line is a guess: the paid reviewer decides
        return DONE, "local review FAIL without a file:line quote (unclear, passed on): " + out.strip()[:REASON_CUT]
    check = (lambda ln: quote_in_diff(ln, diff)) if diff is not None else (lambda ln: text is None or quote_in_text(ln, text))
    kept = [ln for ln in cited if check(ln)]
    if not kept:
        what = "the diff" if diff is not None else "the shown lines"
        return DONE, f"local review FAIL whose quote is not in {what} (unclear, passed on): " + out.strip()[:REASON_CUT]
    first = out.strip().splitlines()[0]
    return FAIL, "\n".join(([first] if "|" not in first and first not in kept else []) + kept)


def decide(rc, out, diff=None, text=None):
    """-> (verdict word, note) from `lp review` exit code and output (`FAIL ...` then one `why | file:line quote` line per finding; the old single-line form works too)."""
    if rc == 1 and out.lstrip().startswith(FAIL):
        return verified_fail(out, diff, text)
    if rc in (0, REVIEW_SKIP_RC) and out.lstrip().startswith("skipped"):
        return SKIPPED, "local review " + out.strip()
    if rc == 0:
        return DONE, "local review PASS: " + out.strip()
    return SKIPPED, f"local review skipped (rc {rc}): {out.strip()[:REASON_CUT]}"


def usage_context(d, paths, max_syms=8):
    """What else uses the definitions this diff adds or changes (`lp nav refs`), packed, so the reviewer can see a broken caller. "" when lp knows nothing."""
    out, n = [], 0
    for f, ranges in added_ranges(d, "HEAD").items():
        if (paths and not any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/") for p in paths)) or not ranges:
            continue
        for ln, sym in definitions(lp(d, "nav", "outline", f).splitlines(), lambda x, ranges=ranges: any(a <= x <= b for a, b in ranges)):
            if n >= max_syms:
                break
            n += 1
            used, text = uses_of(d, f, sym)
            out.append(
                f"- {'~' if text else ''}{sym} ({f}:{ln}): "
                + (", ".join(used[:5]) + (f" +{len(used) - 5}" if len(used) > 5 else "") if used else "no other use")
            )
    return ("\n\n## Other files that use the changed definitions (lp nav refs; ~ = word search only)\n" + "\n".join(out) + "\n") if out else ""


NO_COMMITS = "(no committed changes on the job branch)"


def job_diff(stage, task, d, limit=PREREVIEW_DIFF_CHARS):
    """-> the committed diff of the task's job worktree (`wt.diff`), "" when it has none, or None when the task has no job worktree."""
    try:
        import wt

        if task not in wt.read_manifest(d, stage):
            return None
        out = wt.diff(d, stage, task, limit)
    except Exception:  # best effort: fall back to the plain diff
        return None
    return "" if out.startswith(NO_COMMITS) else out


def _all_outside_repo(paths, d):
    """True when the task has Predicted paths and each is absolute and outside the checkout `d` or under its `.work/` (a report file: no repo diff)."""
    root = os.path.realpath(d)
    work = os.path.join(root, WORK_DIR)

    def outside(p):
        full = os.path.realpath(p)
        return os.path.isabs(p) and (not full.startswith(root + os.sep) or full == work or full.startswith(work + os.sep))

    return bool(paths) and all(outside(p) for p in paths)


def quote_in_text(out, text):
    """True when a FAIL's evidence (after `|`) quotes text that stands on a line of `text` (the files' lines, without our `N: ` prefix)."""
    ev = out.split("|", 1)[1] if "|" in out else out
    m = QUOTE_RE.search(ev)
    quote = re.sub(r"^\d+[:|]\s*", "", ev[m.end() :].strip().strip("`'\"")) if m else ""
    return bool(quote) and any(quote in ln for ln in text.splitlines())


def over_budget(*texts):
    """-> the SKIPPED note when the texts together are over the one-call token budget, else None."""
    n = sum(est_tokens(t) for t in texts)
    return f"local review skipped: input ~{n} est. tokens > budget {PREREVIEW_INPUT_TOKENS}" if n > PREREVIEW_INPUT_TOKENS else None


def numbered(path, text):
    """-> one file as `=== path ===` and its lines with `N: ` numbers."""
    return f"=== {path} ===\n" + "".join(f"{n}: {line}\n" for n, line in enumerate(text.splitlines(), 1))


def file_mode(brief_text, paths, d, review):
    """-> (verdict word, note): judge the task's deliverable files, all in ONE call, against its Done criteria."""
    texts = deliverable_texts(paths, d)
    if not texts:
        return SKIPPED, "local review skipped: no deliverable file found (looked at: " + ", ".join(paths) + ")"
    m = re.search(r"(?ims)^Done:.*", brief_text)
    crit = m[0] if m else brief_text
    body = "\n".join(numbered(p, t) for p, t in texts)
    if note := over_budget(crit, body):
        return SKIPPED, note
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as cf, tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as ff:
        cf.write(crit)
        ff.write(body)
    try:
        rc, out = review(d, "judgefiles", "--criteria", cf.name, "--files", ff.name, t=PREREVIEW_LP_TIMEOUT_S)
    finally:
        os.unlink(cf.name)
        os.unlink(ff.name)
    v, note = decide(rc, out, text="\n".join(t for _, t in texts))
    return v, note + ("\ndiff-sha: " + files_sha(texts) if v == FAIL else "")


def run(stage, task, d, git=git_text, review=lp_rc, context=usage_context):
    """-> (verdict word, note). `git(args, cwd)` -> stdout, `review(repo, *lp_args)` -> (rc, out) and `context(d, paths)` -> str are injectable for tests."""
    brief = os.path.join(d, WORK_DIR, stage, f"{task}.md")
    try:
        with open(brief, encoding="utf-8", errors="replace") as fh:
            brief_text = fh.read()
        paths = paths_in(brief_text)
    except OSError as e:
        return SKIPPED, f"local review skipped: {e}"
    if task_type(brief_text) == "report" or _all_outside_repo(paths, d):
        return file_mode(brief_text, paths, d, review)
    diff = job_diff(stage, task, d)
    if diff is None:  # no job worktree: the work is uncommitted in `d`
        diff = uncommitted_diff(d, paths, lambda repo, *args, timeout=None: git(list(args), repo, timeout))
    if CUT_MARK in diff:  # never review half a diff
        return SKIPPED, f"local review skipped: diff too big (cut at {PREREVIEW_DIFF_CHARS} chars), not counted as FAIL"
    if not diff.strip():
        return SKIPPED, "local review skipped: no diff (WARNING: check the review input)"
    if note := over_budget(diff, brief_text):
        return SKIPPED, note + ", not counted as FAIL"
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as f, tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as b:
        f.write(diff)
        try:
            b.write(brief_text + context(d, paths))
        except Exception:  # context is a bonus: the review still runs on the plain brief
            b.write(brief_text)
    try:
        rc, out = review(d, "review", "--brief", b.name, "--diff", f.name, t=PREREVIEW_LP_TIMEOUT_S)
        v, note = decide(rc, out, diff=diff)
        if v == FAIL:  # the driver rebuilds the same diff (job or uncommitted) and compares: an unchanged one skips the next pre-review
            note += "\ndiff-sha: " + hashlib.sha256(diff.encode("utf-8")).hexdigest()[:DIFF_SHA_LEN]
        return v, note
    finally:
        os.unlink(f.name)
        os.unlink(b.name)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    a = ap.parse_args(argv)
    v, note = run(a.stage, a.task, a.dir)
    print(f"VERDICT: {v}\n{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
