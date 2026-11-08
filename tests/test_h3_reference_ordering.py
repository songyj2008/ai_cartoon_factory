import copy

import pytest

from generation.pipelines.minimax_h3.auto_binder import order_automatic_references
from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler


def image(role, order, **metadata):
    return {'asset_id': role, 'type': 'image', 'path': role + '.png', 'role': role,
            'order': order, 'metadata': {'asset_kind': 'character', 'role_id': role,
                                       'identity_id': 'shared-identity', **metadata}}


def names(manifest):
    return [a.asset_id for a in H3GenerationConfig.from_mapping({'asset_manifest': manifest}).images]


def test_final_speakers_precede_visual_cast_and_background_without_changing_assets():
    assets = [image('a', 1), image('b', 2), image('c', 3),
              image('room', 4, asset_kind='background')]
    shots = [{'visible_roles': ['a', 'b', 'c'], 'dialogues': [
        {'speaker': 'c', 'line': '先说。'}, {'speaker': 'b', 'line': '回答。'}]}]
    before = copy.deepcopy(assets)
    result = order_automatic_references(assets, shots)
    assert names(result) == ['c', 'b', 'a', 'room']
    assert assets == before
    assert order_automatic_references(result, shots) == result
    for a in result:
        old = next(v for v in before if v['asset_id'] == a['asset_id'])
        assert {k: v for k, v in a.items() if k != 'order'} == {k: v for k, v in old.items() if k != 'order'}


@pytest.mark.parametrize('unit', [
    {'speaker': 'b', 'line': ''},
    {'speaker': 'b', 'line': '声音', 'scope': 'offscreen'},
    {'speaker': 'b', 'line': '声音', 'scope': 'telephone'},
    {'speaker': 'b', 'line': '声音', 'scope': 'narration'},
    {'speaker': 'b', 'line': '声音', 'source_type': 'broadcast'},
    {'speaker': 'b', 'line': '声音', 'delivery_spans': [{'range': [0, 2], 'visibility': 'offscreen'}]},
])
def test_empty_or_external_speech_does_not_promote_picture(unit):
    assets = [image('b', 1), image('a', 2)]
    assert names(order_automatic_references(assets, [
        {'visible_roles': ['a', 'b'], 'dialogue': unit}])) == ['a', 'b']


def test_mixed_delivery_promotes_visible_speaker_but_never_adds_images():
    unit = {'speaker': 'b', 'scope': 'offscreen', 'line': '声音', 'delivery_spans': [
        {'range': [0, 1], 'visibility': 'offscreen'}, {'range': [1, 2], 'visibility': 'onscreen'}]}
    shots = [{'visible_roles': ['a', 'b'], 'dialogue': unit}]
    assert names(order_automatic_references([image('a', 1), image('b', 2)], shots)) == ['b', 'a']
    assert names(order_automatic_references([image('a', 1)], shots)) == ['a']


def test_explicit_order_anchor_slots_variants_and_video_audio_are_preserved():
    video = {'asset_id': 'video', 'type': 'video', 'path': 'v.mp4', 'order': 7, 'use_video_audio': True}
    audio = {'asset_id': 'audio', 'type': 'audio', 'path': 'v.wav', 'order': 8}
    assets = [image('room', 4, asset_kind='background'), video, image('a', 1),
              image('frame', 2, picture_role='first_frame'), image('b', 3), audio,
              {**image('b', 5), 'asset_id': 'b-variant', 'path': 'other.png'}]
    result = order_automatic_references(assets, [{'visible_roles': ['a', 'b'],
                                                'dialogue': {'speaker': 'b', 'line': '答复'}}])
    assert names(result) == ['b', 'frame', 'b-variant', 'a', 'room']
    assert [a for a in result if a['type'] != 'image'] == [video, audio]
    bindings = H3AssetBinder().bind({'asset_manifest': result})
    assert bindings.videos[0].paired_audio_tag == '<Audio 1>'
    assert bindings.audios[0].prompt_tag == '<Audio 2>'


def test_sorted_picture_subject_workflow_slot_and_voice_refer_to_same_role():
    assets = [image('a', 1, identity_id='identity-a'), image('b', 2, identity_id='identity-b'),
              {'asset_id': 'voice-b', 'type': 'audio', 'path': 'b.wav',
               'metadata': {'voice_only': True, 'identity_id': 'identity-b', 'role_id': 'b'}}]
    shots = [{'start_sec': 0, 'end_sec': 4, 'visible_roles': ['a', 'b'],
              'dialogue': {'speaker': 'b', 'line': '请回答。'}}]
    config = H3GenerationConfig.from_mapping({'asset_manifest': order_automatic_references(assets, shots)})
    bindings = H3AssetBinder().bind(config)
    assert bindings.images[0].asset.asset_id == 'b'
    assert bindings.images[0].prompt_tag == '<Picture 1>'
    assert bindings.images[0].workflow_input == 'ref_images.ref_image_0'
    prompt = H3PromptCompiler().compile({'shots': shots}, config, bindings, duration_sec=4).text
    assert '<Subject 1>：b；可见内容来自 <Picture 1>' in prompt
    assert '<Audio 1> → <Subject 1>（S1）' in prompt
    assert '<Subject 1> (S1)说：<d>[Chinese]请回答。</d>' in prompt
