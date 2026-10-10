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
                raise NeedsAttentionError('原网页修正任务已超时，无法确认发送状态；请检查原会话，不会自动重发。')
            if task['status'] in TERMINAL:
                raise RuntimeError(task.get('message') or f"网页任务状态：{task['status']}")
            from services.text_jobs import check_cancelled
            check_cancelled()
            time.sleep(1)
        revised = str((task.get('result') or {}).get('revised_prompt') or '').strip()
        if not revised:
            raise ValueError('网页回复缺少修正提示词')
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
        phase('按剧情上下文重新规划')
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
        excerpt = str(args[1] or '')
        beat['source_excerpt'] = excerpt; segment['source_excerpt'] = excerpt
        _save('beats.json',beats_data); _save('video_jobs.json',jobs)
        get_generation_service().regenerate_segment_prompt(beats_data,idx)
        _check_file('video_jobs.json','segments')
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
