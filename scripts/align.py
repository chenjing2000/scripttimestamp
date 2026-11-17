#!/usr/bin/env python3
"""Align an original paragraph/sentence JSON with WhisperX word timestamps.

Script JSON v1.0 output uses only title, paragraphs, text, timed_units.
Requires Python 3.9+; the standard library is sufficient.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Ordinary word hyphens stay within a token; em-dashes separate words.
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*(?:[-‐‑][^\W_]+(?:['’][^\W_]+)*)*", re.UNICODE)


@dataclass(frozen=True)
class SourceToken:
    text: str
    paragraph: int
    sentence: int
    sid: Optional[str]
    start_char: int
    end_char: int


@dataclass(frozen=True)
class AudioToken:
    text: str
    start: Optional[float]
    end: Optional[float]


@dataclass(frozen=True)
class Match:
    src_start: int
    src_end: int
    wav_start: int
    wav_end: int
    category: str


def load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8-sig') as f:
        return json.load(f)


def norm(text: str) -> str:
    # Normalisation is for comparison ONLY, not for output.
    t = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    return ''.join(c for c in t if c.isalnum())


def gb_us_variant(a: str, b: str) -> bool:
    if a == b:
        return False
    aliases = {
        'centre': 'center', 'centres': 'centers',
        'metre': 'meter', 'metres': 'meters',
        'litre': 'liter', 'litres': 'liters',
        'theatre': 'theater', 'theatres': 'theaters',
        'grey': 'gray', 'colour': 'color', 'colours': 'colors',
        'favour': 'favor', 'favourite': 'favorite',
        'behaviour': 'behavior', 'neighbour': 'neighbor',
        'neighbours': 'neighbors', 'organise': 'organize',
        'organised': 'organized', 'organisation': 'organization',
        'recognise': 'recognize', 'recognised': 'recognized',
        'metabolise': 'metabolize', 'synthesise': 'synthesize',
        'synthesised': 'synthesized', 'synthesising': 'synthesizing',
        'incentivise': 'incentivize', 'incentivising': 'incentivizing'
    }
    if aliases.get(a) == b or aliases.get(b) == a:
        return True
    for en, us in [('ising', 'izing'), ('ised', 'ized'), ('isation', 'ization'),
                   ('isations', 'izations')]:
        if len(a) >= 6 and a.endswith(en) and a[:-len(en)] + us == b:
            return True
        if len(b) >= 6 and b.endswith(en) and b[:-len(en)] + us == a:
            return True
    return False


def contraction_expansions(word: str) -> set:
    """Return only explicit contraction expansions; no guess from pronunciation."""
    t = unicodedata.normalize('NFKC', word).casefold().replace('’', "'")
    irregular = {
        "can't": {'cannot'}, "won't": {'willnot'},
        "shan't": {'shallnot'}, "ain't": {'amnot', 'isnot', 'arenot'},
        "let's": {'letus'}, "ma'am": {'madam'},
    }
    result = set(irregular.get(t, set()))
    endings = {
        "n't": ['not'], "'re": ['are'], "'ve": ['have'],
        "'ll": ['will'], "'m": ['am'], "'s": ['is', 'has'],
        "'d": ['would', 'had'],
    }
    for suffix, expansions in endings.items():
        if t.endswith(suffix) and len(t) > len(suffix):
            stem = t[:-len(suffix)]
            if suffix == "n't" and stem == 'ca':
                stem = 'can'
            for ending in expansions:
                result.add(norm(stem + ending))
    return result


def parse_source(data: Any) -> Tuple[str, List[List[str]], List[SourceToken]]:
    if not isinstance(data, dict) or not isinstance(data.get('paragraphs'), list) or not data['paragraphs']:
        raise ValueError('Source must contain a nonempty paragraphs array.')
    title = data.get('title', '')
    if not isinstance(title, str):
        raise ValueError('Source title must be a string when present.')
    paragraphs: List[List[str]] = []
    tokens: List[SourceToken] = []
    known_sids = set()
    for p_idx, paragraph in enumerate(data['paragraphs']):
        if not isinstance(paragraph, dict) or not isinstance(paragraph.get('paragraph'), list) or not paragraph['paragraph']:
            raise ValueError(f'Source paragraphs[{p_idx}] must have a nonempty paragraph list.')
        sentences: List[str] = []
        for s_idx, sentence in enumerate(paragraph['paragraph']):
            if not isinstance(sentence, dict) or not isinstance(sentence.get('text'), str) or not sentence['text'].strip():
                raise ValueError(f'Source paragraph {p_idx}, sentence {s_idx} lacks text.')
            sid = sentence.get('sid')
            if sid is not None and (not isinstance(sid, str) or sid in known_sids):
                raise ValueError(f'Source contains a duplicate or invalid sid: {sid!r}.')
            if sid is not None:
                known_sids.add(sid)
            txt = sentence['text']
            words = list(WORD_RE.finditer(txt))
            if not words:
                raise ValueError(f'Source sentence {sid or (p_idx,s_idx)} has no words.')
            sentences.append(txt)
            for m in words:
                tokens.append(SourceToken(m.group(), p_idx, s_idx, sid, m.start(), m.end()))
        paragraphs.append(sentences)
    return title, paragraphs, tokens


def parse_audio(data: Any) -> Tuple[List[AudioToken], List[Dict[str, Any]]]:
    if not isinstance(data, dict):
        raise ValueError('WhisperX input must be a JSON object.')
    notices: List[Dict[str, Any]] = []
    segwords = []
    for seg in data.get('segments', []):
        if isinstance(seg, dict) and isinstance(seg.get('words'), list):
            segwords.extend(seg['words'])
    if isinstance(data.get('word_segments'), list) and data['word_segments']:
        raw = data['word_segments']
        if segwords:
            equivalent = len(raw) == len(segwords) and all(
                isinstance(a, dict) and isinstance(b, dict) and
                (a.get('word'), a.get('start'), a.get('end')) ==
                (b.get('word'), b.get('start'), b.get('end'))
                for a, b in zip(raw, segwords))
            if not equivalent:
                notices.append({'type': 'conflicting_word_sources', 'detail':
                                'word_segments and segments[].words disagree; word_segments selected.',
                                'requires_review': True})
    elif segwords:
        raw = segwords
        notices.append({'type': 'fallback_word_source', 'detail':
                        'No word_segments found; used segments[].words.', 'requires_review': False})
    else:
        raise ValueError('WhisperX contains neither word_segments nor segments[].words.')

    tokens: List[AudioToken] = []
    for idx, w in enumerate(raw):
        if not isinstance(w, dict) or not isinstance(w.get('word'), str):
            raise ValueError(f'Invalid WhisperX word at index {idx}.')
        word = w['word'].strip()
        if not word:
            notices.append({'type': 'empty_whisper_word', 'audio_index': idx,
                            'requires_review': True})
            continue
        start = w.get('start')
        end = w.get('end')
        if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and
                   math.isfinite(v) and v >= 0 for v in [start, end]) or start >= end:
            notices.append({'type': 'invalid_whisper_time', 'audio_index': idx,
                            'word': word, 'requires_review': True})
            start = end = None
        tokens.append(AudioToken(word, start, end))
    if not tokens:
        raise ValueError('WhisperX has no usable word records.')
    return tokens, notices


def group_match(source: Sequence[SourceToken], audio: Sequence[AudioToken],
                i: int, ni: int, j: int, nj: int) -> Optional[str]:
    # Multiple original tokens cannot be combined across sentence boundaries.
    if ni > 1 and any((x.paragraph, x.sentence) !=
                      (source[i].paragraph, source[i].sentence) for x in source[i:i+ni]):
        return None
    a = ''.join(norm(x.text) for x in source[i:i+ni])
    b = ''.join(norm(x.text) for x in audio[j:j+nj])
    if a and a == b:
        return 'group_exact'
    if nj == 1 and ni > 1 and a in contraction_expansions(audio[j].text):
        return 'contraction'
    if ni == 1 and nj > 1 and b in contraction_expansions(source[i].text):
        return 'expanded_audio'
    return None


def singleton_kind(source_word: str, audio_word: str) -> Optional[str]:
    a, b = norm(source_word), norm(audio_word)
    if a and a == b:
        return 'exact'
    if gb_us_variant(a, b):
        return 'spelling_variant'
    if a in contraction_expansions(audio_word) or b in contraction_expansions(source_word):
        return 'contraction_spelling'
    # Fuzzy replacements require context and must be reported, never silently corrected.
    if a and b and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.58:
        return 'text_substitution'
    return None


def gap_alignment(src: Sequence[SourceToken], wav: Sequence[AudioToken],
                  base_i: int, base_j: int, strong_flanks: bool) -> Tuple[List[Match], List[int], List[int]]:
    """Local edit-DP with exact group and contraction transitions."""
    n, m = len(src), len(wav)
    if n == 0:
        return [], [], list(range(base_j, base_j + m))
    if m == 0:
        return [], list(range(base_i, base_i + n)), []
    if n * m > 30000:
        # Avoid speculative alignment or excessive work on unrelated inputs.
        return [], list(range(base_i, base_i + n)), list(range(base_j, base_j + m))

    # Tied scores are decided deterministically by the fixed transition order.
    inf = float('inf')
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    back: List[List[Optional[Tuple[int, int, str]]]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            current = dp[i][j]
            if current == inf:
                continue
            options: List[Tuple[int, int, float, str]] = []
            if i < n and j < m:
                kind = singleton_kind(src[i].text, wav[j].text)
                if kind == 'text_substitution' and not strong_flanks:
                    kind = None
                if kind:
                    cost = {'exact': 0.0, 'spelling_variant': 0.09,
                            'contraction_spelling': 0.09, 'text_substitution': 0.6}[kind]
                    options.append((1, 1, cost, kind))
                elif n == m == 1 and strong_flanks:
                    # Only infer a totally different word if exact anchors surround it.
                    options.append((1, 1, 1.35, 'contextual_substitution'))
                for ni in range(1, min(4, n - i) + 1):
                    for nj in range(1, min(5, m - j) + 1):
                        if ni == nj == 1 or (ni > 1 and nj > 1):
                            continue
                        category = group_match(src, wav, i, ni, j, nj)
                        if category:
                            options.append((ni, nj, 0.10 + 0.02*(ni+nj-2), category))
            if i < n:
                options.append((1, 0, 1.4, 'missing_source'))
            if j < m:
                options.append((0, 1, 1.15, 'extra_audio'))
            for ni, nj, cost, category in options:
                ii, jj = i+ni, j+nj
                candidate = current + cost
                if candidate < dp[ii][jj] - 1e-9:
                    dp[ii][jj] = candidate
                    back[ii][jj] = (i, j, category)
    matches: List[Match] = []
    missing: List[int] = []
    extra: List[int] = []
    i, j = n, m
    while i or j:
        entry = back[i][j]
        if entry is None:
            raise RuntimeError('Internal DP reconstruction error.')
        pi, pj, category = entry
        if category == 'missing_source':
            missing.extend(range(base_i+pi, base_i+i))
        elif category == 'extra_audio':
            extra.extend(range(base_j+pj, base_j+j))
        else:
            matches.append(Match(base_i+pi, base_i+i, base_j+pj, base_j+j, category))
        i, j = pi, pj
    matches.reverse()
    missing.reverse()
    extra.reverse()
    return matches, missing, extra


def align(source: List[SourceToken], audio: List[AudioToken]
          ) -> Tuple[List[Match], List[int], List[int]]:
    matcher = difflib.SequenceMatcher(
        None, [norm(s.text) for s in source], [norm(w.text) for w in audio], autojunk=False)
    all_matches: List[Match] = []
    missing: List[int] = []
    extra: List[int] = []
    opcodes = matcher.get_opcodes()
    for index, (tag, ai, bi, aj, bj) in enumerate(opcodes):
        if tag == 'equal':
            all_matches.extend(Match(i, i+1, aj+(i-ai), aj+(i-ai)+1, 'exact')
                               for i in range(ai, bi))
        else:
            # Match substitution by contextual evidence only when both surrounding blocks
            # have at least two consecutive exact anchor words.
            prev = opcodes[index-1] if index else None
            nxt = opcodes[index+1] if index+1 < len(opcodes) else None
            strong_flanks = bool(prev and nxt and prev[0] == nxt[0] == 'equal' and
                                 prev[2]-prev[1] >= 2 and nxt[2]-nxt[1] >= 2)
            got, absent, unused = gap_alignment(source[ai:bi], audio[aj:bj], ai, aj, strong_flanks)
            all_matches.extend(got)
            missing.extend(absent)
            extra.extend(unused)
    all_matches.sort(key=lambda a: a.src_start)
    return all_matches, missing, extra


def find_source_location(s: SourceToken) -> Dict[str, Any]:
    obj = {'paragraph_index': s.paragraph, 'sentence_index': s.sentence}
    if s.sid is not None:
        obj['source_sid'] = s.sid
    return obj


def time_for_match(match: Match, audio: List[AudioToken]) -> Tuple[Optional[float], Optional[float]]:
    segment = audio[match.wav_start:match.wav_end]
    if any(x.start is None or x.end is None for x in segment):
        return None, None
    start = min(x.start for x in segment)
    end = max(x.end for x in segment)
    return round(start, 3), round(end, 3)


def prepare_output(title: str, paragraphs: List[List[str]],
                   source: List[SourceToken], audio: List[AudioToken],
                   matches: List[Match], missing: List[int], extra: List[int],
                   notices: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # A whole source token is either matched exactly once or unmatched.
    source_to_match: Dict[int, Match] = {}
    for m in matches:
        for k in range(m.src_start, m.src_end):
            if k in source_to_match:
                raise ValueError(f'Overlapping source matches around word {k}.')
            source_to_match[k] = m
    if sorted(set(missing)) != sorted(set(range(len(source))) - set(source_to_match)):
        raise ValueError('Unmatched source-word accounting failed.')

    counts: Dict[str, int] = collections.Counter()
    issues: List[Dict[str, Any]] = list(notices)
    for m in matches:
        counts[m.category] += 1
        if m.category in ('text_substitution', 'contextual_substitution'):
            issues.append({
                'type': 'text_substitution', 'category': m.category,
                **find_source_location(source[m.src_start]),
                'source_text': ' '.join(x.text for x in source[m.src_start:m.src_end]),
                'recognized_text': ' '.join(x.text for x in audio[m.wav_start:m.wav_end]),
                'audio_start': audio[m.wav_start].start,
                'audio_end': audio[m.wav_end-1].end,
                'requires_review': True
            })
    for k in missing:
        issues.append({'type': 'unmatched_source_word', **find_source_location(source[k]),
                       'source_text': source[k].text, 'requires_review': True})

    # Wholly unmatched preamble/trailer should not change sentence text or audio times.
    first_audio = min((m.wav_start for m in matches), default=len(audio))
    last_audio = max((m.wav_end for m in matches), default=0)
    unused_items: List[Dict[str, Any]] = []
    for where, group in [
        ('before_source', [i for i in extra if i < first_audio]),
        ('within_source', [i for i in extra if first_audio <= i < last_audio]),
        ('after_source', [i for i in extra if i >= last_audio]),
    ]:
        if not group:
            continue
        unused_items.append({'position': where, 'count': len(group),
                             'first_start': audio[group[0]].start,
                             'last_end': audio[group[-1]].end,
                             'sample': [audio[k].text for k in group[:12]]})
        if where == 'within_source':
            issues.append({'type': 'unmatched_audio_within_source', 'count': len(group),
                           'indices': group, 'requires_review': True})

    # Create ordered timed units per sentence. A group N:1 is emitted just once.
    result: List[List[Dict[str, Any]]] = [
        [{'text': text, 'timed_units': []} for text in paragraph] for paragraph in paragraphs
    ]
    index = 0
    matched_source_count = 0
    timed_source_count = 0
    timed_unit_count = 0
    time_warnings = []
    prev_global_start = -1.0
    while index < len(source):
        token = source[index]
        match = source_to_match.get(index)
        if match:
            if match.src_start != index:
                raise RuntimeError('Partial source group encountered.')
            covered = source[match.src_start:match.src_end]
            if any((x.paragraph, x.sentence) != (token.paragraph, token.sentence) for x in covered):
                raise ValueError('A matched group spans sentence boundaries.')
            text = paragraphs[token.paragraph][token.sentence][token.start_char:covered[-1].end_char]
            start, end = time_for_match(match, audio)
            count = match.src_end - match.src_start
            matched_source_count += count
            if start is not None:
                timed_source_count += count
            index = match.src_end
        else:
            text = token.text
            start = end = None
            index += 1
        if start is not None:
            if not 0 <= start < end:
                raise ValueError('Invalid generated time range.')
            if start < prev_global_start - 1e-6:
                time_warnings.append({'type': 'nonmonotonic_time',
                                      **find_source_location(token), 'text': text,
                                      'start': start, 'previous_start': prev_global_start,
                                      'requires_review': True})
            prev_global_start = max(prev_global_start, start)
        result[token.paragraph][token.sentence]['timed_units'].append([text, start, end])
        timed_unit_count += 1

    issues.extend(time_warnings)
    output = {'title': title, 'paragraphs': result}
    validate_output(output, paragraphs, source)
    status = 'needs_review' if any(issue.get('requires_review') for issue in issues) else 'passed'
    report = {
        'status': status,
        'summary': {
            'paragraphs': len(paragraphs),
            'sentences': sum(len(p) for p in paragraphs),
            'source_words': len(source),
            'whisperx_words': len(audio),
            'matched_source_words': matched_source_count,
            'timed_source_words': timed_source_count,
            'untimed_source_words': len(source) - timed_source_count,
            'unaligned_source_words': len(missing),
            'timed_units': timed_unit_count,
            'unmatched_whisperx_words': len(extra),
            'match_units_by_category': dict(sorted(counts.items())),
        },
        'ignored_audio': unused_items,
        'issues': issues,
    }
    return output, report


def validate_output(output: Dict[str, Any], paragraphs: List[List[str]],
                    source: List[SourceToken]) -> None:
    if set(output) != {'title', 'paragraphs'} or not isinstance(output['title'], str):
        raise ValueError('Output root fields violate Script JSON v1.0.')
    actual = output['paragraphs']
    if not isinstance(actual, list) or len(actual) != len(paragraphs):
        raise ValueError('Output paragraph count does not match source.')
    expected = collections.defaultdict(list)
    for token in source:
        expected[token.paragraph, token.sentence].append(token.text)
    prev_start = -1.0
    for pi, (actual_p, source_p) in enumerate(zip(actual, paragraphs)):
        if not isinstance(actual_p, list) or len(actual_p) != len(source_p):
            raise ValueError(f'Paragraph {pi} sentence count changed.')
        for si, (out_s, source_text) in enumerate(zip(actual_p, source_p)):
            if not isinstance(out_s, dict) or set(out_s) != {'text', 'timed_units'} or out_s['text'] != source_text:
                raise ValueError(f'Paragraph {pi}, sentence {si} text or fields changed.')
            units = out_s['timed_units']
            if not isinstance(units, list) or not units:
                raise ValueError(f'Paragraph {pi}, sentence {si} missing units.')
            rebuilt_words: List[str] = []
            search_pos = 0
            for u in units:
                if not isinstance(u, list) or len(u) != 3 or not isinstance(u[0], str) or not u[0]:
                    raise ValueError(f'Invalid timed unit in {pi}:{si}.')
                txt, start, end = u
                # Must be an exact source substring in forward order.
                pos = source_text.find(txt, search_pos)
                if pos < 0:
                    raise ValueError(f'Unit {txt!r} not found in source order at {pi}:{si}.')
                search_pos = pos + len(txt)
                rebuilt_words.extend(m.group() for m in WORD_RE.finditer(txt))
                if (start is None) != (end is None):
                    raise ValueError('Half-null time is forbidden.')
                if start is not None:
                    if (isinstance(start, bool) or isinstance(end, bool) or
                        not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or
                        not math.isfinite(start) or not math.isfinite(end) or
                        start < 0 or end <= start):
                        raise ValueError('Invalid time range.')
                    if start < prev_start - 1e-6:
                        # Ordering issues are surfaced in the report, not suppressed.
                        pass
                    prev_start = max(prev_start, start)
            if rebuilt_words != expected[pi, si]:
                raise ValueError(f'Word coverage mismatch at paragraph {pi}, sentence {si}.')


def atomic_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def run(source_path: Path, whisperx_path: Path, output_path: Path, report_path: Path) -> int:
    report: Dict[str, Any]
    try:
        if output_path.resolve() == report_path.resolve():
            raise ValueError('Output and report paths must be different.')
        inputs = [source_path.resolve(), whisperx_path.resolve()]
        if output_path.resolve() in inputs or report_path.resolve() in inputs:
            raise ValueError('Output paths must not overwrite input JSON files.')
        src_data, wr_data = load_json(source_path), load_json(whisperx_path)
        title, paragraphs, src_tokens = parse_source(src_data)
        audio_tokens, notices = parse_audio(wr_data)
        matches, missing, extra = align(src_tokens, audio_tokens)
        output, report = prepare_output(title, paragraphs, src_tokens, audio_tokens,
                                        matches, missing, extra, notices)
        atomic_json(output_path, output)
        atomic_json(report_path, report)
        print(f"{report['status']}: {report['summary']['paragraphs']} paragraphs, "
              f"{report['summary']['sentences']} sentences, "
              f"{report['summary']['timed_source_words']}/{len(src_tokens)} timed source words, "
              f"{len(report['issues'])} issues")
        return 0 if report['status'] == 'passed' else 2
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        report = {'status': 'failed', 'error': str(exc)}
        try:
            atomic_json(report_path, report)
        except OSError:
            pass
        print(f'failed: {exc}', file=sys.stderr)
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description='Build a Script JSON v1.0 from original text and WhisperX words.')
    p.add_argument('--source', type=Path, required=True, help='Source paragraph/sentence JSON')
    p.add_argument('--whisperx', type=Path, required=True, help='WhisperX JSON with word timestamps')
    p.add_argument('--output', type=Path, required=True, help='Destination script.json')
    p.add_argument('--report', type=Path, required=True, help='Destination alignment-report.json')
    a = p.parse_args()
    return run(a.source, a.whisperx, a.output, a.report)


if __name__ == '__main__':
    sys.exit(main())
