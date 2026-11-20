"""Model client of ask_code: the Strata call, query translation (question -> names) and the judge (excerpts -> verified picks).
Kept out of ask_code.py so the index, BM25 and vector code (and `lp find --no-model`) never import Strata."""

import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir
from pipeline.util import first_json_obj  # noqa: E402
from search.strata import messages, strata_model, strata_up, wait_strata  # noqa: E402

from localmodel.prompts import JSON_ONLY  # noqa: E402

THINK = set(filter(None, os.environ.get("ASK_CODE_THINK", "").split(",")))  # steps that get the model's reasoning: translate,judge


def set_think(careful):
    """Per call: a long-lived server must not keep an earlier --careful."""
    THINK.clear()
    THINK.update(filter(None, os.environ.get("ASK_CODE_THINK", "").split(",")))
    if careful:
        THINK.add("judge")  # reasoning on the judge: 4-5x slower, fewer false positives


def llm(prompt, max_tokens=700, think=False):
    from search.localq import slot

    wait_strata()
    body = {"model": strata_model(), "max_tokens": max_tokens, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}
    if not think:
        body["thinking"] = {"type": "disabled"}  # reasoning ate the whole token budget and left an empty answer
    with slot("call"):  # shared with every other agent on the local model
        out = messages(body, 600)
    return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")


def jsonobj(text):
    return first_json_obj(text, greedy=True) or {}


def translate(question, vocab=None, seen=None):
    hint = ""
    if vocab:
        hint += f"\nWords this codebase uses in its function names: {', '.join(vocab[0])}.\nIts source files are named after: {', '.join(vocab[1])}.\nPrefer these words and the style of these names over generic guesses; the code may call a concept by a different word than the question does."
    if seen:
        hint += f"\nNames found in the closest files so far: {', '.join(seen)}."
    j = jsonobj(
        llm(
            "A developer asks a question about a codebase. List the identifiers (function/class/variable names, in the style the code probably uses) "
            "and plain keywords most likely to appear where the answer lives. Max 6 identifiers and 8 keywords." + hint + "\n"
            f"Question: {question}\n{JSON_ONLY}"
            '{"symbols":["..."],"keywords":["..."]}',
            3000 if "translate" in THINK else 700,
            think="translate" in THINK,
        )
    )
    return [str(x) for x in j.get("symbols", [])][:6], [str(x) for x in j.get("keywords", [])][:8]


def snippet(root, c, n=None):
    n = n or min(max(c["end"] - c["start"] + 1, 12), 40)  # the chunk, padded to 12 lines so a one-liner still shows its surroundings
    try:
        with open(os.path.join(root, c["path"]), errors="replace") as f:
            return "\n".join(f.read().split("\n")[c["start"] - 1 : c["start"] - 1 + n])
    except OSError:
        return ""


def judge(root, question, cands):
    """Model picks excerpts and must quote the line that answers; code drops any pick whose quote is not really in the excerpt."""
    blocks = "\n\n".join(f"[{i}] {c['path']}:{c['start']}\n{snippet(root, c)}" for i, c in enumerate(cands))
    j = jsonobj(
        llm(
            "Question about a codebase: "
            + question
            + "\n\nCandidate excerpts:\n\n"
            + blocks
            + "\n\nWhich excerpts contain the code that directly implements or defines what the question asks? Be strict: code that only mentions a related word, "
            "docs about other things, or a similar-sounding feature does not count. For each pick, copy ONE line from that excerpt, exactly as written, that proves it. "
            "If no excerpt qualifies, return an empty list. " + JSON_ONLY + '{"relevant":[{"n":<excerpt number>,"evidence":"<exact line>"}]}',
            4000 if "judge" in THINK else 600,
            think="judge" in THINK,
        )
    )

    def norm(t):
        return re.sub(r"\s+", " ", t).strip()

    keep = []
    for r in j.get("relevant", []):
        if (
            isinstance(r, dict)
            and isinstance(r.get("n"), int)
            and 0 <= r["n"] < len(cands)
            and norm(str(r.get("evidence", "")))
            and norm(str(r["evidence"])) in norm(snippet(root, cands[r["n"]]))
        ):
            keep.append(r["n"])
    return keep


def use_model(a):
    """False (with a notice) when the model is wanted but unreachable."""
    if "--no-model" in a:
        return False
    if strata_up():
        return True
    expected = os.path.exists("/data/state") or os.environ.get(
        "STRATA_URL"
    )  # a Strata container: the model may still be loading, wait briefly (STRATA_WAIT_TRIES x ~10 s, default 2)
    if expected and wait_strata(int(os.environ.get("STRATA_WAIT_TRIES", "2"))):
        return True
    print("(no local model reachable: keyword and symbol search only, results unverified)", file=sys.stderr)
    return False
