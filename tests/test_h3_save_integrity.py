"""A save must not turn a complete five-picture graph into dangling tags."""
import copy
from types import SimpleNamespace

import pytest

from generation.service import GenerationService
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.editor_draft import reference_revision
from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler


@pytest.fixture
def saved_segment(monkeypatch, tmp_path):
    from workflow import video_merge
    images = [{'asset_id': f'image{i}', 'type': 'image', 'path': f'{i}.png', 'role': f'角色{i}',
               'metadata': {'asset_kind': 'character' if i < 5 else 'background',
                            'identity_id': f'identity{i}', 'role_id': f'character_{i:03d}'}}
              for i in range(1, 6)]
    audio = {'asset_id': 'voice', 'type': 'audio', 'path': 'voice.wav',
             'metadata': {'voice_only': True, 'identity_id': 'identity1', 'role_id': 'character_001'}}
    prompt = '【主体定义】\n' + '\n'.join(
        f'<Subject {i}>：角色{i}；可见内容来自 <Picture {i}>。' for i in range(1, 6))
    prompt += '\n\n【详细镜头描述】\n[Shot 1]（0–5 秒）：场景使用 <Subject 5>；画面人物：'
    prompt += '、'.join(f'<Subject {i}>' for i in range(1, 5))
    prompt += '；<Subject 1>说：<d>[Chinese]你好。</d>'
    segment = {'segment_index': 1, 'segment_uid': 'stable', 'model_id': 'minimax_h3_local_ref2va',
               'generation_config': {'asset_manifest': images + [audio], 'prompt_override': prompt},
               'job': {'prompt': prompt, 'duration_sec': 5}}
    payload = {'segments': [segment]}
    writes = []
    monkeypatch.setattr(video_merge, 'load_video_jobs', lambda: payload)
    monkeypatch.setattr(video_merge, 'save_video_jobs', lambda value: writes.append(copy.deepcopy(value)))
    monkeypatch.setattr('generation.service.normalize_video_jobs_payload', lambda value: value)
    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata', lambda items: items)
    monkeypatch.setattr('services.character_voice.refresh_character_voices', lambda items, root: items)
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_compiler.load_project_bible', lambda: {})
    service = object.__new__(GenerationService)
    def reconcile(segment, old, new, text):
        return {**new, 'prompt_override': H3PromptReconciler().reconcile(
            text, H3GenerationConfig.from_mapping(old), H3GenerationConfig.from_mapping(new))}
    service._pipeline_for = lambda model: SimpleNamespace(reconcile_generation_config=reconcile)
    return service, segment, writes


@pytest.mark.parametrize('mode', ['normal', 'force', 'web_revision', 'duration_replan'])
def test_audio_only_save_cannot_erase_five_subject_definitions(saved_segment, mode):
    service, segment, writes = saved_segment
    before = copy.deepcopy(segment)
    kwargs = {'force_save': mode == 'force'}
    if mode in {'web_revision', 'duration_replan'}:
        kwargs['prompt_provenance'] = {'source': mode}
    with pytest.raises(ValueError, match='绑定'):
        service.update_segment_generation_config(
            1, asset_manifest=[segment['generation_config']['asset_manifest'][-1]],
            prompt_override=segment['generation_config']['prompt_override'], **kwargs)
    assert segment == before
    assert writes == []


def test_repeated_text_saves_preserve_five_images_and_definitions(saved_segment):
    service, segment, writes = saved_segment
    for _ in range(3):
        service.update_segment_generation_config(
            1, prompt_override=segment['generation_config']['prompt_override'],
            base_revision=reference_revision(segment), segment_uid='stable')
        assert sum(a['type'] == 'image' for a in segment['generation_config']['asset_manifest']) == 5
        assert segment['generation_config']['prompt_override'].count('可见内容来自') == 5
    assert len(writes) == 3


def test_stale_request_and_wrong_segment_do_not_overwrite(saved_segment):
    service, segment, writes = saved_segment
    revision = reference_revision(segment)
    segment['generation_config']['prompt_override'] += '\n新的编辑'
    before = copy.deepcopy(segment)
    with pytest.raises(ValueError, match='后台提示词或参考素材已变化'):
        service.update_segment_generation_config(1, base_revision=revision)
    with pytest.raises(ValueError, match='目标分段已变化'):
        service.update_segment_generation_config(1, segment_uid='other')
    assert segment == before
    assert not writes


