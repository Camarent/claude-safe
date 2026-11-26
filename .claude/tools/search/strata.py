#!/usr/bin/env python3
"""Where the local model (Strata) lives and how to reach it. Shared by ask_code.py, local.py, localdev, agent_files and the trial scripts: URL, key, model and the /v1/messages client live only here."""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from pipeline.util import local_log


def strata_url():
    return os.environ.get("STRATA_URL", "http://127.0.0.1:8080")


def strata_model():
    return os.environ.get("STRATA_MODEL", "coder")


def strata_key():
    return (
        os.environ.get("STRATA_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or (Path("/data/state/api-key").read_text(encoding="utf-8").strip() if os.path.exists("/data/state/api-key") else "")
    )


def messages(body, timeout):
    """POST BODY to the server's /v1/messages (Anthropic format) -> the parsed JSON answer. The one client for askmodel, strata_call and agent_files."""
    req = urllib.request.Request(
        strata_url() + "/v1/messages",
        json.dumps(body).encode(),
        {"content-type": "application/json", "anthropic-version": "2023-06-01", "Authorization": "Bearer " + strata_key()},
    )
    local_log(f"strata request -> {req.full_url} (model={body.get('model')}, max_tokens={body.get('max_tokens')}, timeout={timeout}s)")
    start = time.time()
    try:
        out = json.load(urllib.request.urlopen(req, timeout=timeout))  # nosec B310  # http URL set by the operator, not user input
    except Exception as e:  # logged, then re-raised: not a swallow
        local_log(f"strata request failed after {time.time() - start:.1f}s: {type(e).__name__}: {e}")
        raise
    local_log(f"strata answer after {time.time() - start:.1f}s")
    return out


def strata_call(prompt, max_tokens=1500, thinking=True):
    """One user prompt -> the model's text answer, through the shared call queue. thinking=False: no hidden tokens, much faster, for list-picking calls."""
    try:
        from search.localq import slot
    except ImportError:  # tools copied elsewhere without the queue: no limiting
        import contextlib

        def slot(kind="call", timeout=None):
            return contextlib.nullcontext()

    body = {"model": strata_model(), "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if not thinking:
        body["thinking"] = {"type": "disabled"}
    with slot("call"):
        out = messages(body, 600)
    return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")


def wait_strata(tries=120):
    """Block until the local server answers (it may still be loading the model); False after ~10 min (see `tries` and the sleep step)."""
    for i in range(tries):
        try:
            urllib.request.urlopen(urllib.request.Request(strata_url() + "/v1/models", headers={"Authorization": "Bearer " + strata_key()}), timeout=5).read()  # nosec B310  # http URL set by the operator, not user input
            return True
        except urllib.error.HTTPError:
            return True  # it answered (401 without the right key still means the server is up)
        except (OSError, http.client.HTTPException, ValueError):
            if i < tries - 1:
                time.sleep(min(5, 1 + i))  # 1 s, 2 s, ... capped at 5 s: a server that is up in 5 s is not kept waiting 10
    return False


def strata_up():
    """One quick check: does the local server answer at all (any HTTP answer counts)? False when STRATA=0 (set by you or by preflight())."""
    if os.environ.get("STRATA") == "0":
        return False
    try:
        urllib.request.urlopen(strata_url() + "/health", timeout=2)  # nosec B310  # http URL set by the operator, not user input
        return True
    except urllib.error.HTTPError:
        return True  # server answered, just not on /health
    except (OSError, http.client.HTTPException, ValueError):  # no answer is the probe's False result, not an error
        return False


def _loading():
    """True when the model may still be loading: STRATA_URL is set (the start script found the local model server running when this container started, so
    a Strata that does not answer is most likely still loading), or the phase file (STRATA_PHASE_FILE, default /run/strata-phase) says so."""
    if "STRATA_URL" in os.environ:
        return True
    try:
        return "loading" in Path(os.environ.get("STRATA_PHASE_FILE", "/run/strata-phase")).read_text(encoding="utf-8").lower()
    except OSError:  # no phase file: the server is not reported as loading
        return False


def preflight():
    """Run once before planning: if the local model is down, set STRATA=0 (child processes inherit it; STRATA=1 or unset = probe the server) so every local-model step skips. -> True when up."""
    if os.environ.get("STRATA") == "0":
        return False
    if strata_up():
        return True
    if _loading():
        deadline = time.time() + float(os.environ.get("STRATA_LOAD_WAIT", "90"))  # the entrypoint says the model is still loading (about 50 s): wait for it
        while time.time() < deadline and _loading():
            if strata_up():
                return True
            time.sleep(1)
        if strata_up():
            return True
    os.environ["STRATA"] = "0"
    print("preflight: local model (Strata) down: local-model steps are skipped for this run (start it on the host: modelctl use strata)")
    return False
