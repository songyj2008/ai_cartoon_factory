import copy
import json
import pytest

from services.llm_payloads import (BEAT_FIELDS, STORY_FIELDS, project_bible_context,
                                   reference_context, request_metrics, select_fields)


def test_bible_projection_keeps_creative_facts_and_local_binding_unchanged():
    bible = {'core_characters': [{'id': 'r1', 'aliases': ['A'], 'locked': True,
                                 'asset_identity_id': 'file1', 'need_reference_image': True,
                                 'custom_backstory': 'Keep this fact.'}],
             'allowed_backgrounds': [{'id': 's1', 'asset_background_id': 'file2'}],
             'extras_policy': {'can_speak': False}, 'custom_world': {'unknown': None}}
    before = copy.deepcopy(bible)
    value = project_bible_context(bible)
    assert value['core_characters'] == [{'id': 'r1', 'aliases': ['A'], 'custom_backstory': 'Keep this fact.'}]
    assert value['allowed_backgrounds'] == [{'id': 's1'}]
    assert value['extras_policy']['can_speak'] is False
    assert value['custom_world']['unknown'] is None
    value['core_characters'][0]['aliases'].append('B')
    assert bible == before


def test_reference_projection_keeps_semantics_and_never_changes_selected_files():
    assets = [{'asset_id': 'a1', 'type': 'audio', 'path': 'private/voice.wav',
               'upload_status': 'done', 'use_video_audio': False,
               'metadata': {'role_id': 'r1', 'transcript': 'Original dialogue.',
                            'voice_only': True, 'path': 'private/voice.wav',
                            'custom_usage': 'Retain the accent.'}}]
    before = copy.deepcopy(assets)
    value = reference_context(assets)
    assert 'path' not in value[0] and 'upload_status' not in value[0]
    assert 'path' not in value[0]['metadata']
    assert value[0]['metadata']['transcript'] == 'Original dialogue.'
    assert value[0]['metadata']['custom_usage'] == 'Retain the accent.'
    assert value[0]['use_video_audio'] is False
    assert assets == before


def test_task_fields_do_not_leak_future_management_fields_or_prune_silence():
    source = {'story': 'Complete text', 'dialogue_units': [], 'dialogue_authoritative': True,
              'source_excerpt': 'Complete excerpt', 'future_management_field': 'private'}
    assert select_fields(source, STORY_FIELDS) == {'story': 'Complete text'}
    beat = select_fields(source, BEAT_FIELDS)
    assert beat['dialogue_units'] == [] and beat['dialogue_authoritative'] is True
    assert beat['source_excerpt'] == 'Complete excerpt'
    assert 'future_management_field' not in beat


def test_metrics_measure_transport_representation_without_content():
    text = '\u4e2d\u6587'
    assert request_metrics(text) == {'schema_version': 1, 'chars': 2, 'utf8_bytes': 6}
    messages = [{'role': 'user', 'content': text}]
    assert request_metrics(messages)['utf8_bytes'] == len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))


def test_beats_serialization_projects_bible_without_losing_local_bindings(monkeypatch, tmp_path):
    from scripts.story_script import generate_beats as beats
    payload = {'story': 'Complete story', 'project_bible': {
        'core_characters': [{'id': 'r1', 'asset_identity_id': 'local-file', 'aliases': ['A']}]}}
    before = copy.deepcopy(payload)
    calls = []
    monkeypatch.setattr(beats, '_call_deepseek_messages', lambda config, messages, *args, **kwargs: calls.append(messages) or {})
    beats.call_deepseek({}, payload, tmp_path / 'unused.json', tmp_path)
    sent = json.loads(calls[0][-1]['content'])
    assert sent['project_bible']['core_characters'] == [{'id': 'r1', 'aliases': ['A']}]
    assert payload == before


def test_planner_projects_transport_only_and_keeps_authoritative_silence(monkeypatch):
    from generation.pipelines.minimax_h3 import prompt_planner, planning_checkpoints
    from services import speech_timing, video_experience, project_bible
    bible = {'core_characters': [{'id': 'r1', 'asset_identity_id': 'private-file'}]}
    assets = [{'id': 'a1', 'type': 'image', 'path': 'private.png', 'metadata': {'role_id': 'r1'}}]
    before = copy.deepcopy((bible, assets))
    monkeypatch.setattr(project_bible, 'load_project_bible', lambda: bible)
    monkeypatch.setattr(speech_timing, 'planning_context', lambda assets: {})
    monkeypatch.setattr(video_experience, 'retrieve', lambda *args, **kwargs: [])
    monkeypatch.setattr(planning_checkpoints, 'load_plan', lambda key: None)
    class Captured(Exception):
        pass
    def model(system, user, **kwargs):
        data = json.loads(user)
        assert data['project_bible']['core_characters'] == [{'id': 'r1'}]
        assert 'path' not in data['asset_manifest'][0]
        assert data['source_dialogue'] == []
        assert data['dialogue_authoritative'] is True
        raise Captured()
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    with pytest.raises(Captured):
        prompt_planner.plan_h3_prompt({'binding_schema_version': 1, 'estimated_duration_sec': 8,
                                      'dialogue_authoritative': True, 'dialogue_units': []},
                                     1, asset_manifest=assets, force_replan=True)
    assert (bible, assets) == before
