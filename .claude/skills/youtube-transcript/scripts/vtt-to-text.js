#!/usr/bin/env node
/*
 * Convert a YouTube .vtt subtitle file to readable plain text.
 *
 * Auto-generated (ASR) captions repeat each line several times as the caption
 * box scrolls, so a naive strip produces ~3x duplicated text. This dedupes
 * against a sliding window of recently emitted lines rather than a global set,
 * so a phrase genuinely repeated later in the video survives.
 *
 * Usage:
 *   node vtt-to-text.js input.vtt              > transcript.txt
 *   node vtt-to-text.js input.vtt --timestamps > transcript.txt
 *
 * --timestamps emits one "[mm:ss] text" line per cue instead of flowing prose.
 * Use it when you need to cite or re-check a specific moment in the video.
 */

const fs = require("fs");

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith("--"));
const withTimestamps = args.includes("--timestamps");

if (!file) {
  console.error("usage: node vtt-to-text.js <file.vtt> [--timestamps]");
  process.exit(1);
}

const WINDOW = 6; // how many recent lines to check for duplicates

const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
const out = [];
const recent = [];
let cueStart = null;

const clean = (s) =>
  s
    .replace(/<[^>]*>/g, "") // inline <c> / <00:00:00.000> karaoke tags
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .trim();

const stamp = (t) => {
  const m = t.match(/(\d+):(\d\d):(\d\d)/);
  if (!m) return "00:00";
  const [, h, mm, ss] = m;
  const total = +h * 60 + +mm;
  return `${String(total).padStart(2, "0")}:${ss}`;
};

for (const raw of lines) {
  if (raw.includes("-->")) {
    cueStart = stamp(raw.split("-->")[0].trim());
    continue;
  }
  if (
    !raw.trim() ||
    raw.startsWith("WEBVTT") ||
    /^(Kind|Language|NOTE|STYLE|REGION):/.test(raw) ||
    /^\d+$/.test(raw.trim()) // cue sequence numbers
  ) {
    continue;
  }

  const text = clean(raw);
  if (!text || recent.includes(text)) continue;

  recent.push(text);
  if (recent.length > WINDOW) recent.shift();

  out.push(withTimestamps ? `[${cueStart ?? "00:00"}] ${text}` : text);
}

const result = withTimestamps
  ? out.join("\n")
  : out.join(" ").replace(/\s+/g, " ").trim();

process.stdout.write(result + "\n");

const words = result.split(/\s+/).filter(Boolean).length;
console.error(`[vtt-to-text] ${out.length} lines, ~${words} words`);
