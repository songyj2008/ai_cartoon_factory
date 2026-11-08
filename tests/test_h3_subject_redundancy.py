import pytest

from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler


def render(subject, *, with_cast=True):
    compiler = H3PromptCompiler()
    bindings = H3AssetBinder().bind({'asset_manifest': [
        {'asset_id': f'p{i}', 'type': 'image', 'path': f'{i}.png', 'role': name,
         'metadata': {'asset_kind': 'character', 'role_id': f'character_{i:03d}'}}
        for i, name in enumerate(('陈默', '王振国'), 1)
    ]})
    entries = compiler._subject_reference_entries(bindings)
    shot = {'subject': subject, 'action': '陈默看向王振国。'}
    if with_cast:
        shot['visible_roles'] = ['character_001', 'character_002']
    return compiler._shot_description(shot, subject_entries=entries, primary_entries=entries)


@pytest.mark.parametrize('subject', ['陈默', ['陈默', '王振国'], '<Subject 1>'])
def test_cast_does_not_repeat_a_bare_subject(subject):
    text = render(subject)
    assert '画面人物：<Subject 1>、<Subject 2>；<Subject 1>' not in text.replace('；<Subject 1>看向', '；动作看向')
    assert '；<Subject 1>；' not in text
    assert '；<Subject 1>、<Subject 2>；' not in text
    assert '<Subject 1>看向<Subject 2>' in text


def test_subject_description_and_castless_subject_are_kept():
    assert '<Subject 1>站在办公桌前' in render('陈默站在办公桌前')
    assert render('陈默', with_cast=False).startswith('<Subject 1>；')


def test_camera_and_blocking_are_deduplicated_before_labels_and_actions_are_kept():
    compiler = H3PromptCompiler()
    shot = {'shot_size': 'medium', 'camera': '桌侧斜拍', 'blocking': '桌侧斜拍',
            'description': '他换上红色外套。', 'prompt': '独立的必要动作。', 'action': '纸张落地。'}
    text = compiler._shot_description(shot, subject_entries=[], primary_entries=[])
    assert text.count('桌侧斜拍') == 1
    assert text.index('桌侧斜拍') < text.index('纸张落地')
    assert '他换上红色外套' in text and '独立的必要动作' in text
