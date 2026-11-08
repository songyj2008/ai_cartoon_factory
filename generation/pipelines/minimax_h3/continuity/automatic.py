"""Automatic scope detection and asynchronous, metadata-only scene records."""
import copy,json
from .checkpoints import digest
from .context import freeze_scope
from .state import character_keys

def automatic_scope(jobs,uid,draft,providers='inherit'):
    from ..editor_draft import snapshot
    from services.llm import call_llm
    rows=jobs['segments'];index=next(i for i,s in enumerate(rows) if s['segment_uid']==uid)
    prefix=rows[:index+1]
    override=(rows[index].get('h3_continuity') or {}).get('config') or {}
    if override.get('manual'):
        start=next((i for i,s in enumerate(rows) if s['segment_uid']==override['start_segment_uid']),None)
        if start is not None:
            return freeze_scope(jobs,uid,{'context_id':override['context_id'],'start_index':start+1,
                'tracked_role_ids':override['tracked_role_ids'],'manual':True})
    if index==0:
        # The first segment necessarily starts its scene; no model inference is needed.
        contexts=(jobs.get('h3_scene_continuity') or {}).get('contexts',{})
        key=next((key for key,cfg in contexts.items() if cfg.get('start_segment_uid')==uid),None)
        return freeze_scope(jobs,uid,{'context_id':key or 'scene_'+digest(uid)[:12],
            'start_index':1,'tracked_role_ids':sorted(character_keys(draft['asset_manifest']))})
    inputs=[{'segment_uid':s['segment_uid'],'prompt':draft['prompt'] if s['segment_uid']==uid else snapshot(s)['prompt'],
             'scene_context_id':s.get('scene_context_id'),
             'known_context':(s.get('h3_continuity') or {}).get('context_id')} for s in prefix]
    system='''你只判断current_segment_uid这一段所在的连续场次，只输出一个JSON对象：{"start_segment_uid":"当前段所在连续场次的首段UID","source_quote":"首段或当前段支持判断的连续原文"}。
segments只是用于阅读的历史列表，不是批量输出任务。禁止逐段返回结果，禁止返回数组或多种备选。网页封装中的data必须是单个对象，即data:{start_segment_uid:...,source_quote:...}，不能是data:[...]。
读取截止当前段的稿件。切镜、特写、反打、说话人改变不换场；明确换场、跳时、第二天或另一次会面才新建场次。同背景不一定同场次。现有明确场次ID优先，但正文明确改变时间地点时不得强套旧记录。不要提取或改写人物站位，不创建人物和剧情。start_segment_uid必须来自输入。
source_quote只复制一处短的连续原文（例如地点建立或跳时句），禁止拼接不同章节、删去中间文字、概括改写或组合多个Shot。'''
    request={'current_segment_uid':uid,'segments':inputs,'output_type':'single_object'}
    result,_=call_llm(system,json.dumps(request,ensure_ascii=False,sort_keys=True),max_tokens=1000,
        label='h3_prompt_scene_scope_'+digest(request)[:24],provider_override=providers)
    if not isinstance(result,dict): raise ValueError('场次识别结果不是对象')
    start=next((i for i,s in enumerate(prefix) if s['segment_uid']==result.get('start_segment_uid')),None)
    quote=result.get('source_quote')
    from .evidence import source_quote
    matched=next((span for r in inputs if (span:=source_quote(r['prompt'],quote)) is not None),None)
    if start is None or matched is None:
        raise ValueError('自动场次识别缺少有效来源，可展开人工校准')
    first=prefix[start]
    contexts=(jobs.get('h3_scene_continuity') or {}).get('contexts',{})
    context=next((key for key,cfg in contexts.items() if cfg.get('start_segment_uid')==first['segment_uid']),None)
    context=context or 'scene_'+digest(first['segment_uid'])[:12]
    saved=contexts.get(context) or {}
    roster=list(saved.get('tracked_role_ids') or [])
    for row in prefix[start:]:
        manifest=draft['asset_manifest'] if row['segment_uid']==uid else (row.get('generation_config') or {}).get('asset_manifest',[])
        roster.extend(sorted(character_keys(manifest)-set(roster)))
    settings={'context_id':context,'start_index':start+1,'tracked_role_ids':roster}
    # A changed screenplay may invalidate old automatic assignments. The model's
    # explicit new boundary is authoritative; original jobs remain frozen.
    frozen=copy.deepcopy(jobs)
    for row in frozen['segments'][start:index]:
        if (row.get('h3_continuity') or {}).get('status')!='current':
            row.pop('h3_continuity',None);row.pop('scene_context_id',None)
    return freeze_scope(frozen,uid,settings)

def enqueue_record(uid):
    from services import text_jobs
    from workflow.video_merge import load_video_jobs
    from ..editor_draft import snapshot,fingerprint,locate
    from .source import freeze_segment
    from services.context import get_project_dir
    jobs=load_video_jobs();row=locate(jobs,uid)
    from generation.job_schema import resolved_model_id
    if resolved_model_id(row,jobs)!='minimax_h3_local_ref2va':return None
    if (row.get('h3_continuity') or {}).get('status')=='current':return None
    if not character_keys((row.get('generation_config') or {}).get('asset_manifest',[])):return None
    draft=snapshot(row);revision=fingerprint(draft)
    # Coalesce repeated saves of the identical version, using the durable queue.
    with text_jobs.database() as db:
        for item in db.execute("SELECT payload FROM operations WHERE state IN ('preparing','queued','running','waiting_children')"):
            task=json.loads(item[0]);args=task.get('args') or []
            if (task.get('kind')=='opening_continuity' and args and args[0].get('mode')=='auto_record'
                and task.get('source')==str(get_project_dir().resolve()) and args[0].get('base_revision')==revision):return task['id']
    payload={'mode':'auto_record','segment_uid':uid,'draft':draft,'base_revision':revision,
             'source':freeze_segment(row),'scope_jobs':jobs}
    return text_jobs.submit('opening_continuity',[payload])['id']

def record_status(uid):
    from services import text_jobs
    from services.context import get_project_dir
    runtime=str(get_project_dir().resolve())
    with text_jobs.database() as db:
        for row in db.execute('SELECT payload FROM operations ORDER BY updated DESC LIMIT 200'):
            task=json.loads(row[0]);args=task.get('args') or []
            if (task.get('kind')=='opening_continuity' and task.get('source')==runtime and args
                and args[0].get('mode')=='auto_record' and args[0].get('segment_uid')==uid):
                return {k:task.get(k) for k in ('id','state','message','phase')}
    return None

def commit_automatic(item,result):
    from services import context
    from workflow.video_merge import load_video_jobs,save_video_jobs,video_jobs_lock
    from .revisions import accept_candidate_state
    # Only scene metadata is applied. A concurrent edit causes version rejection.
    from pathlib import Path
    with context.pin_runtime((Path(item['source']),item['project'],item['episode'])):
        context.assert_current_runtime_writable('自动记录场景状态')
        with video_jobs_lock():
            jobs=load_video_jobs()
            accept_candidate_state(jobs,result['draft']['continuity_patch'])
            save_video_jobs(jobs)
