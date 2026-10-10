from generation.service import GenerationService


def test_web_revision_is_saved_without_rebuilding_subjects(monkeypatch):
    text = '【主体定义】\n<Subject 2>：王大力\n\n【详细镜头描述】\n只出现王大力。'
    payload = {'default_model_id': 'minimax_h3_local_ref2va', 'segments': [{
        'segment_index': 1, 'model_id': 'minimax_h3_local_ref2va',
        'generation_config': {'asset_manifest': []}, 'job': {'prompt': 'old'},
    }]}
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: payload)
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda *_: None)
    service = GenerationService()
    class Pipeline:
        def reconcile_generation_config(self, *args):
            raise AssertionError('must not rewrite LLM revision')
    monkeypatch.setattr(service, '_pipeline_for', lambda *_: Pipeline())
    result = service.update_segment_generation_config(1, prompt_override=text,
        prompt_provenance={'source': 'web_revision', 'task_id': 'task', 'version': 6})
    config = result['segments'][0]['generation_config']
    assert config['prompt_override'] == text
    assert config['latest_prompt_revision']['prompt'] == text
