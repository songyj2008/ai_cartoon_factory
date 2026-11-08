import copy

from ui.group_panel import _h3_config_html


def test_card_can_display_damaged_saved_prompt_without_rebuilding_or_mutating(monkeypatch):
    prompt = ('【主体定义】\n<Subject 1>：人物；可见内容来自 <Picture 1>。\n\n'
              '【详细镜头描述】\n[Shot 1]（0–5 秒）：画面人物：<Subject 1>；'
              '<Subject 1>说：<d>[Chinese]原来的对白。</d>')
    segment = {'segment_index': 1, 'segment_uid': 'damaged',
               'generation_config': {'asset_manifest': [], 'prompt_override': prompt}}
    before = copy.deepcopy(segment)
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata', lambda items: items)
    monkeypatch.setattr('services.character_voice.refresh_character_voices', lambda items, root: items)
    def forbidden(*args, **kwargs):
        raise AssertionError('render must not reconcile saved content')
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_reconciler.H3PromptReconciler.reconcile', forbidden)
    from html import unescape
    html = unescape(_h3_config_html(segment))
    assert prompt in html
    assert '参考绑定不完整' in html
    assert '原来的对白。' in html
    assert segment == before


def test_valid_saved_definitions_are_not_rewritten_during_render(monkeypatch):
    prompt = '【主体定义】\n<Subject 1>：我的原始定义。\n\n【详细镜头描述】\n[Shot 1]（0–5 秒）：<Subject 1>静静站着。'
    segment = {'segment_index': 2, 'generation_config': {
        'asset_manifest': [{'asset_id': 'image', 'type': 'image', 'path': 'reference.png', 'role': '人物'}],
        'prompt_override': prompt}}
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata', lambda items: items)
    monkeypatch.setattr('services.character_voice.refresh_character_voices', lambda items, root: items)
    from html import unescape
    html = unescape(_h3_config_html(segment))
    assert prompt in html
    assert '参考绑定不完整' not in html
