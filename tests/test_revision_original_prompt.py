from generation.service import GenerationService


def test_web_revision_is_saved_without_rebuilding_subjects(monkeypatch, tmp_path):
    # A Subject 2 requires two bound images. Keep compiler-managed reference
    # sections valid so this checks revision preservation, not missing assets.
    import base64
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=')
    manifest = []
    for index, name in enumerate(['其他人物', '王大力'], 1):
        path = tmp_path / f'person_{index}.png'
        path.write_bytes(png)
        manifest.append({'id': f'person-{index}', 'type': 'image', 'path': str(path),
                         'role': name, 'order': index})
    text = ('【主体定义】\n'
            '<Subject 1>：其他人物；可见内容来自 <Picture 1>。\n'
            '<Subject 2>：王大力；可见内容来自 <Picture 2>。\n\n'
            '【参考保留关系】\n'
            '<Subject 1>：fully_preserved - 外观、结构与材质。\n'
            '<Subject 2>：fully_preserved - 外观、结构与材质。\n\n'
            '【详细镜头描述】\n只出现王大力。')
    payload = {'default_model_id': 'minimax_h3_local_ref2va', 'segments': [{
        'segment_index': 1, 'model_id': 'minimax_h3_local_ref2va',
        'generation_config': {'asset_manifest': manifest}, 'job': {'prompt': 'old'},
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
    assert '<Subject 2>：王大力；可见内容来自 <Picture 2>。' in config['prompt_override']
