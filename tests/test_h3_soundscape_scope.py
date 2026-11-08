import pytest

from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler


@pytest.mark.parametrize('ambient', ['', '持续的室内空调底噪。'])
def test_shot_audio_keeps_its_scope_when_compiled(ambient):
    config = H3GenerationConfig.from_mapping({})
    segment = {'model_hints': {'minimax_h3_local_ref2va': {
        'core_idea': '室内人物处理文件后回应。',
        'overall_soundscape': ambient,
        'shots': [
            {'start_sec': 0, 'end_sec': 2, 'visible_roles': [],
             'action': '文件落在桌上。', 'audio': '无对白，仅有文件落桌声。'},
            {'start_sec': 2, 'end_sec': 6, 'visible_roles': [],
             'action': '镜头停留在文件上。', 'audio': '室内底噪。',
             'dialogue': {'speaker': '负责人', 'scope': 'offscreen',
                          'line': '请核对这份文件。'}},
        ],
    }}}
    prompt = H3PromptCompiler().compile(
        segment, config, H3AssetBinder().bind(config), duration_sec=6,
    ).text
    details, overall = prompt.split('【整体声音环境】', 1)
    overall = overall.split('【非叙事性音乐】', 1)[0]
    assert '声音：无对白，仅有文件落桌声。' in details
    assert '文件落桌声' not in overall
    assert prompt.count('<d>[Chinese]请核对这份文件。</d>') == 1
    if ambient:
        assert ambient in overall

