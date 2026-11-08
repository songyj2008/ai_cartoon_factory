"""Version-bound H3 video learning, persisted independently of task history."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from services import context

MODEL_ID = 'minimax_h3_local_ref2va'
_LOCK = threading.RLock()

PLANNING_RULES = '''【视频 OK 原提示词案例：软参考】
approved_prompt_experiences 来自已标记视频 OK 的历史版本，提供原提示词片段和来源。人工认可对白完整度及节奏，不等于逐镜核验了静默、口型、开口时刻或音色；历史时间是计划分配，actual_duration_sec 仅为成片总长。
先看 relevance：speaker_match 是实际发言者身份相同；speaking_voice_match 才是该发言者对应音频相同。voice_match 仅表示整段参考音频有交集，可能来自其他人物，不能视为当前人物的成功记录。结构相近但音色不同的案例可参考衔接组织，不能证明当前音色稳定。
仅提炼适用于当前稿的声画组织：前镜状态、实际声音、下一镜承接及指定发言的可见起点；辅助表演与对白可并行。当前静态镜头与无对白安排仍由当前剧情决定，不能从成功动作例推导必须增加动作、删掉静默或更换音色。
当前 editor_prompt/current_beat、已确认台词、素材和用户约束始终优先。不能照搬历史剧情、台词、动作、人物、Subject/Audio 编号、时长、语速或音色绑定，不能恢复当前已删除内容。没有相近案例时按当前事实规划，不把单个成功例写成通用硬规则。
保持“参考音色＋提示词→H3 自由生成整段音视频”的路径。案例只指导本次提示词；不生成预制对白、不拆分生成音轨、不把文字时间码当作硬静音控制。
'''.strip()


def library_path():
    return context.BASE_DIR / 'assets' / 'learning' / 'h3_video_experience.sqlite3'


@contextmanager
def database():
    path = library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, sqlite3.connect(path, timeout=20) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS samples (id TEXT PRIMARY KEY, project TEXT, source TEXT, '
                   'segment_uid TEXT, active INTEGER, token TEXT, operation_id TEXT, status TEXT, '
                   'sample TEXT, analysis TEXT, updated REAL)')
        db.execute('CREATE INDEX IF NOT EXISTS samples_lookup ON samples(project,active,status,updated)')
        yield db


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def voice_fingerprints(manifest):
    result = []
    for item in manifest or []:
        if item.get('type') != 'audio':
            continue
        path = Path(item.get('path') or '')
        if not path.is_absolute():
            path = context.BASE_DIR / path
        if path.is_file():
            result.append(file_hash(path))
    return sorted(set(result))


def expected_dialogue(prompt):
    return [{'line_id': i, 'line': re.sub(r'^\[(?:Chinese|English|中文|英文)\]', '', match.group(1).strip(), flags=re.I)}
            for i, match in enumerate(re.finditer(r'<d>(.*?)</d>', prompt, re.S | re.I), 1)]


def snapshot(index, jobs):
    from generation.render_history import active_render
    from generation.media import inspect_media
    row = next((r for r in jobs.get('segments') or [] if int(r.get('segment_index') or 0) == index), None)
    if not row:
        raise ValueError('找不到视频分段')
    render = active_render(row)
    if not render or (render.get('model') or {}).get('id') != MODEL_ID:
        raise ValueError('经验分析需要当前 H3 成片的生成快照')
    output = render.get('output') or {}
    path = Path(output.get('video_path') or '')
    if not path.is_file() or output.get('video_deleted'):
        raise ValueError('当前成片文件不存在，无法分析视频')
    prompt = str(render.get('prompt_snapshot') or '').strip()
    if not prompt:
        raise ValueError('缺少成片生成时的提示词快照，不能用编辑稿代替')
    duration = (output.get('actual_media_spec') or {}).get('duration_sec')
    if not duration:
        duration = inspect_media(path).duration_sec
    if not duration or not math.isfinite(float(duration)) or float(duration) <= 0:
        raise ValueError('无法读取成片实际时长，不能当作实测样本')
    source = str(context.get_project_dir().resolve())
    from services.speech_timing import _voices
    sample_id = hashlib.sha256(json.dumps([source, row.get('segment_uid'), render['render_id'], prompt, float(duration)]).encode()).hexdigest()
    return {'sample_id': sample_id, 'project': context.get_current_project_name(), 'source': source,
            'episode': context.get_current_episode_name(), 'segment_index': index,
            'segment_uid': row.get('segment_uid') or f'index-{index}', 'render_id': render['render_id'],
            'model_id': MODEL_ID, 'actual_duration_sec': float(duration),
            'generated_prompt': prompt, 'expected_dialogue': expected_dialogue(prompt),
            'asset_manifest': render.get('asset_manifest') or [],
            'speaker_voice_fingerprints': _voices(render.get('asset_manifest') or []),
            'human_review': {'reviewed_by': 'human', 'dialogue_complete': True,
                             'pacing_approved': True, 'source': 'video_ok'},
            'voice_fingerprints': voice_fingerprints(render.get('asset_manifest') or [])}


def on_video_ok(index, jobs, approved):
    """The OK flag is already saved; learning failures must not roll it back."""
    with _LOCK:
        return _on_video_ok(index, jobs, approved)


def _on_video_ok(index, jobs, approved):
    from services import text_jobs
    row = next((r for r in jobs.get('segments') or [] if int(r.get('segment_index') or 0) == index), {})
    from generation.render_history import active_render, render_model_id
    if render_model_id(row, jobs.get('default_model_id', '')) != MODEL_ID:
        return {'state':'skipped', 'message':'视频 OK 标记已保存'}
    owner = row.get('segment_uid') or f'index-{index}'
    source = str(context.get_project_dir().resolve())
    with database() as db:
        active = db.execute('SELECT * FROM samples WHERE source=? AND segment_uid=? AND active=1', (source, owner)).fetchall()
        if not approved:
            current_id = (active_render(row) or {}).get('render_id')
            active = [sample for sample in active if json.loads(sample['sample']).get('render_id') == current_id]
            for old in active:
                db.execute('UPDATE samples SET active=0,updated=? WHERE id=?', (time.time(), old['id']))
    if not approved:
        for old in active:
            if old['operation_id']:
                try:
                    text_jobs.cancel(old['operation_id'])
                except ValueError:
                    pass
        return {'state': 'disabled', 'message': '已取消视频 OK，对应版本经验已停用'}
    sample = snapshot(index, jobs)
    with database() as db:
        previous = db.execute('SELECT * FROM samples WHERE id=?', (sample['sample_id'],)).fetchone()
        if previous and previous['active'] and previous['operation_id']:
            try:
                operation = text_jobs.get(previous['operation_id'])
            except ValueError:
                operation = None
            if previous['status'] in {'complete', 'unverified'} or (operation and operation['state'] in {'preparing','queued','running','needs_attention'}):
                return {'state': previous['status'], 'operation_id': previous['operation_id'], 'message': '此成片版本已分析或正在分析，不重复提交'}
        token = str(uuid.uuid4())
        db.execute('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (sample['sample_id'], sample['project'], source, owner, 1, token, '', 'queued',
                    json.dumps(sample, ensure_ascii=False), None, time.time()))
        from services.speech_timing import index_sample
        index_sample(sample, db=db)
    from services.web_llm_bridge import routes_for
    if 'api' in routes_for('video_learning'):
        return {'state': 'analysis_unavailable', 'message': 'OK 节奏数据已保存；请为经验分析配置网页服务'}
    task = text_jobs.submit('video_learning', [index, sample['sample_id'], token], start=False)
    with database() as db:
        db.execute('UPDATE samples SET operation_id=? WHERE id=? AND token=?', (task['id'], sample['sample_id'], token))
    text_jobs.ensure_worker()
    text_jobs._WAKE.set()
    message = '已标记视频 OK，并提交网页 LLM 提示词经验分析；可在任务面板查看'
    return {'state': 'queued', 'operation_id': task['id'], 'message': message}


def get_sample(identifier, token=None):
    with database() as db:
        row = db.execute('SELECT * FROM samples WHERE id=?', (identifier,)).fetchone()
    if not row or not row['active'] or (token is not None and row['token'] != token):
        raise ValueError('对应成片的 OK 标记已取消或版本已变化，不再学习此结果')
    return dict(row), json.loads(row['sample'])


def prompt_sections(prompt):
    """Send only the two requested sections of the historical render prompt."""
    result = {}
    for key, title in [('subject_definitions', '主体定义'), ('shot_descriptions', '详细镜头描述')]:
        match = re.search(r'【' + title + r'】(.*?)(?=【[^】\n]+】|\Z)', prompt, re.S)
        result[key] = match.group(1).strip() if match else ''
    if not result['shot_descriptions']:
        # Older snapshots may omit the section heading, but still have Shot entries.
        match = re.search(r'(\[Shot\s+1\].*?)(?=【[^】\n]+】|\Z)', prompt, re.S | re.I)
        result['shot_descriptions'] = match.group(1).strip() if match else ''
    if not result['shot_descriptions']:
        raise ValueError('历史提示词缺少详细镜头描述，无法分析')
    return result


def validate_analysis(result, sample):
    if not isinstance(result, dict) or result.get('sample_id') != sample['sample_id']:
        raise ValueError('经验分析的样本编号不匹配')
    if result.get('analysis_basis') != 'user_approved_prompt':
        raise ValueError('经验分析必须注明仅依据用户认可的提示词，不是音画实测')
    for key in ('timing_analysis', 'transition_analysis', 'speech_control_analysis', 'reusable_lessons', 'limitations'):
        values = result.get(key)
        if not isinstance(values, list) or len(values) > 30 or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values):
            raise ValueError('提示词经验分析字段格式错误：' + key)
    if not result['limitations'] or not isinstance(result.get('summary'), str) or not result['summary'].strip():
        raise ValueError('提示词经验分析缺少结论或局限说明')
    return 'complete'


def analyze(identifier, token):
    try:
        return _analyze(identifier, token)
    except Exception as exc:
        with database() as db:
            db.execute("UPDATE samples SET status='error',analysis=?,updated=? WHERE id=? AND token=? AND active=1",
                       (json.dumps({'error':str(exc)}, ensure_ascii=False), time.time(), identifier, token))
        raise


def _analyze(identifier, token):
    from services import text_jobs, web_llm_bridge as bridge
    row, sample = get_sample(identifier, token)
    # Retrying an old queued operation must not reuse its former media request.
    operation = text_jobs.current()
    if operation:
        for child_id in text_jobs.get(operation['id']).get('children', []):
            child = bridge.task_status(child_id, current_runtime=False)
            if child.get('metadata', {}).get('attachments'):
                bridge.cancel_task(child_id, current_runtime=False)
    system = (context.BASE_DIR / 'profiles' / 'h3_video_learning_rules.md').read_text(encoding='utf-8')
    data = {key: sample.get(key) for key in ('sample_id', 'project', 'episode', 'segment_index', 'segment_uid',
                                           'render_id', 'model_id', 'actual_duration_sec')}
    data.update(prompt_sections(sample['generated_prompt']))
    data['expected_dialogue'] = sample['expected_dialogue']
    data['human_review'] = sample.get('human_review') or {
        'reviewed_by': 'human', 'dialogue_complete': True, 'pacing_approved': True, 'source': 'video_ok'}
    result, _ = bridge.wait_for_llm(system, json.dumps(data, ensure_ascii=False), kind='json', entry='video_learning',
                                  provider=bridge.routes_for('video_learning'), call_hint='video_learning_text')
    status = validate_analysis(result, sample)
    text_jobs.check_cancelled()
    with database() as db:
        cursor = db.execute('UPDATE samples SET analysis=?,status=?,updated=? WHERE id=? AND active=1 AND token=?',
                            (json.dumps(result, ensure_ascii=False), status, time.time(), identifier, token))
        if not cursor.rowcount:
            raise ValueError('视频 OK 已取消，分析结果未启用')
        from services.speech_timing import index_sample
        index_sample(sample, result, db)
    return {'message': '人工认可的提示词节奏与经验已保存',
            'sample_id': identifier, 'status': status, 'analysis': result}


def retrieve(manifest, limit=3, *, prompt='', dialogue=None, shots=None, purpose='planning'):
    """Read bounded, traceable OK examples for the current speaking owners/layout."""
    if purpose not in ('planning', 'timing', 'revision'):
        raise ValueError('Unknown experience purpose: ' + str(purpose))
    path = library_path()
    if not path.exists() or limit <= 0:
        return []
    from services.speech_timing import _people, _owner, _voices, canonical_identity
    from services.h3_experience_matching import speech_layout, speaker_ids, prompt_evidence, shot_blocks

    # Retrieval must not migrate the library or turn a read into a learning task.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'samples' not in tables:
            return []
        aliases = dict(db.execute('SELECT alias,canonical FROM speech_identity_aliases').fetchall()) \
            if 'speech_identity_aliases' in tables else {}
        rows = db.execute('SELECT sample,analysis FROM samples WHERE active=1 ORDER BY updated DESC,id').fetchall()
    canonical = lambda identity: canonical_identity(identity, aliases)
    identities = {canonical(p['identity_id']) for p in _people(manifest, prompt)}
    current_speakers = speaker_ids(manifest, prompt, dialogue, people_reader=_people,
                                   owner_reader=_owner, canonical=canonical)
    current_voices = {canonical(identity): fingerprint for identity, fingerprint in _voices(manifest).items()}
    voices = set(voice_fingerprints(manifest))
    has_audio_reference = any(item.get('type') == 'audio' for item in manifest or [])
    layout = speech_layout(prompt, shots)
    contextual = bool(prompt or shots) or dialogue is not None
    examples = []
    for row in rows:
        try:
            sample = json.loads(row['sample'])
            analysis = json.loads(row['analysis']) if row['analysis'] else {}
        except (ValueError, TypeError):
            continue
        if not isinstance(sample, dict) or sample.get('model_id') != MODEL_ID:
            continue
        original = sample.get('generated_prompt')
        historical_manifest = sample.get('asset_manifest')
        historical_voice_values = sample.get('speaker_voice_fingerprints') or {}
        fingerprints = sample.get('voice_fingerprints') or []
        review = sample.get('human_review') or {}
        if (not isinstance(original, str) or not shot_blocks(original)
                or not isinstance(historical_manifest, list)
                or any(not isinstance(item, dict) or not isinstance(item.get('metadata') or {}, dict)
                       for item in historical_manifest)
                or any(not isinstance((item.get('metadata') or {}).get('aliases') or [], list)
                       for item in historical_manifest)
                or not isinstance(historical_voice_values, dict)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in historical_voice_values.items())
                or not isinstance(fingerprints, list) or any(not isinstance(value, str) for value in fingerprints)
                or not isinstance(review, dict) or not sample.get('sample_id')):
            continue
        analysis = analysis if isinstance(analysis, dict) else {}
        basis = analysis.get('analysis_basis')
        # Old media-observation analyses are a different evidence source.
        if basis and basis != 'user_approved_prompt':
            continue
        reviewed = review.get('source') == 'video_ok'
        if basis != 'user_approved_prompt' and not reviewed:
            continue
        historical_ids = {canonical(p['identity_id']) for p in _people(historical_manifest, original)}
        historical_speakers = speaker_ids(historical_manifest, original, people_reader=_people,
                                          owner_reader=_owner, canonical=canonical)
        historical_voices = {canonical(identity): fingerprint for identity, fingerprint in historical_voice_values.items()}
        matching_speakers = current_speakers.intersection(historical_speakers)
        matching_voices = {identity for identity in matching_speakers if current_voices.get(identity)
                           and current_voices[identity] == historical_voices.get(identity)}
        history_layout = speech_layout(original)
        transition_match = bool(layout['has_no_dialogue_to_dialogue']
                                and history_layout['has_no_dialogue_to_dialogue'])
        opening_match = bool(layout['starts_without_dialogue_text']
                             and history_layout['starts_without_dialogue_text'])
        project_match = sample.get('project') == context.get_current_project_name()
        person_match = bool(identities.intersection(historical_ids))
        reference_match = has_audio_reference == any(item.get('type') == 'audio' for item in historical_manifest)
        if contextual:
            relevant = bool(matching_speakers) or (transition_match and reference_match)
            if purpose == 'timing':
                relevant = bool(matching_speakers)
            if not relevant:
                continue
        elif not person_match:
            continue
        relevance = {'speaker_match': bool(matching_speakers), 'speaking_voice_match': bool(matching_voices),
                     'matching_speaker_ids': sorted(matching_speakers),
                     'matching_speaking_voice_ids': sorted(matching_voices),
                     'silent_to_dialogue_text_match': transition_match, 'opening_layout_match': opening_match,
                     'audio_reference_presence_match': reference_match, 'project_match': project_match,
                     'current_speaker_identity_resolved': bool(current_speakers),
                     'historical_speaker_identity_resolved': bool(historical_speakers)}
        example = {'sample_id': sample.get('sample_id'), 'model_id': MODEL_ID,
                   'source': {key: sample.get(key) for key in
                              ('project', 'episode', 'source', 'segment_index', 'render_id')},
                   'voice_match': bool(voices.intersection(sample.get('voice_fingerprints') or [])),
                   'person_match': person_match, 'relevance': relevance,
                   'analysis_basis': 'user_approved_prompt' if basis else 'human_approved_prompt_only',
                   'actual_duration_sec': sample.get('actual_duration_sec'),
                   'duration_basis': 'whole_media_duration_not_measured_speech_windows',
                   'speech_layout': history_layout,
                   'original_prompt_evidence': prompt_evidence(original, prefer_transition=transition_match),
                   'summary': str(analysis.get('summary') or '已标记视频 OK 的原提示词；尚无可用文本分析。')[:400]}
        fields = {'planning': ('timing_analysis', 'transition_analysis', 'speech_control_analysis', 'reusable_lessons', 'limitations'),
                  'timing': ('timing_analysis', 'limitations'),
                  'revision': ('transition_analysis', 'speech_control_analysis', 'limitations')}[purpose]
        for key in fields:
            values = analysis.get(key) or []
            example[key] = [value[:350] for value in values[:2] if isinstance(value, str)] if isinstance(values, list) else []
        if not example['limitations']:
            example['limitations'] = ['人工 OK 不等于逐镜静默、开口时刻或口型的实测核验。']
        if purpose == 'timing':
            if not example.get('timing_analysis'):
                continue
            example.pop('original_prompt_evidence', None)
            example.pop('summary', None)
        example['purpose'] = purpose
        example['source'].pop('source', None)
        examples.append(example)
    examples.sort(key=lambda item: (
        item['relevance']['speaking_voice_match'],
        item['relevance']['opening_layout_match'] and item['relevance']['audio_reference_presence_match'],
        item['relevance']['silent_to_dialogue_text_match'] and item['relevance']['audio_reference_presence_match'],
        item['relevance']['speaker_match'], item['person_match'], item['voice_match'],
        item['relevance']['project_match']), reverse=True)
    # Complement same-voice cases with a relevant transition example, if one
    # exists. Do not let several immediate-speech examples crowd it out.
    candidates = examples[:min(limit, 3)]
    if limit > 1 and layout['has_no_dialogue_to_dialogue'] and not any(
            e['relevance']['silent_to_dialogue_text_match'] and e['relevance']['audio_reference_presence_match']
            for e in candidates):
        complement = next((e for e in examples if e['relevance']['silent_to_dialogue_text_match']
                           and e['relevance']['audio_reference_presence_match']), None)
        if complement:
            candidates = candidates[:min(limit, 3) - 1] + [complement]
    if limit > 1 and current_speakers and not any(e['relevance']['speaker_match'] for e in candidates):
        owner_example = next((e for e in examples if e['relevance']['speaker_match']), None)
        if owner_example:
            candidates = candidates[:min(limit, 3) - 1] + [owner_example]
    selected, budget = [], 0
    for example in candidates:
        size = len(json.dumps(example, ensure_ascii=False))
        if budget + size <= 18000:
            selected.append(example)
            budget += size
    return selected
