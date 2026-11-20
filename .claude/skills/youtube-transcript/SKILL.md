---
name: youtube-transcript
description: Download the transcript/subtitles/captions of a YouTube video as plain text, for fact-checking, summarizing, quoting, or analyzing what a video actually says. Use whenever a YouTube URL or video needs its spoken content read — "what does this video claim", "get the transcript", "download subtitles", "evaluate this video", "summarize this YouTube link". Encodes which extraction routes still work and which are dead.
---

# YouTube transcript → text

## The one path that works

`yt-dlp` is the only reliably working extractor. Everything else is dead (see
Dead ends). Install the **standalone binary** — it bundles its own Python, so it
works on hosts with no `python3`:

```bash
cd "$SCRATCHPAD"   # or any writable dir
curl -sL -o yt-dlp https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux
chmod +x yt-dlp
```

macOS: `yt-dlp_macos` instead. If `yt-dlp` is already on PATH, just use it.

Then fetch subtitles only — never download the video:

```bash
./yt-dlp --write-auto-subs --write-subs --sub-lang en --skip-download \
         -o "vid.%(ext)s" "https://www.youtube.com/watch?v=VIDEO_ID"
```

`--write-subs` grabs human-authored captions if they exist, `--write-auto-subs`
falls back to machine (ASR) ones. You get `vid.en.vtt`.

Convert to prose:

```bash
node scripts/vtt-to-text.js vid.en.vtt > transcript.txt
```

Add `--timestamps` for `[mm:ss] text` lines instead — use that when you need to
cite or re-check a specific moment.

## Gotchas that will bite you

**Do not pass `--convert-subs srt`.** It shells out to ffmpeg, and if ffmpeg is
missing the whole command exits non-zero *after* the `.vtt` has already
downloaded successfully. The error looks fatal but isn't — check for the `.vtt`
before retrying anything. Skipping the conversion entirely and post-processing
the VTT avoids the dependency.

**Auto-captions triplicate every line.** The caption box scrolls, so each line
is re-emitted as it moves up. Raw stripping yields ~3x duplicated text. The
bundled script dedupes against a sliding window (not a global set, which would
wrongly drop genuinely repeated phrases later in the video).

**"No supported JavaScript runtime could be found"** is a warning, not an error.
It affects video format selection, not subtitles. Ignore it.

**Check whether captions are ASR before trusting proper nouns.** ASR mangles
names badly — real examples from one run: "François Guillet" for *Gouyette*,
"Le Grand-Bornand in the Gaou" for *La Grand-Combe, in the Gard*, "Retail" for
*Retailleau*. If a name looks off, it is probably the transcription, not the
video. Verify names independently before attributing an error to the creator.
`yt-dlp --list-subs URL` shows which tracks are `auto-generated` vs authored.

## Dead ends — don't retry these

Both fail silently or cryptically; they cost turns.

- **Scraping `captionTracks[].baseUrl` from the watch page.** The `baseUrl`
  parses out fine and looks valid, but fetching it returns **HTTP 200 with zero
  bytes** from datacenter/sandbox IPs. There is no error message. Missing PO
  token / bot gating.
- **InnerTube `youtubei/v1/player` with the old public `AIzaSy...` key.**
  Returns `400 FAILED_PRECONDITION`. The key is retired.
- **`WebFetch` on a YouTube watch URL.** Returns only footer navigation — no
  title, description, or transcript.

## Notes

- Non-English: `--sub-lang fr` (or `--sub-lang "en,fr"`). `--list-subs` shows
  what exists.
- Playlists/channels work too, but add `--playlist-end N` to bound the run.
- Age-restricted or private videos need cookies (`--cookies-from-browser`) and
  generally won't work from a headless sandbox.
- A transcript is the creator's content. Quote and analyze it as needed, but
  don't republish it wholesale as a standalone artifact.
