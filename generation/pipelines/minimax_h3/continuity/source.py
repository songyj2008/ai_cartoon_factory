"""Freeze the explicitly selected screenplay version, never actual video pixels."""
from ..editor_draft import snapshot, fingerprint, locate
from .state import CONTRACT_VERSION

def freeze_segment(segment):
    from .revisions import revision
    draft=snapshot(segment);draft['editor_constraints']={}
    return {'segment_uid':segment['segment_uid'],'snapshot':draft,
            'prompt_hash':fingerprint({'prompt':draft['prompt'],'asset_manifest':draft['asset_manifest']}),
            'content_revision':revision(segment),'shot_uids':(segment.get('h3_shot_editor') or {}).get('shot_uids',[]),
            'record':segment.get('h3_continuity')}

def resolve_source_snapshot(jobs,target_uid,render_id=None):
    target=locate(jobs,target_uid); index=jobs['segments'].index(target)
    if not index: raise ValueError('第一段没有上一段，可直接编辑开场')
    source=jobs['segments'][index-1]; draft=snapshot(source)
    # Provenance must not recursively embed earlier opening contracts.
    draft['editor_constraints']={}
    if render_id:
        from generation.render_history import find_render
        render=find_render(source,render_id=render_id)
        if not render or not render.get('prompt_snapshot'): raise ValueError('所选视频没有提交稿')
        draft['prompt']=render['prompt_snapshot'];draft['asset_manifest']=render.get('asset_manifest') or []
        # A historical render must not borrow today's prose/state metadata.
        draft['editor_constraints']={};draft['source_excerpt']=''
        draft['duration_sec']=(render.get('requested_media_spec') or {}).get('duration_sec')
    return {'segment_uid':source['segment_uid'],'title':source.get('title'),'kind':'render_prompt' if render_id else 'saved_prompt',
            'render_id':render_id,'prompt_hash':fingerprint({'prompt':draft['prompt'],'asset_manifest':draft['asset_manifest']}),'snapshot':draft}


def validate_source_revision(jobs, target_uid, constraints):
    opening = (constraints or {}).get('opening_continuity') or {}
    if not opening.get('enabled') or opening.get('schema_version') != CONTRACT_VERSION:
        return
    source = opening.get('source') or {}
    if opening.get('scene_dependencies'):
        from .revisions import validate_dependencies
        validate_dependencies(jobs,opening['scene_dependencies'])
    if not source.get('prompt_hash'):
        return
    current = resolve_source_snapshot(jobs, target_uid, source.get('render_id'))
    if (current['segment_uid'] != source.get('segment_uid')
            or current['prompt_hash'] != source['prompt_hash']):
        raise ValueError('承接来源已变化，当前稿已保留，请重新生成承接候选')