@pytest.mark.parametrize('invalid', [{}, 'broken', [None]])
def test_invalid_manifest_is_not_treated_as_a_clear_operation(invalid):
    with pytest.raises(ValueError, match='无效'):
        GenerationService._normalise_asset_manifest(invalid)


@pytest.mark.parametrize('existing_binding',[False,True])
def test_retime_preserves_voice_text_and_manual_save_can_refresh_binding(saved_segment,existing_binding):
    service, segment, writes = saved_segment
    original = segment['generation_config']['prompt_override']
    if existing_binding:
        original=original.replace('【详细镜头描述】','【人物音色绑定】\n<Audio 1> → <Subject 1>（S1）：音色参考。\n\n【详细镜头描述】')
        segment['generation_config']['prompt_override']=original
    manifest=GenerationService._normalise_asset_manifest(segment['generation_config']['asset_manifest'])
    service.update_segment_generation_config(1, prompt_override=original,
        prompt_provenance={'source': 'duration_replan'})
    retimed = segment['generation_config']['prompt_override']
    assert retimed==original
    assert segment['generation_config']['asset_manifest']==manifest
    service.update_segment_generation_config(1, prompt_override=retimed)
    saved = segment['generation_config']['prompt_override']
    assert saved.count('【人物音色绑定】') == 1
    assert '<Audio 1> → <Subject 1>' in saved
    assert any((a.get('asset_id') or a.get('id')) == 'voice' for a in segment['generation_config']['asset_manifest'])
    assert writes[-1]['segments'][0]['h3_editor_prompt'] == saved


def test_fractional_draft_can_be_saved_again_without_enabling_submit(saved_segment):
    service, segment, writes = saved_segment
    segment['generation_config']['duration_sec'] = 5.1
    text = segment['generation_config']['prompt_override'].replace('0–5 秒', '0–5.100 秒')
    service.update_segment_generation_config(1, prompt_override=text)
    assert segment['generation_config']['duration_sec'] == 5.1
    assert segment['planning_state'] == 'warning'
    assert any(i['code'] == 'duration_not_integer' and i['blocking'] for i in segment['planning_issues'])
    assert len(writes) == 1


def test_retime_updates_anchored_state_even_when_pending_flag_is_stale(saved_segment,monkeypatch):
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    from generation.pipelines.minimax_h3.continuity.ledger import record_from_plan
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt
    service,segment,writes=saved_segment
    segment['generation_config']['asset_manifest']=service._normalise_asset_manifest(
        segment['generation_config']['asset_manifest'])
    text=segment['generation_config']['prompt_override']
    uid=parse_prompt(text,segment['segment_uid'],segment['generation_config']['asset_manifest'])['shots'][0]['uid']
    segment['h3_shot_editor']={'shot_uids':[uid]}
    segment['h3_continuity']=record_from_plan({'shots':[{'shot_uid':uid,'world_events':[]}]},
        revision(segment),'input',opening_state={},segment_uid=segment['segment_uid'])
    segment['h3_continuity']['text_pending']=True
    before=copy.deepcopy(segment['h3_continuity'])
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('retime save must not call LLM'))
    service.update_segment_generation_config(1,prompt_override=text.replace('0–5 秒','0–6 秒'),
        prompt_provenance={'source':'duration_replan'})
    saved=writes[-1]['segments'][0]
    assert saved['h3_continuity']['source_revision']==revision(saved)
    assert saved['h3_continuity']['text_pending'] is False
    assert saved['h3_continuity']['end_state']==before['end_state']


def test_unchanged_scene_save_preserves_prompt_binding_and_state_revision(saved_segment, monkeypatch):
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    service, segment, writes = saved_segment
    segment['scene_context_id'] = 'meeting'
    segment['generation_config']['prompt_override'] += '\n【场景人物占位】\n角色1：在场；坐着。'
    original = copy.deepcopy(segment['generation_config'])
    before_revision = revision(segment)
    service._pipeline_for = lambda model: MiniMaxH3Ref2VAPipeline()
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata',
                        lambda *a: pytest.fail('no-op save cannot re-enrich references'))
    normalized = service._normalise_asset_manifest(original['asset_manifest'])
    service.update_segment_generation_config(1, prompt_override=original['prompt_override'], asset_manifest=normalized)
    saved = writes[-1]['segments'][0]
    assert saved['generation_config']['prompt_override'] == original['prompt_override']
    assert saved['generation_config']['asset_manifest'] == original['asset_manifest']
    assert revision(saved) == before_revision
