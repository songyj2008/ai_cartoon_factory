"""Wire references must preserve the complete evidence and local edit contracts."""
import copy
import json

import pytest

from services.llm_payloads import referenced_feedback


def test_feedback_references_exact_original_values_without_pruning_differences():
    dialogue = [{'speaker': 'r1', 'line': 'original ' * 30, 'scope': 'offscreen'}]
    response = {'shots': [{'dialogues': dialogue, 'action': 'original action ' * 20}]}
    sources = {'source_dialogue': dialogue, 'invalid_plan': response}
    feedback = {'expected': dialogue, 'actual': response,
                'detail': {'line': 'different ' * 30}, 'false': False, 'empty': [], 'unknown': None}
    before = copy.deepcopy((sources, feedback))
    wire = referenced_feedback(feedback, sources)
    assert wire['expected'] == {'input_ref': '#/source_dialogue'}
    assert wire['actual'] == {'input_ref': '#/invalid_plan'}
    assert wire['detail'] == feedback['detail']
    assert wire['false'] is False and wire['empty'] == [] and wire['unknown'] is None
    assert len(json.dumps(wire)) < len(json.dumps(feedback))
    assert (sources, feedback) == before


def test_feedback_pointer_escapes_keys_and_preserves_original_diagnostics():
    original = {'a/b~c': ['complete ' * 40]}
    feedback = {'expected': original['a/b~c'][0]}
    wire = referenced_feedback(feedback, original)
    assert wire == {'expected': {'input_ref': '#/a~1b~0c/0'}}
    assert feedback['expected'] == original['a/b~c'][0]


def test_director_ir_preserves_dialogue_order_speakers_and_actions():
    from prompt_builder.licon_director_compiler import compile_ir_to_llm_prompt
    line = 'Complete original dialogue with punctuation. ' * 30
    ir = {'events': {'dialogue_units': [{'speaker': 'a', 'line': line}],
                     'event_flow': [{'actor': 'a', 'dialogue': line, 'action': 'first'},
                                    {'actor': 'b', 'dialogue': line, 'action': 'second'},
                                    {'actor': 'a', 'dialogue': line, 'action': 'third'}]}}
    before = copy.deepcopy(ir)
    prompt = compile_ir_to_llm_prompt(ir)
    wire = json.loads(prompt.split('[PromptIR]\n', 1)[1])
    flow = wire['events']['event_flow']
    assert flow[0]['dialogue'] == flow[2]['dialogue'] == {'input_ref': '#/events/dialogue_units/0/line'}
    assert flow[1]['dialogue'] == line  # A different owner is never merged.
    assert [e['action'] for e in flow] == ['first', 'second', 'third']
    assert wire['events']['dialogue_units'] == ir['events']['dialogue_units']
    assert ir == before


def test_short_director_dialogue_stays_literal_without_reference_overhead():
    from prompt_builder.licon_director_compiler import compile_ir_to_llm_prompt
    ir = {'events': {'dialogue_units': [{'speaker': 'a', 'line': 'Short line.'}],
                     'event_flow': [{'actor': 'a', 'dialogue': 'Short line.'}]}}
    prompt = compile_ir_to_llm_prompt(ir)
    assert json.loads(prompt.split('[PromptIR]\n', 1)[1]) == ir
    assert 'input_ref' not in prompt


def test_composition_references_restore_all_spatial_sources(monkeypatch):
    from test_h3_scene_framing import sample
    from generation.pipelines.minimax_h3.continuity import composition, spatial_plan
    from generation.pipelines.minimax_h3.continuity.mapping import normalize_script_entities
    from generation.pipelines.minimax_h3.continuity.framing import directing_input
    base, context, _patch = sample()
    base['prompt'] = base['prompt'].replace('甲正面近景', '甲正面近景，' + '桌面与门框的固定空间关系清晰可读。' * 30, 1)
    before = copy.deepcopy((base, context))
    captured = {}
    class Captured(Exception):
        pass
    def model(system, user, **kwargs):
        captured.update(json.loads(user))
        assert 'output_contract只约束patch内部' in system
        raise Captured()
    monkeypatch.setattr('services.llm.call_llm', model)
    with pytest.raises(Captured):
        composition.plan_composition_patch(base, context)
    assert captured['editor_prompt'] == directing_input(base['prompt'])
    assert 'patch_contract' not in captured
    assert all('body' not in row and 'protected_quotes' not in row for row in captured['shots'])
    shots = composition._shots(dict(base, prompt=captured['editor_prompt']))
    restored = {}
    assert any(isinstance(ref, dict) for ref in captured['spatial_sources'].values())
    for key, ref in captured['spatial_sources'].items():
        if isinstance(ref, str):
            restored[key] = ref
            continue
        value = captured[ref['field']]
        if 'shot_number' in ref:
            value = shots[ref['shot_number'] - 1]['body']
            value = normalize_script_entities(value, base['asset_manifest'])[0]
        else:
            for part in ref['path']:
                value = value.get(part, ref.get('default'))
            if ref.get('as_string'):
                value = str(value)
        restored[key] = value
    assert restored == spatial_plan.sources(base, context)
    assert (base, context) == before
    # Compare against the previous body/quote/source duplication, retaining
    # identical contracts and facts rather than using a smaller story fixture.
    legacy = copy.deepcopy(captured)
    for row, shot in zip(legacy['shots'], composition._shots(base)):
        row.pop('body_source'); row.pop('shot_number')
        row['body'] = shot['body']
        row['protected_quotes'] = [shot['body'][a:b] for a, b in composition._protected(shot)]
    legacy['spatial_sources'] = restored
    legacy['patch_contract'] = legacy['output_contract']
    assert len(json.dumps(captured, ensure_ascii=False)) < len(json.dumps(legacy, ensure_ascii=False))
