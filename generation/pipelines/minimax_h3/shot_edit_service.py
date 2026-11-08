"""Preview and commit one consistent batch of manual H3 Shot edits."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .asset_binder import H3AssetBinder
from .contracts import H3GenerationConfig
from .prompt_reconciler import H3PromptReconciler
from .shot_editor import parse_prompt, render_prompt, timeline, duration_errors, dialogue_changed, dialogue_units, silent_body, restore_dialogue_ids
from .validator import H3Ref2VAValidator

MODEL = 'minimax_h3_local_ref2va'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _prompt(segment):
    cfg, job = segment.get('generation_config') or {}, segment.get('job') or {}
    return str(cfg.get('prompt_override') or job.get('final_prompt') or job.get('prompt') or '')


def _editing_fingerprint(beats, jobs):
    """Track editable content, not provider polling or independent routing."""
    segments = []
    for segment in jobs.get('segments') or []:
        config = {key: value for key, value in (segment.get('generation_config') or {}).items()
                  if key not in {'channel_id', 'prompt_version_info', 'latest_prompt_revision'}}
        job = segment.get('job') or {}
        segments.append({'identity': {key: segment.get(key) for key in (
            'segment_uid', 'beat_uid', 'segment_id', 'part_id', 'segment_index', 'title',
            'model_id', 'model_override', 'generation_mode', 'source_excerpt')},
            'editor_constraints': segment.get('h3_editor_constraints') or {}, 'config': config, 'prompt': _prompt(segment), 'duration_sec': job.get('duration_sec')})
    return _hash({'beats': beats, 'segments': segments, 'default_model_id': jobs.get('default_model_id')})


def _uid(segment, index, root):
    return segment.get('segment_uid') or 'segment_' + _hash([str(root), index, segment.get('beat_uid')])[:24]


def merge_manifests(target, source):
    result = copy.deepcopy([item for _, item in sorted(enumerate(target),
        key=lambda pair: (pair[1].get('order') is None, pair[1].get('order') if pair[1].get('order') is not None else pair[0]))])
    for item in source:
        path = H3PromptReconciler._asset_path_key(item.get('path'))
        same = [a for a in result if a.get('type') == item.get('type') and
                H3PromptReconciler._asset_path_key(a.get('path')) == path]
        ident = (item.get('metadata') or {}).get('identity_id')
        collisions = [a for a in result if a.get('type') == item.get('type') and (
            (ident and (a.get('metadata') or {}).get('identity_id') == ident)
            or (item.get('asset_id') and a.get('asset_id') == item.get('asset_id')))]
        if any(H3PromptReconciler._asset_path_key(a.get('path')) != path for a in collisions):
            raise ValueError(f'参考版本冲突：{item.get("role") or item.get("label") or ident or item.get("asset_id")}；可改为新建下一段')
        if same:
            a = same[0]
            old_meta, new_meta = a.get('metadata') or {}, item.get('metadata') or {}
            if old_meta.get('voice_usage') != new_meta.get('voice_usage') or (
                old_meta.get('identity_id') and new_meta.get('identity_id') and old_meta['identity_id'] != new_meta['identity_id']):
                raise ValueError('同一参考文件的人物或声音用途冲突，请统一绑定或新建下一段')
            if bool(a.get('use_video_audio')) != bool(item.get('use_video_audio')):
                raise ValueError('同一视频的参考音轨用途不同，请先统一配置或新建下一段')
            continue
        result.append(copy.deepcopy(item))
    for i, item in enumerate(result, 1):
        item['order'] = i
    return result


def _reconcile(document):
    config = H3GenerationConfig.from_mapping(document['config'])
    binder, reconciler = H3AssetBinder(), H3PromptReconciler()
    new = binder.bind(config)
    text = render_prompt(document, lambda body, old: reconciler._remap_reference_tags(
        body, binder.bind({'asset_manifest': old}), new))
    # Body tags are now mapped; rebuild managed sections against this manifest.
    return reconciler.reconcile(text, config, config)


def _load(root):
    _recover(root)
    beats = json.loads((root / 'beats.json').read_text(encoding='utf-8-sig'))
    jobs = json.loads((root / 'video_jobs.json').read_text(encoding='utf-8-sig'))
    if len(beats.get('beats') or []) != len(jobs.get('segments') or []):
        raise ValueError('Beat 与视频分段数量不一致，请先同步分段后再编辑 Shot')
    return beats, jobs


def _journal(root):
    return root / 'temp' / 'h3_shot_edit_transaction.json'


def _replace_json(path, value):
    temp = path.with_name(path.name + '.shot-edit.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def _recover(root):
    path = _journal(root)
    if not path.is_file():
        return
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('state') != 'committed':
        for name, before in record['before'].items():
            if name not in {'beats.json', 'video_jobs.json'}:
                raise ValueError('Shot 提交恢复记录包含非法路径')
            _replace_json(root / name, before)
    path.unlink()


def _write_batch(root, beats, jobs):
    from .segment_content_sync import write_content_pair
    write_content_pair(root, beats=beats, jobs=jobs)


def _initial_documents(root, beats, jobs, payload):
    index = int(payload.get('segment_index') or 0)
    documents = []
    for i, segment in enumerate(jobs.get('segments') or [], 1):
        uid = _uid(segment, i, root)
        cfg = copy.deepcopy(segment.get('generation_config') or {})
        text = _prompt(segment)
        if i == index:
            text = str(payload.get('editor_prompt', text))
            if 'asset_manifest' in payload:
                cfg['asset_manifest'] = copy.deepcopy(payload['asset_manifest'])
        model = segment.get('model_id') or jobs.get('default_model_id') or MODEL
        base = {'uid': uid, 'index': i, 'model_id': model, 'config': cfg,
                'editor_constraints': copy.deepcopy(segment.get('h3_editor_constraints') or {}),
                'title': segment.get('title') or f'第 {i} 段', 'source_excerpt': segment.get('source_excerpt') or '',
                'touched': i == index, 'new': False, 'removed': False, 'prompt': text}
        normalize = lambda value: str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
        base['editor_changed'] = i == index and normalize(text) != normalize(_prompt(segment))
        base['saved_duration_cs'] = round(float((segment.get('job') or {}).get('duration_sec') or 0) * 100)
        try:
            if model != MODEL:
                raise ValueError('此段不是 H3，请选择新建下一段')
            base.update(parse_prompt(text, uid, cfg.get('asset_manifest') or []))
            saved = segment.get('h3_shot_editor') or {}
            # Shot identity survives prose edits; explicit structural edits
            # carry their own UIDs through the documents below.
            if len(saved.get('shot_uids') or []) == len(base['shots']):
                for shot_index, (shot, shot_uid) in enumerate(zip(base['shots'], saved['shot_uids'])):
                    shot['uid'] = shot_uid
                    ids = (saved.get('dialogue_uids') or {}).get(shot_uid, [])
                    if len(ids) == len(shot['dialogues']):
                        restore_dialogue_ids(shot, ids)
        except ValueError as exc:
            base.update(error=str(exc), shots=[], sections=[])
        documents.append(base)
    if not 0 < index <= len(documents):
        raise ValueError('找不到编辑分段')
    if documents[index - 1].get('error'):
        raise ValueError(documents[index - 1]['error'])
    if 'source_excerpt' in payload:
        documents[index - 1]['source_excerpt'] = str(payload['source_excerpt'])
    return documents


def preview(payload, *, root=None):
    from services.context import get_project_dir
    from workflow.video_merge import video_jobs_lock
    root = Path(root) if root is not None else get_project_dir()
    with video_jobs_lock():
        beats, jobs = _load(root)
        fingerprint = _editing_fingerprint(beats, jobs)
        runtime = str(root.resolve())
        if payload.get('fingerprint') and (payload['fingerprint'] != fingerprint or payload.get('runtime') != runtime):
            raise ValueError('后台分镜、Beat、素材或段顺序已变化；草稿未提交。请保留当前文字，点击“读取当前稿”重建草稿后再保存；有转移或删段时请先备份各段内容。')
        documents = copy.deepcopy(payload.get('documents')) if payload.get('documents') is not None else _initial_documents(root, beats, jobs, payload)
        if not isinstance(documents, list) or not documents:
            raise ValueError('Shot 编辑批次不能为空')
        old_uids = [_uid(s, i, root) for i, s in enumerate(jobs.get('segments') or [], 1)]
        preserved = [d['uid'] for d in documents if not d.get('new')]
        if preserved != old_uids or len({d['uid'] for d in documents}) != len(documents):
            raise ValueError('原分段顺序或身份不完整，请刷新编辑器')
        seen, errors = set(), []
        total, active_index = 0, 0
        for doc in documents:
            if doc.get('removed'):
                if doc.get('shots'):
                    errors.append('删除空段前请先处理其中的 Shot')
                continue
            active_index += 1
            doc['index'] = active_index
            if not doc.get('touched') and not doc.get('new'):
                source = jobs['segments'][old_uids.index(doc['uid'])]
                total += float((source.get('job') or {}).get('duration_sec') or 0)
                continue
            if doc.get('model_id') != MODEL:
                raise ValueError('Shot 手动编辑仅支持 H3 分段')
            for shot in doc.get('shots') or []:
                if shot['uid'] in seen:
                    raise ValueError('同一个 Shot 被放入多个分段，请撤销重复移动')
                seen.add(shot['uid'])
                if 'dialogues' in shot:
                    dialogue_units(shot)
                doc['config']['asset_manifest'] = merge_manifests(
                    doc['config'].get('asset_manifest') or [], shot.get('reference_manifest') or [])
            doc['warnings'] = duration_errors(doc)
            if any(dialogue_changed(shot) for shot in doc.get('shots') or []):
                doc['warnings'].append('对白已修改，请核对镜头中的动作、语气、重音和说话人是否仍匹配；时长保持当前设置。')
            doc['errors'] = duration_errors(doc, draft=True)
            try:
                doc['prompt'] = _reconcile(doc) if doc.get('shots') else ''
            except ValueError as exc:
                doc['errors'].append(str(exc))
            frames = timeline(doc)
            doc['duration_cs'] = frames[-1]['end_cs'] if frames else 0
            total += doc['duration_cs'] / 100
            config = H3GenerationConfig.from_mapping(doc['config'])
            report = H3Ref2VAValidator().validate(config, duration_sec=max(4, min(15, (doc['duration_cs'] + 99) // 100)), check_files=False)
            doc['errors'].extend(issue.message for issue in report.errors)
            errors.extend(f'第 {active_index} 段：{e}' for e in doc['errors'])
        if not active_index:
            errors.append('至少保留一个非空视频段')
        return {'runtime': runtime, 'fingerprint': fingerprint, 'documents': documents,
                'total_duration_sec': total, 'errors': errors, 'valid': not errors}


def _assert_idle(segment):
    from workflow.runninghub_queue import has_active_submission
    if has_active_submission(segment) or str(segment.get('status') or '').lower() in {'running', 'queued', 'pending'}:
        raise ValueError('受影响分段正在生成或排队，请完成后再保存结构编辑；草稿已保留')


def _dialogues(text, config):
    parsed = parse_prompt(text, 'dialogue', config.to_dict()['asset_manifest'])
    return [unit for shot in parsed['shots'] for unit in dialogue_units(shot)]


def commit(payload, *, root=None, check_files=True):
    from services.context import get_project_dir, assert_current_runtime_writable
    from workflow.video_merge import video_jobs_lock
    from workflow.runninghub_history import retire_segment
    if root is None:
        assert_current_runtime_writable('保存 Shot 编辑')
    root = Path(root) if root is not None else get_project_dir()
    with video_jobs_lock():
        if not payload.get('fingerprint') or not payload.get('runtime'):
            raise ValueError('缺少编辑基线，请重新打开 Shot 编辑器')
        candidate = preview(payload, root=root)
        if not candidate['valid']:
            raise ValueError('；'.join(candidate['errors']))
        beats, jobs = _load(root)
        originals = {_uid(s, i, root): (s, beats['beats'][i - 1]) for i, s in enumerate(jobs['segments'], 1)}
        old_order = list(originals)
        new_beats, new_segments = [], []
        now = datetime.now(timezone.utc).isoformat()
        for doc in candidate['documents']:
            original, beat = originals.get(doc['uid'], ({}, {}))
            new_index = len(new_segments) + 1
            changed_position = bool(original) and old_order.index(doc['uid']) + 1 != new_index
            if doc.get('touched') or doc.get('removed') or changed_position:
                _assert_idle(original)
            if doc.get('removed'):
                if original:
                    retire_segment(jobs, original, 'manual Shot edit: empty segment removed')
                continue
            segment, beat = copy.deepcopy(original), copy.deepcopy(beat)
            segment.update(segment_uid=doc['uid'], segment_index=new_index,
                           segment_id=f'segment_{new_index:03d}', part_id=f'part_{new_index:03d}')
            beat.update(id=new_index, order=new_index, node_id=f'beat_{new_index:03d}')
            beat.setdefault('beat_uid', 'beat_' + uuid.uuid4().hex)
            segment['beat_uid'] = beat['beat_uid']
            segment['h3_editor_constraints'] = copy.deepcopy(doc.get('editor_constraints') or {})
            beat['h3_editor_constraints'] = copy.deepcopy(segment['h3_editor_constraints'])
            if doc.get('touched') or doc.get('new'):
                duration = doc['duration_cs'] / 100
                cfg = copy.deepcopy(doc['config'])
                # Channel selection auto-saves independently while this editor
                # is open. Never overwrite it with the draft's earlier value.
                live_config = original.get('generation_config') or {}
                if original:
                    if 'channel_id' in live_config:
                        cfg['channel_id'] = live_config['channel_id']
                    else:
                        cfg.pop('channel_id', None)
                cfg.update(duration_sec=duration, prompt_override=doc['prompt'])
                config = H3GenerationConfig.from_mapping(cfg)
                report = H3Ref2VAValidator().validate(config, duration_sec=duration, check_files=check_files)
                from .planning_drafts import issue
                segment['planning_issues'] = [issue(e.code, e.message, blocking=True) for e in report.errors]
                segment['planning_state'] = 'warning' if report.errors else 'ready'
                version = int((cfg.get('prompt_version_info') or {}).get('version') or 0) + 1
                cfg['prompt_version_info'] = {'version': version, 'updated_at': now, 'provider': 'Shot 手动编辑', 'source': 'shot_edit', 'task_id': ''}
                cfg.pop('latest_prompt_revision', None)
                if segment.get('video_path'):
                    segment['stale_video_path'] = segment['video_path']
                for key in ('workflow_path', 'workflow', 'video_path', 'active_render_id', 'video_ok', 'preserve_on_reset', 'protected_from_reset'):
                    segment.pop(key, None)
                job = copy.deepcopy(segment.get('job') or {})
                for key in ('workflow_path', 'video_path', 'workflow', 'video_ok', 'preserve_on_reset'):
                    job.pop(key, None)
                frames = max(5, duration * 24)
                frames += (5 - frames % 17) % 17
                job.update(part_id=f'part_{new_index:03d}', duration_sec=duration, fps=24, total_frames=frames,
                           prompt=doc['prompt'], final_prompt=doc['prompt'], director_prompt=doc['prompt'])
                segment.update(title=doc['title'], model_id=MODEL, generation_config=cfg, job=job,
                               h3_prompt_source='shot_edit', h3_editor_prompt=doc['prompt'],
                               compiled_prompt_snapshot=doc['prompt'], source_excerpt=doc.get('source_excerpt', ''),
                               prompt_saved=True, status='needs_regenerate', submitted=False,
                               h3_shot_editor={'schema_version': 2, 'prompt_hash': _hash(doc['prompt']),
                                               'shot_uids': [s['uid'] for s in doc['shots']],
                                               'dialogue_uids': {s['uid']: [r['uid'] for r in s.get('dialogues', [])] for s in doc['shots']}},
                               requested_media_spec={**(segment.get('requested_media_spec') or {}), 'duration_sec': duration,
                                                     'requested_duration_sec': duration, 'fps': 24, 'has_audio': True,
                                                     'aligned_frame_count': frames, 'aligned_duration_sec': frames / 24})
                segment.pop('h3_prompt_plan', None)
                parsed = parse_prompt(doc['prompt'], doc['uid'], cfg.get('asset_manifest') or [])
                for shot, source in zip(parsed['shots'], doc['shots']):
                    shot['uid'] = source['uid']
                    if 'dialogues' in source:
                        restore_dialogue_ids(shot, [row['uid'] for row in source['dialogues']])
                from .segment_content_sync import sync_segment_content
                speech_issues = [message for shot in parsed['shots'] for message in shot.get('speech_issues', [])]
                beat, segment, diagnostics = sync_segment_content(
                    beat, segment, prompt=doc['prompt'], generation_config=cfg,
                    inspection={'document': parsed, 'verified': not speech_issues, 'issues': speech_issues},
                    source_excerpt=doc.get('source_excerpt', ''))
                beat.update(title=doc['title'], estimated_duration_sec=duration, h3_duration_sec=duration)
                for message in diagnostics['issues']:
                    segment.setdefault('planning_issues', []).append(
                        issue('unverified_prompt_structure', message, blocking=True))
                    segment['planning_state'] = 'warning'
                if 'duration_sec' in beat:
                    beat['duration_sec'] = duration
            else:
                segment.setdefault('job', {})['part_id'] = segment['part_id']
            new_segments.append(segment)
            new_beats.append(beat)
        beats.update(beats=new_beats, total_duration_sec=sum(float(b['estimated_duration_sec']) for b in new_beats))
        jobs.update(segments=new_segments, segment_count=len(new_segments),
                    total_duration_sec=sum(float((s.get('job') or {}).get('duration_sec') or 0) for s in new_segments))
        jobs.pop('final_video_path', None)
        _write_batch(root, beats, jobs)
        from .editor_draft import saved_baseline
        return {'documents': candidate['documents'], 'total_duration_sec': jobs['total_duration_sec'],
                'editor_snapshots': {s['segment_uid']: saved_baseline(jobs, s) for s in new_segments},
                'beats_text': json.dumps(beats, ensure_ascii=False, indent=2), 'segment_count': len(new_segments)}
