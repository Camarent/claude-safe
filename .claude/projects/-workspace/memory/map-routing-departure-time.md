---
name: map-routing-departure-time
description: "When checking route times on a map (Google Maps etc.), always set the planned date and clock time of departure, never \"leave now\""
metadata:
  node_type: memory
  type: feedback
  originSessionId: c9a5eea7-c752-48a0-9030-b2b77f6b21a6
  modified: 2026-11-22T11:34:43.909Z
---

When routing legs on a map for a trip plan, set the planned day and hour of departure (depart-at), so traffic matches the real day. Never use the default "leave now".

**Why:** In the China trip plan the first batch of Google Maps routes ran with "leave now" (traffic of the day I checked), and the user asked to always set the day/time and recompute.

**How to apply:** Google Maps URL form that works: `/maps/dir/<A>/<B>/data=!4m6!4m5!2m3!6e0!7e2!8j<unix_ts>!3e0` (depart at epoch; the browser here runs in UTC and Google shows the wall clock from the epoch as UTC, so compute `TZ=UTC date -d "YYYY-MM-DD HH:MM" +%s` for the local clock time wanted; an epoch in the real timezone gave night traffic by mistake; always read back the shown date and "Arriver vers" time to confirm). Or use the "Partir à" menu and date/time fields. State in the answer that times are typical traffic for that weekday and hour, not an exact forecast. See [[communication]] for answer style.
