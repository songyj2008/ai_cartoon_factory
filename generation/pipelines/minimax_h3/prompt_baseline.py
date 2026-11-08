"""Restore authored visuals while retaining the current Shot speech and timing."""
import copy

from .editor_draft import fingerprint, locate, validate_candidate
from .shot_editor import parse_prompt, render_prompt


def retain_baseline(segment, draft):
    if draft.get('scene_patch') and draft.get('prompt_baseline'):
        segment.setdefault('h3_prompt_baseline', copy.deepcopy(draft['prompt_baseline']))


def restore(payload):
    from workflow.video_merge import load_video_jobs
    from .asset_binder import H3AssetBinder
    from .prompt_reconciler import H3PromptReconciler
    from .contracts import H3GenerationConfig
    from .shot_edit_service import merge_manifests

    segment = locate(load_video_jobs(), payload['segment_uid'])
    current = copy.deepcopy(payload['draft'])
    baseline = segment.get('h3_prompt_baseline') or current.get('prompt_baseline')
    if not baseline:
        raise ValueError('暂无底稿：此段尚未保存优化前底稿，无法还原')
    if baseline.get('segment_uid') != payload['segment_uid']:
        raise ValueError('底稿不属于当前分段')
    binder, reconciler = H3AssetBinder(), H3PromptReconciler()
    manifest = merge_manifests(current['asset_manifest'], baseline['asset_manifest'])
    bindings = binder.bind({'asset_manifest': manifest})
    def document(draft):
        text = reconciler._remap_reference_tags(draft['prompt'], binder.bind(draft), bindings)
        return parse_prompt(text, payload['segment_uid'], manifest)
    old, latest = document(baseline), document(current)
    old_ids, new_ids = baseline.get('shot_uids'), current.get('shot_uids')
    if len(old['shots']) != len(latest['shots']) or (old_ids and new_ids and old_ids != new_ids):
        raise ValueError('Shot 结构已变化，无法对应最新台词；请重新生成分段并建立底稿')
    for original, now in zip(old['shots'], latest['shots']):
        rows = copy.deepcopy(now['dialogues'])
        # Keep matching source spans so existing lines are replaced in place.
        for row, prior in zip(rows, original['dialogues']):
            row['uid'] = prior['uid']
        original['dialogues'] = rows
        original['duration_cs'] = now['duration_cs']
    candidate = copy.deepcopy(current)
    for key in ('scene_patch', 'state_guard', 'composition_patch', 'state_only',
                'state_preparation', 'operation_contract', 'operation_result',
                'optimization_attempt_id', 'plan', 'scene_framing'):
        candidate.pop(key, None)
    candidate.update(prompt=render_prompt(old), asset_manifest=manifest, editor_constraints={})
    config = H3GenerationConfig.from_mapping(candidate)
    candidate['prompt'] = reconciler.reconcile(candidate['prompt'], config, config)
    candidate['prompt_baseline'] = copy.deepcopy(baseline)
    # Speech validation also catches stale per-line voice annotations.
    speech_base = dict(current, prompt=render_prompt(latest), asset_manifest=manifest)
    doc = validate_candidate(speech_base, candidate, allow_background_visibility=True)
    return {'draft': candidate, 'document': doc, 'operation_base': copy.deepcopy(candidate),
            'draft_hash': fingerprint(current), 'base_revision': payload['base_revision'],
            'changes': ['已还原底稿的镜头与站位，保留最新台词和时长；点击顶部保存。']}
