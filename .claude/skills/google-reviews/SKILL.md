---
name: google-reviews
description: Read Google Maps reviews of a place (restaurant, shop, museum, business) through the chrome-devtools MCP browser — rating, star histogram, review-topic counts and review texts. Use whenever someone wants to know what people say about a place on Google: "check the Google reviews for X", "is this restaurant any good", "what are the complaints about X", "compare ratings of A and B".
---

# Google reviews via chrome-devtools MCP

Tools are `mcp__chrome-devtools__*` (deferred — load via ToolSearch:
`select:mcp__chrome-devtools__new_page,mcp__chrome-devtools__navigate_page,mcp__chrome-devtools__evaluate_script`).
This is **not** the `claude-in-chrome` extension; that skill doesn't apply.

## Don't open the browser for a bare rating

A rating alone ("what's X rated?", "how many stars does X have?") is a stated
fact — use `WebSearch` and stop. Search results carry it. The `/sorry/` CAPTCHA
below is about *browsing* google.com from this IP; it does not affect the
WebSearch tool.

Open the browser when the question needs the **histogram, the topic counts, or
the review texts** — "is it any good", "what do people complain about",
"compare A and B".

## The path that works

1. Open a Maps search URL — it auto-redirects to the place page on a unique match:

   ```
   https://www.google.com/maps/search/?api=1&query=<Name+City>&hl=en
   ```

   Always add a city/street to the query. Always pass `hl=en` — the selectors
   and aria-label regexes in the scripts are English.

2. Pick the smaller script. Paste the file's contents verbatim as the
   `function` argument of `evaluate_script`.

   | Question | Script | Returns |
   |---|---|---|
   | "is it good", "what are the complaints", comparisons | `scripts/stats.js` | `place, url, rating, total_reviews, histogram, topics` |
   | you actually need quotes from reviewers | `scripts/extract-reviews.js` | the above + `limited_view`, `reviews[]` |

   **Default to `stats.js`.** It is a third the size, skips the "See more"
   expansion, and returns the only data that describes *all* reviews (see
   below). Reach for the full extractor when the user wants to read what
   people said, not just how many said it.

   `reviews[]` entries are `{author, stars, date, text, likes, owner_response}`.

   If either returns `error: "not_a_place_page"`, the search had several hits:
   pick from `candidates[].url`, `navigate_page` there, run it again.
   `error: "captcha"` → see Dead ends.

3. Several places: reuse one tab and `navigate_page` between them. No need to
   close/reopen. Each place costs one fresh paste of the script — another
   reason to send the small one.

## What you actually get (logged out) — read this before promising anything

The browser profile is **not signed in**, and Google Maps then serves a
"limited view": **exactly 5 reviews per place, full stop.**

Verified dead ends inside the limited view (don't retry them):
- Scrolling the review panel → stays at 5.
- "See more reviews (N)" button → click does nothing.
- "Sort reviews" → no sort options appear; can't pick Newest/Lowest.
- Topic chips ("crowded", "queue"…) → same 5 reviews for every chip.

What **is** reliable and complete: the **rating, the full 1–5★ histogram
(exact counts), and the topic chips with mention counts**. The histogram and
topics describe *all* reviews. The 5 texts are Google's "most relevant" picks
and don't reflect the histogram (Louvre: four 5★ + one 3★ shown, while 1★
alone is 5,527 reviews). So:

- Base sentiment/complaint-share claims on the **histogram and topics**, not the 5 texts.
- Use the texts as illustrative quotes and say they're a 5-review sample.
- Topic chips like "queue", "crowded", "no photos allowed" plus their counts are
  the best logged-out signal for recurring complaints.

To get more than 5 reviews, the profile at
`/root/.cache/chrome-devtools-mcp/chrome-profile` would need a Google sign-in
(it persists between sessions). Not done and untested; ask the user first and
never enter credentials yourself.

## Gotchas

- **Don't use `take_snapshot` to read reviews.** The accessibility tree of a
  Maps place page is several thousand tokens of buttons (photo thumbnails, share,
  likes). Use `evaluate_script`; snapshot only when you need a uid to click.
- `owner_response` parsing is **unverified** (no tested place had one). If an
  owner reply is visible in a screenshot but returns `null`, fix the selector
  in the script.
- Dates are relative ("2 months ago") — Google doesn't expose absolute dates in
  the DOM. Convert against today's date if you need to.
- The page `/maps/place/...` URL from the result is a stable link to cite.
- Google's CSS class names (`jftiEf`, `wiI7pd`, `d4r55`, `kvMYJc`, `rsqaWe`,
  `w8nwRe`, `RfnDt`) are obfuscated and can change. If `reviews` comes back
  empty but the page visibly shows reviews, re-derive them from one card's
  `outerHTML` (cards keep a `data-review-id` attribute) and update the script.
  The histogram and topics use aria-labels, not classes, so `stats.js` survives
  these rotations.

## Dead ends — don't retry

- **Google Search in the browser** (`google.com/search?q=X+reviews`) → immediate
  redirect to `/sorry/` "unusual traffic" CAPTCHA from this IP. Maps is
  unaffected, and the `WebSearch` tool is unaffected.
- **WebFetch** on a Maps URL → no reviews; the page is JS-rendered.
