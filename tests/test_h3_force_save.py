import copy
from types import SimpleNamespace

import pytest

from generation.service import GenerationService
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
from workflow import video_merge


@pytest.mark.parametrize("running", [False, True])
def test_force_save_replaces_reference_baseline_and_preserves_history(monkeypatch, running):
    old = {'id': 'old_room', 'type': 'image', 'path': 'old.png', 'role': '旧场景'}
    new = {'id': 'new_room', 'type': 'image', 'path': 'new.png', 'role': '新场景'}
    prompt = '【主体定义】\n<Subject 1>：新场景；可见内容来自 <Picture 1>。\n\n【详细镜头描述】\n[Shot 1] <Subject 1> 内灯光亮起。'
    segment = {
        'segment_index': 1, 'model_id': 'minimax_h3_local_ref2va',
        'generation_config': {'asset_manifest': [old], 'asset_match_signature': 'old'},
        'renders': [{'render_id': 'original', 'prompt_snapshot': 'original prompt'}],
        'runninghub_attempts': [{'task_id': 'original', 'consume_coins': 215}],
        'runninghub_usage': {'generation_count': 1, 'total_consume_coins': 215},
        'video_path': 'original.mp4',
    }
    if running:
        segment.update(status='running', submitted=True, backend='runninghub', task_id='active')
    payload = {'segments': [segment]}
    before = copy.deepcopy(segment)
    saves = []
    monkeypatch.setattr(video_merge, 'load_video_jobs', lambda: payload)
    monkeypatch.setattr(video_merge, 'save_video_jobs', lambda value: saves.append(copy.deepcopy(value)))
    monkeypatch.setattr('generation.service.normalize_video_jobs_payload', lambda value: value)
    def reconcile(segment, old_config, new_config, text):
        result = dict(new_config)
        result['prompt_override'] = H3PromptReconciler().reconcile(
            text, H3GenerationConfig.from_mapping(old_config), H3GenerationConfig.from_mapping(new_config))
        return result
    service = object.__new__(GenerationService)
    monkeypatch.setattr(service, '_pipeline_for', lambda _: SimpleNamespace(reconcile_generation_config=reconcile))
    with pytest.raises(ValueError, match='参考素材绑定失效'):
        service.update_segment_generation_config(1, asset_manifest=[new], prompt_override=prompt)
    assert not saves
    service.update_segment_generation_config(1, asset_manifest=[new], prompt_override=prompt, force_save=True)
    assert segment['generation_config']['prompt_override'] == prompt
    assert segment['generation_config']['asset_manifest'][0]['id'] == 'new_room'
    assert 'asset_match_signature' not in segment['generation_config']
    for key in ('renders', 'runninghub_attempts', 'runninghub_usage', 'video_path'):
        assert segment[key] == before[key]
    assert segment['status'] == ('running' if running else 'needs_regenerate')
    # Ordinary saves now interpret the same Subject number using the new baseline.
    service.update_segment_generation_config(1, asset_manifest=[new], prompt_override=prompt)
    assert len(saves) == 2
    assert '旧场景' not in segment['generation_config']['prompt_override']
    assert '<Subject 1> 内灯光亮起' in segment['generation_config']['prompt_override']
    with pytest.raises(ValueError, match='非空提示词'):
        service.update_segment_generation_config(1, asset_manifest=[new], prompt_override='', force_save=True)
    assert len(saves) == 2
