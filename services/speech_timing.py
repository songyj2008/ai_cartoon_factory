"""Traceable timing statistics from human-approved H3 prompt snapshots.

Only explicit speech windows enter the rate distribution. Shot budgets are
retained as diagnostics, never silently substituted for speech measurements.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from services import context, video_experience as learning

VERSION = 'speech-timing-v3'
_TIME = r'(?:\d{1,2}:){1,2}\d{2}(?:\.\d+)?|\d+(?:\.\d+)?'
_RANGE = re.compile(rf'(?<![\d.:+-])(?P<a>{_TIME})\s*(?:秒|s\b)?\s*[-–—~至]\s*(?P<b>{_TIME})\s*(?:秒|s\b)?')
_SHOT = re.compile(r'\[Shot\s+\d+\]', re.I)
_DIALOGUE = re.compile(r'<d\s*>(.*?)</d\s*>', re.S | re.I)

PLANNING_RULES = '''【人工认可的台词节奏依据】
speech_timing_context 的统计来自人工已核对台词完整度和节奏可用的 OK 成片；可作为有效分配依据。窗口来自生成时提示词，非 LLM 自行听取音轨或逐句实测。
speakers只包含本次明确解析到的说话人；空列表表示没有可提供的个人统计，不代表当前稿没有对白。population是总体分布参考。
同一素材人物跨项目、跨角色名共享个人样本；当前角色名、Subject/Audio 编号及剧情仍以本次输入为准，不能照搬历史剧情。
优先参考同人物、同音色、相近表达的样本；个人样本少时结合库级中位数及样本量。没有个人样本时，结合可靠参考录音的原文、时长和语速；原文缺失时使用库级中位数，不猜录音内容。
先按当前台词字数和适用参考语速形成初步时间，再根据当前情绪、语气、句子结构与发声用途调整。中位数是依据而非固定语速；不使用固定情绪倍率，不因数值快而否定已人工认可的样本。
样本对白窗口和参考录音已包含正常换气与句间停顿，不对相同重音、标点和停顿重复加时。file_duration 可能含首尾空白，不能冒充精确发声时长。
duration_reason 简述各 Shot 采用的数值依据和调整原因；明显偏离适用样本时说明具体差异，不仅以“略紧”“沉稳”或“严肃”解释。辅助表演不逐项加时。
'''.strip()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS speech_samples (sample_id TEXT PRIMARY KEY, fingerprint TEXT, data TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS speech_identity_aliases (alias TEXT PRIMARY KEY, canonical TEXT NOT NULL)')


def canonical_identity(identity, aliases):
    seen = set()
    while identity in aliases:
        if identity in seen:
            raise ValueError('人物身份归并存在循环')
        seen.add(identity)
        identity = aliases[identity]
    return identity


def merge_identity(alias, canonical):
    """Explicit library-id mapping; names and voice similarity never merge people."""
    alias, canonical = str(alias).strip(), str(canonical).strip()
    if not alias or not canonical or alias == canonical:
        raise ValueError('需要两个不同的素材人物 ID')
    with learning.database() as db:
        _tables(db)
        aliases = dict(db.execute('SELECT alias,canonical FROM speech_identity_aliases').fetchall())
        aliases[alias] = canonical
        for identity in aliases:
            canonical_identity(identity, aliases)
        db.execute('INSERT OR REPLACE INTO speech_identity_aliases VALUES (?,?)', (alias, canonical))


def _seconds(value):
    result = 0.0
    for part in str(value).split(':'):
        result = result * 60 + float(part)
    return result


def _ranges(text):
    return [(m.start(), m.end(), _seconds(m['a']), _seconds(m['b'])) for m in _RANGE.finditer(text)]


def count_hanzi(text):
    """Do not invent spoken expansions for numbers or mixed-language text."""
    count = len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', text))
    return count, bool(count and not re.search(r'[A-Za-z0-9]', text))


def _path(value):
    path = Path(str(value or ''))
    return path if path.is_absolute() else context.BASE_DIR / path


def _voices(manifest):
    voices = {}
    for item in manifest or []:
        meta = item.get('metadata') or {}
        identity = str(meta.get('canonical_identity_id') or meta.get('identity_id') or '')
        if item.get('type') == 'audio' and identity:
            path = _path(item.get('path'))
            voices[identity] = learning.file_hash(path) if path.is_file() else ''
    return voices


def _people(manifest, prompt=''):
    # Use immutable manifest order and explicit Subject->Picture definitions,
    # never the currently selected project's Bible or a historical role name.
    ordered = sorted(enumerate(manifest or []), key=lambda pair: (
        pair[1].get('order') if isinstance(pair[1].get('order'), (int, float)) else pair[0] + 1, pair[0]))
    images = [item for _, item in ordered if item.get('type') == 'image']
    subject_picture = {int(m[1]): int(m[2]) for m in re.finditer(
        r'<Subject\s+(\d+)>[^\n]*?<Picture\s+(\d+)>', prompt, re.I)}
    people = []
    for index, image in enumerate(images, 1):
        meta = image.get('metadata') or {}
        identity = str(meta.get('canonical_identity_id') or meta.get('identity_id') or '')
        if not identity or meta.get('asset_kind') == 'background':
            continue
        tags = [f'<Subject {s}>' for s, p in subject_picture.items() if p == index]
        names = [image.get('role'), image.get('label'), meta.get('role_id')]
        names.extend(meta.get('aliases') or [])
        people.append({'identity_id': identity, 'role_name': image.get('role') or image.get('label') or '',
                       'subject_tags': tags, 'names': list(dict.fromkeys(str(n) for n in names if n))})
    # Telephone/narrator-only assets can have an identity without an image.
    known = {p['identity_id'] for p in people}
    for item in manifest or []:
        meta = item.get('metadata') or {}
        identity = str(meta.get('canonical_identity_id') or meta.get('identity_id') or '')
        if item.get('type') == 'audio' and identity and identity not in known:
            names = [meta.get('role_id'), meta.get('speaker_name'), item.get('label')]
            people.append({'identity_id': identity, 'role_name': meta.get('speaker_name') or item.get('label') or '',
                           'subject_tags': [], 'names': [str(n) for n in names if n]})
            known.add(identity)
    return people


def _owner(prefix, people):
    matches = []
    for person in people:
        for name in person['subject_tags'] + person['names']:
            pattern = re.escape(name) + r'(?:\s*[（(][^）)]*[）)])?[^<>；;\n]{0,45}说\s*[:：]\s*$'
            if re.search(pattern, prefix, re.I):
                matches.append(person)
                break
    identities = {p['identity_id'] for p in matches}
    return matches[0] if len(identities) == 1 else None


def _union_length(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def _declared_speech_windows(prefix, windows):
    """Read explicit speech-state declarations, not the nearest closing pause.

    Multiple narrative intervals require an explicit speech declaration. An
    unqualified single interval immediately preceding the line is unambiguous.
    No rate thresholds or emotion/action duration heuristics are used.
    """
    explicit = []
    silent = set()
    for index, (_begin, end, start_sec, end_sec) in enumerate(windows):
        stop = windows[index + 1][0] if index + 1 < len(windows) else len(prefix)
        description = prefix[end:stop]
        # Strip the final dialogue declaration itself; it does not label an
        # earlier silent interval as speech merely because the text follows.
        description = re.sub(r'<Subject\s+\d+>(?:\s*[（(][^）)]*[）)])?[^<>；;\n]{0,45}说\s*[:：]\s*$', '', description)
        opening = description.lstrip(' 秒s：:，,\n\r\t')[:100]
        ends_speech = re.search(r'说完(?:后)?(?:闭嘴|闭口|，闭嘴|，闭口)|停止(?:说话|发言|发声)|闭嘴|闭口', opening)
        if ends_speech:
            silent.add((start_sec, end_sec))
        elif re.search(r'说话|说出|说完(?:完整)?台词|说完整句|完整说出|全部台词|全部对白|连续说完|开始(?:发言|发问)|发言期间|发声期间', description):
            explicit.append([start_sec, end_sec])
    if explicit:
        return explicit, silent
    if len(windows) == 1 and (windows[0][2], windows[0][3]) not in silent:
        return [[windows[0][2], windows[0][3]]], silent
    return [], silent


def extract_sample(sample, analysis=None):
    prompt = str(sample.get('generated_prompt') or '')
    manifest = sample.get('asset_manifest') or []
    people = _people(manifest, prompt)
    voices = sample.get('speaker_voice_fingerprints')
    if voices is None:
        voices = _voices(manifest)
    headers = list(_SHOT.finditer(prompt))
    lines = []
    candidate_rows = (analysis or {}).get('speech_timing')
    candidate_rows = candidate_rows if isinstance(candidate_rows, list) else []
    candidates = {item.get('line_id'): item for item in candidate_rows
                  if isinstance(item, dict) and isinstance(item.get('line_id'), int)
                  and not isinstance(item.get('line_id'), bool)}
    for line_id, match in enumerate(_DIALOGUE.finditer(prompt), 1):
        text = re.sub(r'^\s*\[(?:Chinese|English|中文|英文)\]\s*', '', match[1], flags=re.I).strip()
        header = next((h for h in reversed(headers) if h.start() < match.start()), None)
        next_header = next((h.start() for h in headers if h.start() > match.start()), len(prompt))
        block = prompt[header.start():next_header] if header else ''
        header_range = _ranges(block.split('\n', 1)[0])[:1]
        # The first interval is the Shot budget. Explicit dialogue intervals
        # must occur after its closing parenthesis, before this dialogue.
        body_start = header.end() if header else 0
        if header and header_range:
            close = re.search(r'[）)]', prompt[header.end():match.start()])
            if close:
                body_start = header.end() + close.end()
        previous_dialogue = list(_DIALOGUE.finditer(prompt[body_start:match.start()]))
        if previous_dialogue:
            body_start += previous_dialogue[-1].end()
        prefix = prompt[body_start:match.start()]
        windows = _ranges(prefix)
        intervals, silent = _declared_speech_windows(prefix, windows)
        candidate = candidates.get(line_id)
        # LLM may select an explicit interval; it cannot invent one, borrow
        # another line's window, or relabel the speaker/words.
        if candidate and candidate.get('line') == text:
            proposed = candidate.get('speech_intervals')
            available = {(a, b) for _, _, a, b in windows} - silent
            if intervals and len(windows) > 1:
                available.intersection_update(tuple(pair) for pair in intervals)
            if (isinstance(proposed, list) and proposed and all(
                    isinstance(pair, list) and len(pair) == 2
                    and all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in pair)
                    and tuple(pair) in available for pair in proposed)):
                intervals = proposed
        owner = _owner(prefix, people)
        count, supported = count_hanzi(text)
        reason = ''
        if not supported:
            reason = 'unsupported_counting_basis'
        elif not intervals:
            reason = 'missing_explicit_speech_window'
        if intervals and (any(not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a for a, b in intervals)
                          or (header_range and any(a < header_range[0][2] or b > header_range[0][3] for a, b in intervals))):
            reason = 'invalid_or_cross_shot_window'
        row = {'line_id': line_id, 'line_ids': [line_id], 'line': text, 'hanzi_count': count,
               'identity_id': owner['identity_id'] if owner else '',
               'identity_reason': '' if owner else 'unresolved_speaker_identity',
               'role_name_at_generation': owner['role_name'] if owner else '',
               'voice_fingerprint': voices.get(owner['identity_id'], '') if owner else '',
               'speech_intervals': intervals, 'timing_basis': 'approved_prompt_speech_window',
               'counting_basis': 'hanzi_excluding_punctuation', 'language': 'zh',
               'delivery': block[block.find('声音：') + 3:].split('连续性：', 1)[0][:300] if '声音：' in block else '',
               'scope': 'telephone' if '电话' in prefix[-100:] else 'narration' if '旁白' in prefix[-100:] else 'offscreen' if '画外' in prefix[-100:] else 'onscreen',
               'source_evidence': prefix[-1500:], 'eligibility_reason': reason}
        delivery = (candidate or {}).get('delivery')
        if isinstance(delivery, str) and delivery.strip() and delivery in block:
            row['delivery'] = delivery[:300]
        continuing = bool(re.search(r'接着上个\s*Shot\s*继续说', prefix, re.I))
        if continuing and lines and owner and lines[-1]['identity_id'] == row['identity_id']:
            previous = lines[-1]
            previous['line_ids'].append(line_id)
            if previous['line'] != text:
                previous['line'] += text
            previous['hanzi_count'], supported = count_hanzi(previous['line'])
            previous['speech_intervals'].extend(intervals)
            previous['eligibility_reason'] = previous['eligibility_reason'] or reason
            previous['source_evidence'] += '\n' + row['source_evidence']
        else:
            lines.append(row)
    for row in lines:
        duration = _union_length(row['speech_intervals'])
        row['speech_window_sec'] = duration
        row['chars_per_sec'] = row['hanzi_count'] / duration if duration > 0 and not row['eligibility_reason'] else None
    return {'lines': lines, 'human_review': sample.get('human_review') or {
        'reviewed_by': 'human', 'dialogue_complete': True, 'pacing_approved': True, 'source': 'video_ok'},
        'model_id': sample.get('model_id'), 'render_id': sample.get('render_id'),
        'sample_id': sample.get('sample_id'), 'project': sample.get('project')}


def index_sample(sample, analysis=None, db=None):
    if db is None:
        with learning.database() as connection:
            return index_sample(sample, analysis, connection)
    _tables(db)
    fingerprint = _digest([VERSION, sample.get('generated_prompt'), sample.get('asset_manifest'),
                           sample.get('human_review'), sample.get('speaker_voice_fingerprints'),
                           (analysis or {}).get('speech_timing')])
    old = db.execute('SELECT fingerprint,data FROM speech_samples WHERE sample_id=?', (sample['sample_id'],)).fetchone()
    if old and old['fingerprint'] == fingerprint:
        return json.loads(old['data'])
    data = extract_sample(sample, analysis)
    db.execute('INSERT OR REPLACE INTO speech_samples VALUES (?,?,?)',
               (sample['sample_id'], fingerprint, json.dumps(data, ensure_ascii=False)))
    return data


def _summary(rows):
    rates = [r['chars_per_sec'] for r in rows]
    result = {'line_count': len(rows), 'render_count': len({r['render_key'] for r in rows}),
              'median_chars_per_sec': statistics.median(rates) if rates else None,
              'weighted_mean_chars_per_sec': sum(r['hanzi_count'] for r in rows) / sum(r['speech_window_sec'] for r in rows) if rows else None}
    if rates:
        result.update(min_chars_per_sec=min(rates), max_chars_per_sec=max(rates))
    if len(rates) >= 4:
        q = statistics.quantiles(rates, n=4, method='inclusive')
        result.update(q1_chars_per_sec=q[0], q3_chars_per_sec=q[2])
    return result


def _example(row):
    return {key: row[key] for key in ('sample_id', 'line_ids', 'hanzi_count', 'speech_window_sec',
                                     'chars_per_sec', 'scope', 'delivery', 'timing_basis')}


def _reference_audio(manifest, identity):
    for item in manifest or []:
        meta = item.get('metadata') or {}
        if item.get('type') != 'audio' or str(meta.get('canonical_identity_id') or meta.get('identity_id') or '') != identity:
            continue
        transcript = str(meta.get('transcript') or '').strip()
        duration = meta.get('duration_sec')
        # Manual voice selection may not carry transcript in the manifest.
        # Read it only if library metadata identifies the exact selected file.
        from services.character_voice import read_voice, voice_path
        directory = context.ASSETS_DIR / 'characters' / 'identities' / str(meta.get('identity_id') or identity)
        if directory.resolve().parent == (context.ASSETS_DIR / 'characters' / 'identities').resolve() and (directory / 'asset_meta.json').is_file():
            try:
                _, voice = read_voice(directory)
                selected = voice_path(directory, voice)
                if selected and selected == _path(item.get('path')).resolve():
                    transcript = transcript or str(voice.get('transcript') or '').strip()
                    duration = duration or voice.get('duration_sec')
            except (OSError, ValueError, TypeError):
                pass
        count, supported = count_hanzi(transcript)
        valid = (supported and isinstance(duration, (int, float)) and not isinstance(duration, bool)
                 and math.isfinite(duration) and duration > 0)
        return {'transcript': transcript, 'hanzi_count': count, 'duration_sec': duration,
                'duration_basis': 'file_duration', 'chars_per_sec': count / duration if valid else None,
                'eligibility_reason': '' if valid else 'missing_or_unsupported_transcript_or_duration'}
    return None


def build_context(manifest, *, model_id=None):
    """Query all active snapshots across projects; no cached numeric baseline."""
    model_id = model_id or learning.MODEL_ID
    records, aliases, diagnostics = [], {}, {}
    if learning.library_path().exists():
        with learning.database() as db:
            _tables(db)
            aliases = dict(db.execute('SELECT alias,canonical FROM speech_identity_aliases').fetchall())
            samples = db.execute('SELECT sample,analysis FROM samples WHERE active=1 ORDER BY updated DESC,id').fetchall()
            seen = set()
            for raw in samples:
                sample = json.loads(raw['sample'])
                if sample.get('model_id') != model_id:
                    continue
                analysis = json.loads(raw['analysis']) if raw['analysis'] else {}
                data = index_sample(sample, analysis, db)
                review = data['human_review']
                if not (review.get('reviewed_by') == 'human' and review.get('dialogue_complete') and review.get('pacing_approved')):
                    continue
                # Archive copies share a Render id and immutable generation
                # snapshot. Retrying approval never multiplies observations.
                render_key = _digest([sample.get('render_id'), sample.get('generated_prompt')])
                for line in data['lines']:
                    if line['chars_per_sec'] is None:
                        reason = line['eligibility_reason']
                        diagnostics[reason] = diagnostics.get(reason, 0) + 1
                        continue
                    identity = canonical_identity(line['identity_id'], aliases)
                    key = (render_key, tuple(line['line_ids']), identity)
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append({**line, 'canonical_identity_id': identity, 'render_key': render_key,
                                    'sample_id': sample['sample_id']})
    speakers = []
    voices = _voices(manifest)
    for person in _people(manifest):
        identity = canonical_identity(person['identity_id'], aliases)
        own = [r for r in records if r['canonical_identity_id'] == identity]
        voice = voices.get(person['identity_id'], '')
        same_voice = [r for r in own if voice and r['voice_fingerprint'] == voice]
        examples = sorted(own, key=lambda r: bool(voice and r['voice_fingerprint'] == voice), reverse=True)[:8]
        speakers.append({'canonical_identity_id': identity, 'identity_id': person['identity_id'], 'current_role_name': person['role_name'],
                         'role_aliases': person['names'], 'voice_fingerprint': voice,
                         'personal': _summary(own), 'same_voice': _summary(same_voice),
                         'reference_audio': _reference_audio(manifest, person['identity_id']),
                         'examples': [_example(r) for r in examples]})
    population = {'model_id': model_id, 'language': 'zh', 'counting_basis': 'hanzi_excluding_punctuation',
                  **_summary(records)}
    ordered = sorted(records, key=lambda row: row['chars_per_sec'])
    positions = range(len(ordered)) if len(ordered) <= 8 else sorted({round(i * (len(ordered) - 1) / 7) for i in range(8)})
    examples = [_example(ordered[i]) for i in positions]
    return {'schema': VERSION, 'population': population, 'speakers': speakers,
            'examples': examples,
            'human_review': {'reviewed_by': 'human', 'dialogue_complete': True, 'pacing_approved': True,
                             'source': 'active_video_ok'},
            'statistics_version': _digest([population, speakers, examples]), 'excluded_records': diagnostics,
            'unassigned_identity_records': sum(not r['canonical_identity_id'] for r in records)}


def planning_context(manifest):
    """Common input builder for both H3 planners, with explicit absence on error."""
    try:
        return build_context(manifest)
    except Exception as exc:
        from services.logger import log
        log(f'[speech_timing] 节奏统计暂不可用：{exc}', 'WARN')
        return {'schema': VERSION, 'status': 'unavailable', 'reason': str(exc)}


def task_planning_context(manifest, *, prompt='', dialogue=None):
    """Project existing statistics to explicit owners; never guess from listeners."""
    from .h3_experience_matching import speaker_ids
    data = planning_context(manifest)
    if data.get('status') == 'unavailable':
        return data
    owners = speaker_ids(manifest, prompt, dialogue, people_reader=_people,
                         owner_reader=_owner, canonical=lambda identity: identity)
    result = {key: value for key, value in data.items()
              if key not in ('speakers', 'examples', 'statistics_version',
                             'excluded_records', 'unassigned_identity_records')}
    result['speakers'] = []
    for speaker in data.get('speakers', []):
        if speaker.get('identity_id', speaker.get('canonical_identity_id')) not in owners:
            continue
        item = dict(speaker)
        item['examples'] = list(speaker.get('examples') or [])[:2]
        result['speakers'].append(item)
    return result
