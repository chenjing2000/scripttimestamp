---
name: script-timestamp-aligner
description: Merge a structured original-text JSON with WhisperX word timestamps into Script JSON v1.0; keep original paragraphs, sentences and wording, handle multiword timing units, validate and report mismatches. Use when a user supplies one paragraph/sentence source JSON and one WhisperX word-timestamp JSON and requests a synchronized script or transcript JSON.
---

# Script Timestamp Aligner

## Purpose

Create a standalone, minimal Script JSON v1.0 from two complementary sources:

- **Source JSON** owns paragraph breaks, sentence breaks, casing, punctuation and exact wording.
- **WhisperX JSON** supplies word-level timestamps relative to the **original audio start**.

Never regenerate, paraphrase, correct or silently omit source text; never fabricate timestamps. Do not infer a title from the audio. If the source JSON has no `title`, set `"title": ""`.

## Recognize the inputs

Source JSON typically contains `paragraphs: [{"paragraph": [{"sid": "s001", "text": "..."}, ...]}, ...]` and optionally `filetype`, `next_sid`, `title`. Its `sid` is for input diagnostics only; do **not** output `sid` in Script JSON.

WhisperX JSON contains `word_segments: [{"word": "...", "start": 0.0, "end": 0.0}, ...]` and usually `segments[].words`. Prefer the former; fall back to the latter. If both are present and disagree, log the discrepancy and use `word_segments` only. Do not concatenate both sources and double-count words.

Require one source JSON and one WhisperX JSON. If the files do not fit these structures, explain the missing input rather than guessing or proceeding with the wrong file.

## Output contract

`references/script.schema.json` defines structural and type constraints.

```json
{
  "title": "",
  "paragraphs": [
    [
      {
        "text": "BIOLOGY IS HANDED.",
        "timed_units": [
          ["BIOLOGY", 22.377, 23.078],
          ["IS", 23.338, 23.458],
          ["HANDED", 23.778, 24.259]
        ]
      }
    ]
  ]
}
```

- A paragraph is a **list of sentence objects**, not an object containing `sentences`.
- The only top-level keys are `title` and `paragraphs`; sentence keys are precisely `text`, `timed_units`.
- Every timed unit is an ordered 3-element tuple `[original_display_text, start_seconds, end_seconds]`.
- A timed unit is a word **or** an original-text phrase that shares one reliable time span. It is not necessarily one dictionary word.
- `text` is byte-for-byte the JSON-decoded original sentence string, including original punctuation and spacing.
- Display text in each unit comes from the **source sentence**, not the WhisperX transcript. It must occur as an exact substring and in forward order. Sentence punctuation not part of a word can remain only in `text`.
- Start/end times represent seconds on the original audio timeline, using 3 decimal places where available. **Never subtract the duration of unrepresented audio.**
- If a unit has no defensible time, use `["source text", null, null]`; never create artificial boundaries, zeros or half-null ranges.
- Do not add `sid`, text offsets, match scores, other provenance, or diagnostics to the formal Script JSON.

## Mapping rules

1. **Exact:** one source token → one WhisperX token; copy its time interval.
2. **Orthographic:** `synthesising` vs `synthesizing`, case, straight vs curly apostrophes; retain original source display, transfer time. No review unless the relationship is uncertain.
3. **One source token → multiple audio tokens:** `mirror-symmetrical` vs `mirror symmetrical`. Keep one display unit with a span from the first audio start to last audio end.
4. **Multiple source tokens → one audio token:** `it is` vs `it's`; use `['it is', start_of_its, end_of_its]` as **one** display unit. Only combine continuous tokens in **the same original sentence**. Disambiguate `it's` as `it is` versus `it has` from the actual source text.
5. **Substitutions:** `seven` vs `several`, unusual names: map to the recognized time span only if sufficient surrounding words anchor the location; keep source wording, add a *requires-review* item. Otherwise use null time.
6. **Extra audio:** do not insert extra WhisperX words into the source text. Ignore unmatched preamble/trailer for Script JSON, record its count in the report. Flag unmatched audio within the mapped source as a review item.
7. **Unmatched source word:** include its original display text and `[null, null]`; add a review item.

Use document-wide ordered matching (exact anchors plus local sequence alignment), **not** one-WhisperX-segment-per-sentence. Source text spans, rather than comparing word counts, prove full coverage when multiple words share a timed unit.

## Execution

Use Python 3.9+ and the standard library. Run this script from the skill folder (or use absolute paths):

```powershell
python scripts/align.py `
  --source "C:\path\original.json" `
  --whisperx "C:\path\whisperx.json" `
  --output "C:\path\script.json" `
  --report "C:\path\alignment-report.json"
```

The script exits `0` for `passed`, `2` for `needs_review`, `1` for `failed`. Exit 2 is **not** a software failure: output exists but the report must be reviewed. The script refuses to overwrite either input path and writes output atomically when successful. It does not change or delete the originals.

## Validation and hand-off

Before delivering output, check:

1. Original paragraph and sentence counts match exactly; every sentence `text` matches the source exactly.
2. Every source word is covered **exactly once**, including words merged into a unit; unit texts are forward, non-overlapping source substrings.
3. All known timestamps are finite nonnegative numbers with `start < end`; both missing times are null. Unexpected time-order reversals are logged for review.
4. Structural JSON passes `references/script.schema.json`; additional source-word coverage and relative-time checks are enforced by `scripts/align.py`.
5. Report distinguishes `passed`, `needs_review`, `failed`; it includes counts, unrepresented audio, and review issues with original `sid` when available.
6. If `needs_review`, **do not call it fully verified**: explain remaining source-vs-audio uncertainty and link both `script.json` and `alignment-report.json`.
7. If `failed`, do not hand off an old output file as if it were newly generated.

The aligner works from **the two JSON files only**; it cannot verify what is actually spoken without the original audio. For ambiguous phonetic substitutions, recommend listening to the listed intervals.
