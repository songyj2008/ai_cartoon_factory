"""Whitelisted business operations. All runtime writes use the job snapshot."""
import json
from pathlib import Path
import time

from services.context import CONFIG, get_project_dir
from services.text_jobs import phase


def _save(name, value):
    (get_project_dir() / name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def _check_file(name, key=None):
    path = get_project_dir() / name
    if not path.is_file():
        raise ValueError('生成未成功：' + name + ' 不存在，请查看任务日志')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not data or (key and not data.get(key)):
        raise ValueError('生成结果为空：' + name)
    return data


def _save_editors(story, beats):
    from ui.project_handlers import save_story_and_beats_edits
    saved = save_story_and_beats_edits(story,beats)
    if any(marker in str(saved[:2]) for marker in ('🔴【风险','❌')):
        raise ValueError('剧情或 Beats 结构校验未通过，请检查输入')
    return _check_file('beats.json','beats')


def execute(kind, args):
    from ui import project_handlers as p
    from generation.service import get_generation_service
    phase('准备输入快照')
    if kind == 'scene_state':
        from services.scene_state_jobs import build_scene_state_patch
        return build_scene_state_patch(args[0])
    if kind == 'opening_continuity':
        from generation.pipelines.minimax_h3.continuity import build_opening_candidate
        return build_opening_candidate(args[0])
    if kind in ('novel_beats', 'novel_segment'):
        from services.novel_batches import execute_beats, execute_segment
        result = (execute_beats if kind == 'novel_beats' else execute_segment)(args)
        phase('校验并保存候选结果')
        _check_file('beats.json', 'beats')
        _check_file('video_jobs.json', 'segments' if kind == 'novel_segment' else None)
        return result
    if kind == 'video_learning':
        from services.video_experience import analyze
        phase('分析已确认成片')
        return analyze(args[1], args[2])
    if kind == 'revision_wait':
        from services.web_llm_bridge import task_status, TERMINAL
        from services.text_jobs import NeedsAttentionError
        child_id, index = str(args[0]), int(args[1])
        if not child_id:
            from services import text_jobs
            from services.web_llm_bridge import revision_task
            if len(args) < 4 or not isinstance(args[2],dict) or not isinstance(args[3],dict):
                raise RuntimeError('修正任务缺少可恢复的输入快照，需要人工处理')
            operation = text_jobs.current()
            source = Path(operation['source'])
            for name in ('beats.json','video_jobs.json'):
                if text_jobs.digest(source / name) != operation['baseline'].get(name):
                    raise RuntimeError('原项目在网页请求发送前已变化，已停止发送；候选需要人工核对')
            task = revision_task(index,args[2],operation_id=operation['id'],
                                 provider_override=operation['provider'],snapshot_override=args[3])
            child_id = task['id']
            text_jobs.attach_child(operation['id'],child_id)
            text_jobs.update(operation['id'],args=[child_id,index,args[2],args[3]])
        phase('修正任务 · 等待网页回复')
        while True:
            task = task_status(child_id, current_runtime=False)
            if task['status'] == 'complete':
                break
            if task['status'] == 'timeout':
                if task.get('timeout_phase') == 'queue':
                    raise RuntimeError(task['message'])
                raise NeedsAttentionError('原网页修正任务已超时，无法确认发送状态；请检查原会话，不会自动重发。')
            if task['status'] in TERMINAL:
                raise RuntimeError(task.get('message') or f"网页任务状态：{task['status']}")
            from services.text_jobs import check_cancelled
            check_cancelled()
            time.sleep(1)
        revised = str((task.get('result') or {}).get('revised_prompt') or '').strip()
        if not revised:
            raise ValueError('网页回复缺少修正提示词')
        review = (task.get('result') or {}).get('revision_validation')
        if (task.get('input') or {}).get('model_id') == 'minimax_h3_local_ref2va':
            from generation.pipelines.minimax_h3.revision_review import compare_revision
            from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
            revised = H3PromptCompiler.normalize_visible_role_lines(revised)
            review = compare_revision((task.get('input') or {}).get('base_prompt') or '', revised,
                manifest=(task.get('metadata') or {}).get('speech_manifest'),
                target_duration_sec=(task.get('input') or {}).get('target_duration_sec'))
        if review and review.get('blocking_issues'):
            return {'message': '修正版结构需处理，已保留完整候选，未覆盖原稿。',
                    'revised_prompt': revised, 'changes': (task.get('result') or {}).get('changes') or [],
                    'revision_validation': review, 'web_task_id': child_id, 'segment_index': index}
        phase('保存修正候选')
        updated_at = task.get('updated_at')
        if isinstance(updated_at, (int, float)):
            from datetime import datetime, timezone
            updated_at = datetime.fromtimestamp(updated_at, tz=timezone.utc).isoformat()
        provenance = {
            'source': 'web_revision',
            'version': int((task.get('metadata') or {}).get('prompt_version') or 1),
            'updated_at': updated_at,
            'provider': str(task.get('provider') or '网页 LLM'),
            'task_id': child_id,
        }
        get_generation_service().update_segment_generation_config(
            index,
            prompt_override=revised,
            prompt_provenance=provenance,
        )
        return {'message':'修正提示词已生成候选，可在任务面板应用到原项目。',
                'revised_prompt':revised,'changes':(task.get('result') or {}).get('changes') or [],
                'prompt_provenance':provenance,
                'revision_validation':review,
                'web_task_id':child_id,'segment_index':index}
    if kind == 'story':
        topic, _story, duration, custom = args
        phase('生成剧情')
        text,_ = p.generate_story_with_duration(topic,duration,custom)
        if not text or '🔴【风险' in text:
            raise ValueError('剧情生成或结构校验失败，请查看任务日志')
        _check_file('story.json')
    elif kind == 'novel':
        phase('小说拆分及生成 Segments')
        p.generate_novel_segments_with_duration(*args)
        _check_file('beats.json','beats')
        _check_file('video_jobs.json','segments')
    elif kind == 'beats':
        story,duration,custom,count,topic = args
        phase('拆分 Beats')
        last = None
        for last in p.split_beats_with_duration(story,duration,custom,topic_text=topic,target_beat_count=count):
            pass
        if not last or not last[1] or '🔴【风险' in last[1]:
            raise ValueError('Beats 生成或结构校验失败，请查看任务日志')
        _check_file('beats.json','beats')
        # Invalidation is a candidate change, never an eager delete in source.
        _save('video_jobs.json',{'segments':[]})
    elif kind in ('prompts','segment','split'):
        if kind == 'segment':
            index,_page,story,beats = args
        else:
            story,beats = args[:2]
        data = _save_editors(story,beats)
        service = get_generation_service()
        if kind == 'prompts':
            from pipeline.beat_splitter.guard import detect_complex_beats
            if not args[4] and detect_complex_beats(data).get('risky_count',0):
                raise ValueError('存在复杂 Beat，请先拆分或勾选忽略风险后重新提交')
            phase('生成分段提示词')
            service.prepare_segments(data)
        elif kind == 'segment':
            phase('重生成指定分段提示词')
            service.regenerate_segment_prompt(data,int(float(index)))
        else:
            from pipeline.beat_splitter.splitter import split_complex_beat
            from ui.beat_split_migration import migrate_after_complex_beat_split
            phase('拆分复杂 Beat')
            index = int(float(args[2]))
            result = split_complex_beat(data,index,CONFIG,get_project_dir()/'temp'/'beat_splitter')
            _save('beats.json',result['beats'])
            migrate_after_complex_beat_split(index,result['replacement_count'],result['beats'])
        if kind != 'split':
            _check_file('video_jobs.json','segments')
    elif kind == 'source_replan':
        phase('重新规划时长')
        from workflow.video_merge import load_video_jobs, save_video_jobs
        import copy
        jobs = load_video_jobs()
        idx = int(args[0])
        segment = next((row for row in jobs.get('segments') or [] if int(row.get('segment_index') or 0) == idx), None)
        beats_data = _check_file('beats.json','beats')
        if segment is None:
            raise ValueError('找不到对应的分段')
        uid = str(segment.get('beat_uid') or '')
        beat = next((row for row in beats_data['beats'] if isinstance(row,dict) and uid and str(row.get('beat_uid') or '') == uid),None)
        if beat is None and 0 < idx <= len(beats_data['beats']): beat = beats_data['beats'][idx-1]
        if not isinstance(beat,dict): raise ValueError('找不到分段对应的 Beat')
        if len(args) > 2 and args[2].get('timing_only') is True:
            from generation.job_schema import resolved_model_id
            from generation.pipelines.minimax_h3.prompt_retimer import retime_h3_prompt
            from workflow.runninghub_queue import has_active_submission

            if resolved_model_id(segment, jobs) != 'minimax_h3_local_ref2va':
                raise ValueError('重新规划时长仅支持 H3 分段')
            if has_active_submission(segment):
                raise ValueError('本段视频正在生成，请完成后再重新规划时长')
            options = args[2]
            manifest = (segment.get('generation_config') or {}).get('asset_manifest')
            if manifest is None:
                manifest = (segment.get('job') or {}).get('asset_manifest') or []
            planned = retime_h3_prompt(options.get('editor_prompt'), idx,
                                      reference_duration_sec=options['duration_sec'], asset_manifest=manifest, draft_mode=True)
            from services.duration_planning_llm import result_source
            timing_source = result_source(idx)
            duration = planned['duration_sec']
            # Only this Beat changes. The generic duration synchronizer also
            # updates unrelated stale segments and patches original workflows.
            beat['estimated_duration_sec'] = duration
            for key in ('duration_sec', 'h3_duration_sec'):
                if key in beat:
                    beat[key] = duration
            beats_data['total_duration_sec'] = sum(float(row.get('estimated_duration_sec') or row.get('duration_sec') or 0)
                                                  for row in beats_data['beats'] if isinstance(row, dict))
            segment['generation_config'] = {**(segment.get('generation_config') or {}), 'duration_sec': duration}
            segment['h3_prompt_source'] = 'duration_replan'
            segment.pop('h3_prompt_plan', None)
            segment['h3_duration_adjustment'] = {'reference_sec': options['duration_sec'], 'duration_sec': duration,
                                                 **timing_source}
            _save('beats.json', beats_data)
            _save('video_jobs.json', jobs)
            service = get_generation_service()
            service.update_segment_generation_config(idx, asset_manifest=manifest, prompt_override=planned['prompt'],
                prompt_provenance={'source': 'duration_replan', 'provider': timing_source.get('provider') or '时长规划',
                                   **({'model': timing_source['model']} if timing_source.get('model') else {})})
            # The video button rebuilds the workflow from this custom prompt.
            # Do not create workflows in the disposable candidate directory.
            from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline, H3_FPS
            completed = _check_file('video_jobs.json', 'segments')
            target = next(row for row in completed['segments'] if int(row.get('segment_index') or 0) == idx)
            saved_prompt = str((target.get('generation_config') or {}).get('prompt_override') or planned['prompt'])
            frames = MiniMaxH3Ref2VAPipeline._aligned_frame_count(duration)
            target['job'] = {**(target.get('job') or {}), 'duration_sec': duration, 'fps': H3_FPS,
                             'aligned_frame_count': frames, 'total_frames': frames,
                             'prompt': saved_prompt, 'final_prompt': saved_prompt}
            if 'director_prompt' in target['job']:
                target['job']['director_prompt'] = saved_prompt
            target['compiled_prompt_snapshot'] = saved_prompt
            from generation.pipelines.minimax_h3.planning_drafts import refresh_saved_prompt
            refresh_saved_prompt(target, saved_prompt)
            target['requested_media_spec'] = {**(target.get('requested_media_spec') or {}),
                'duration_sec': duration, 'requested_duration_sec': duration, 'fps': H3_FPS,
                'aligned_frame_count': frames, 'aligned_duration_sec': frames / H3_FPS}
            completed['total_duration_sec'] = sum(float((row.get('job') or {}).get('duration_sec') or 0)
                                                  for row in completed['segments'] if isinstance(row, dict))
            _save('video_jobs.json', completed)
            return {'message': f'已重新规划为 {duration} 秒，并同步对应 Beat', 'duration_sec': duration,
                    'duration_reason': planned['duration_reason'], **timing_source}
        excerpt = str(args[1] or '')
        beat['source_excerpt'] = excerpt; segment['source_excerpt'] = excerpt
        if len(args) > 2:
            # Page intent wins even when an older job's duration and its
            # generation config were already out of sync.
            segment['generation_config'] = {**(segment.get('generation_config') or {}),
                                            'duration_sec': args[2]['duration_sec']}
            if args[2].get('auto_duration') is True:
                segment['h3_auto_duration'] = True
            if args[2].get('editor_prompt'):
                segment['h3_editor_prompt'] = args[2]['editor_prompt']
            if 'asset_manifest' in args[2]:
                segment['generation_config']['asset_manifest'] = args[2]['asset_manifest']
        _save('beats.json',beats_data); _save('video_jobs.json',jobs)
        if len(args) > 2:
            # The candidate still references the original workflow paths.
            # Replanning builds new graphs in the isolated work directory.
            get_generation_service().update_segment_duration(idx, args[2]['duration_sec'], patch_workflows=False)
            beats_data = _check_file('beats.json', 'beats')
        get_generation_service().regenerate_segment_prompt(beats_data,idx)
        completed = _check_file('video_jobs.json','segments')
        if len(args) > 2 and args[2].get('auto_duration') is True:
            planned = next(row for row in completed['segments'] if int(row.get('segment_index') or 0) == idx)
            actual_duration = (planned.get('job') or {}).get('duration_sec')
            get_generation_service().update_segment_duration(idx, actual_duration, patch_workflows=False)
            adjustment = planned.get('h3_duration_adjustment') or {}
            return {'message': f'本段按台词重新规划为 {actual_duration} 秒', 'duration_sec': actual_duration,
                    'duration_reason': adjustment.get('reason', '')}
    elif kind == 'pipeline':
        from workflow.master import one_click_full_generation
        phase('生成剧情、Beats 及分段提示词')
        for _ in one_click_full_generation(args[0], submit_to_comfyui=False,
                duration_select_value=args[5],custom_duration=args[6],target_beat_count=args[7]):
            pass
        _check_file('video_jobs.json','segments')
        return {'message':'文字及工作流已准备。应用后，请使用生成视频按钮提交视频。'}
    elif kind == 'feedback':
        from ui.video_feedback import analyze_video_feedback
        phase('分析视频问题')
        text,_ = analyze_video_feedback(*args)
        if not text:
            raise ValueError('视频问题分析失败，请检查输入及任务日志')
        return json.loads(text)
    elif kind == 'metadata':
        from ui.asset_library_panel import complete_asset_metadata
        phase('补全素材描述')
        last = None
        for last in complete_asset_metadata(*args):
            pass
        if not last or not last[0]:
            raise ValueError(str(last[1] if last else '素材补全失败'))
        return last[0]
    else:
        raise ValueError('不支持的文字任务')
    phase('校验并保存候选结果')
