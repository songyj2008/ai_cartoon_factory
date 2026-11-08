import copy

import pytest

from pipeline.novel_chunks import generate_chunked_beats, split_source


def test_source_split_is_lossless_and_does_not_cut_quoted_dialogue():
    text = '甲说：“' + '原文，' * 200 + '不要切断这句话。”\n' + '下一事件。\n' * 200
    chunks = split_source(text, 1000)
    assert ''.join(chunks) == text and len(chunks) > 1
    assert all(chunk.count('“') == chunk.count('”') for chunk in chunks)


def test_unbounded_quote_is_reported_without_silently_dropping_text():
    with pytest.raises(ValueError, match='引号'):
        split_source('“' + '甲' * 5000, 1000)


@pytest.fixture
def checkpoint(monkeypatch):
    from generation.pipelines.minimax_h3 import planning_checkpoints as saved
    cache = {}
    monkeypatch.setattr(saved, 'load_novel_beats', lambda key: copy.deepcopy(cache.get(key)))
    monkeypatch.setattr(saved, 'save_novel_beats', lambda key, value: cache.update({key: copy.deepcopy(value)}))
    return cache


def payload():
    return {'novel_text': ('甲的原文。' * 100 + '\n') * 6, 'target_duration_sec': 120,
            'target_beat_count': 20, 'project_bible': {'title': 'test'}}


def test_long_novel_batches_resume_completed_inputs_and_renumber_only(checkpoint, tmp_path):
    calls = []
    fail = True
    def generate(request, **kwargs):
        nonlocal fail
        index = request['source_batch']['index']
        calls.append(index)
        assert len(request['novel_text']) <= 1000
        assert request['project_bible'] == payload()['project_bible']
        if index == 2 and fail:
            fail = False
            raise RuntimeError('transient provider failure')
        return {'title': 'title', 'locked_outcome': 'model-owned ending', 'scene_contexts': [],
                'beats': [{'id': 1, 'plot': 'model-owned plot '+str(index),
                           'source_excerpt': request['novel_text'], 'estimated_duration_sec': 10}]}
    kwargs = dict(config={'novel_chunk_chars': 1000}, model_id='h3', temp_dir=tmp_path, generate=generate)
    with pytest.raises(RuntimeError, match='provider failure'):
        generate_chunked_beats(payload(), **kwargs)
    data = generate_chunked_beats(payload(), **kwargs)
    assert calls.count(1) == 1
    assert [b['id'] for b in data['beats']] == list(range(1, len(data['beats'])+1))
    assert ''.join(b['source_excerpt'] for b in data['beats']) == payload()['novel_text']
    assert data['beats'][0]['plot'] == 'model-owned plot 1'
    before = list(calls)
    assert generate_chunked_beats(payload(), **kwargs) == data
    assert calls == before


def test_missing_or_reordered_excerpts_do_not_publish(checkpoint, tmp_path):
    def generate(request, **kwargs):
        return {'beats': [{'source_excerpt': 'missing source', 'estimated_duration_sec': 4}]}
    with pytest.raises(ValueError, match='覆盖原文'):
        generate_chunked_beats(payload(), config={'novel_chunk_chars': 1000}, model_id='h3',
                               temp_dir=tmp_path, generate=generate)
    assert checkpoint == {}


def test_short_novel_keeps_existing_single_call_contract(checkpoint, tmp_path):
    request = dict(payload(), novel_text='短小说。')
    calls = []
    result = {'beats': [{'plot': 'unchanged'}]}
    def generate(value, **kwargs):
        calls.append(value)
        return result
    assert generate_chunked_beats(request, config={}, model_id='h3', temp_dir=tmp_path, generate=generate) is result
    assert calls == [request]
