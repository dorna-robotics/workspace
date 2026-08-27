# Replay fusion — the motion book

**Status: APPROVED — implemented on the `fuse` branch (core motion
book + book-gated deposits/merges). In sim verification.**

## Problem (one paragraph)

Today's fuse decides *online*: it holds a verb's exit tail hoping the
next verb can merge. Holding shifts *when* IK solves run, and the IK
candidate filter silently depends on when it runs (it judges against
whatever scene the planner last loaded). Result: poses that were stable
for months started flipping (rail swoops, "off in x"), worst on fresh
caches. Four attempted in-IK fixes each changed station poses and were
rejected: the old poses are not reproducible by any rule — they are the
product of classic stop-and-go solve timing.

## Design

Fusion becomes a **replay optimization** driven by a recorded book,
instead of an online gamble.

- **Run 1 (or any unrecorded seam): pure classic.** Stop-and-go
  execution, exactly pre-fuse motion, identical solve moments,
  identical poses. While running, core records each seam it *could*
  have fused: "after THIS exit group (these points) came THAT motion
  (those points)."
- **Later runs: fuse only what the book proves.** At a stop where the
  book has a record, the tail is held. When the next motion arrives it
  is compared against the recorded one: match → splice into one chain
  (existing merge + certification machinery, traj cache warm);
  mismatch (replan, branch, operator action, changed recipe) → flush
  and run classic, loudly. Unrecorded seams always run classic — and
  get recorded for next time.

The `fuse` knob keeps its exact meaning and scoping (recipe kwarg,
per-call override): it now means "allowed to use the book."

## The book

- File: `core/motion_book.json` — JSONL, same scene-stamp header and
  degrade-to-memory rules as `ik.json` / `traj.json`.
- One record per seam:
  `{owner, tail_pts, next_class, next_pts_prefix, tool_pose}` —
  points stored canonical-j5 with turn re-carry on replay (the same
  treatment `traj.json` already uses), rounded like the other caches.
- Lookup key: `(owner, tail_pts)`. Match test on replay: the arriving
  motion's class and first points equal the record within the cache
  rounding. Anything else is a mismatch → classic.
- Recording happens on every run for seams executed classically, so
  new protocol steps are learned incrementally.

## Invariants

1. **First run after any wipe = pre-fuse motion, bit for bit.** The
   worst case is the guaranteed floor, not a dice roll.
2. **IK never solves in a fusion-shifted context.** Solves are minted
   during classic execution only; fused replays hit the ik cache.
   The IK code is NOT touched — filter, scoring, everything stays.
3. **Mismatch degrades to classic, never to a guess.** Branching and
   replans need no special rules — they simply don't match the book.
4. **All non-fuse work is untouched**: trigger_io grammar, winding fix,
   merged Cap/ParkCap, staged immerse, traj cache, exit-IO lane.

## What is removed

The online hold — `touch` depositing its exit tail *unconditionally*
when `fuse` is on, and planned hops self-deferring — becomes
book-gated. No other fusion machinery changes: deposit, frontier,
merge/splice, barriers, the robot-api gate flush all stay as they are.

## Verified (sim, examples/scale, batch 2, 2026-08-27)

Run 1: zero merges (pure classic), 11 seams armed, 8 recorded — the
3 unrecorded were seams whose next motion wasn't a clean fold, which
is exactly the filter working. Run 2: holds + merges at every
recorded seam, zero errors; the Scale exit — a genuinely branching
seam (its next target differs per tube) — mismatched, flushed
classic, and dropped its record. UPGRADED same day: MULTI-PARTNER
records — one file row per proven partner, accumulated into a list
at load, match-ANY at the merge; a held seam that meets an uncovered
future flushes classic AND learns it on the spot (book_learn), so
both futures fuse from the next run (mid-run tool swaps AND the
final Park). Flushes never drop records; the book only grows with
proven pairs.

## Open knobs (decide at review)

- Book invalidation: scene stamp only (like ik.json), or also a recipe
  fingerprint? Proposed: scene stamp only — changed points fail the
  match anyway, which self-heals by re-recording.
- Should Park/Start seams be recorded too? Proposed: yes — they are
  seams like any other.
