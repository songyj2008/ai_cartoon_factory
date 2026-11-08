import copy
import json

import pytest

from generation.pipelines.minimax_h3 import prompt_planner


@pytest.fixture(autouse=True)
def isolated_experience_library(tmp_path, monkeypatch):
    from services import video_experience
    monkeypatch.setattr(video_experience, 'library_path', lambda: tmp_path / 'h3_experience.sqlite3')


def _plan(duration=8, dialogues=None):
    dialogues = list(dialogues or [])
    count = max(1, len(dialogues))
    shots = []
    for index in range(count):
        shot = {
            "start_sec": duration * index / count,
            "end_sec": duration * (index + 1) / count,
            "action": "人物保持当前站位，自然回应。",
        }
        if dialogues:
            shot["dialogue"] = copy.deepcopy(dialogues[index])
        shots.append(shot)
    return {
        "core_idea": "当前人物完成交流",
        "shot_mode": "one_take" if count == 1 else "multi_shot",
        "shots": shots,
    }


@pytest.mark.parametrize("source_scope, planned_scope", [
    ("画内", "onscreen"), ("画外", "offscreen"),
    ("onscreen", "画内"), ("offscreen", "画外"),
    ("diegetic", "onscreen"), ("diegetic", "offscreen"),
    ("onscreen", "diegetic"),
])
def test_h3_planner_accepts_chinese_conversation_scope_aliases(monkeypatch, source_scope, planned_scope):
    source = [{"speaker": "character_007", "scope": source_scope, "line": "请保留这句原文。"}]
    actual = [{**source[0], "scope": planned_scope}]
    _mock_model(monkeypatch, response=_plan(8, actual))
    beat = {"plot": "人物交流。", "estimated_duration_sec": 8}
    original = copy.deepcopy(source)
    plan = prompt_planner.plan_h3_prompt(beat, 1, source_dialogue=source)
    assert plan["shots"][0]["dialogue"]["line"] == source[0]["line"]
    assert source == original


@pytest.mark.parametrize("changed", ["line", "speaker", "scope"])
@pytest.mark.parametrize("source_scope", ["画内", "diegetic"])
def test_conversation_scope_aliases_do_not_hide_dialogue_changes(monkeypatch, changed, source_scope):
    source = [{"speaker": "character_007", "scope": source_scope, "line": "请保留这句原文。"}]
    actual = [{**source[0], "scope": "onscreen"}]
    actual[0][changed] = {"line": "改写后的台词。", "speaker": "character_002", "scope": "telephone"}[changed]
    _mock_model(monkeypatch, response=_plan(8, actual))
    with pytest.raises(ValueError, match="分镜对白与当前 Beat 不一致"):
        prompt_planner.plan_h3_prompt({"estimated_duration_sec": 8}, 1, source_dialogue=source)


def _mock_model(monkeypatch, *, response=None):
    calls = []

    def fake_model(system, user, **kwargs):
        payload = json.loads(user)
        calls.append({"system": system, "payload": payload, "kwargs": kwargs})
        result = response if response is not None else _plan(payload["duration_sec"])
        return copy.deepcopy(result), 0.1

    monkeypatch.setattr(prompt_planner, "_call_model", fake_model)
    return calls


def test_normal_planning_uses_single_shared_visual_contract(monkeypatch):
    from generation.pipelines.minimax_h3.rules import h3_visual_prompt_rules
    calls = _mock_model(monkeypatch)
    prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1)
    system = calls[0]['system']
    assert system.count(h3_visual_prompt_rules()) == 1
    assert '不自动复述服装' in system and '按本镜视觉主次排列' in system
    assert '不生成Subject/Picture/Audio编号' in system


def test_beat_dialogue_refs_preserve_all_six_lines_without_model_transcription(monkeypatch):
    source = [
        {'speaker': f'role_{index % 2}', 'scope': 'telephone' if index % 2 else 'onscreen',
         'line': f'第{index}句原文。'} for index in range(1, 7)
    ]
    response = _plan(10)
    response['shots'][0]['dialogue_refs'] = [1, 2, 3, 4, 5, 6]
    calls = _mock_model(monkeypatch, response=response)
    beat = {'estimated_duration_sec': 10, 'dialogue_units': copy.deepcopy(source)}
    plan = prompt_planner.plan_h3_prompt(beat, 7)
    assert plan['shots'][0]['dialogues'] == source
    assert 'dialogue_refs' not in plan['shots'][0]
    assert beat['dialogue_units'] == source
    assert len(calls) == 1
    assert calls[0]['payload']['source_dialogue'] == source
    assert 'dialogue_catalog' not in calls[0]['payload']
    assert '可按原文拆为跨镜连续片段' in calls[0]['system']
    reused = prompt_planner.plan_h3_prompt(beat, 7, cached_plan=plan)
    assert reused == plan and len(calls) == 1


@pytest.mark.parametrize('refs', [[1], [1, 1, 2], [2, 1], [0, 2], [True, 2], ['1', 2]])
def test_dialogue_refs_reject_missing_duplicate_reordered_and_invalid_assignments(monkeypatch, refs):
    response = _plan(8)
    response['shots'][0]['dialogue_refs'] = refs
    _mock_model(monkeypatch, response=response)
    with pytest.raises(prompt_planner.DialogueValidationError):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1, source_dialogue=[
            {'speaker': 'a', 'scope': 'onscreen', 'line': '第一句。'},
            {'speaker': 'b', 'scope': 'offscreen', 'line': '第二句。'},
        ])


def test_dialogue_assignment_keeps_explicit_silent_shot_and_original_repetition(monkeypatch):
    source = [{'speaker': 'a', 'scope': 'onscreen', 'line': '原文重复。'}] * 2
    response = _plan(8, [{}, {}, {}])
    for shot, refs in zip(response['shots'], [[1], [], [2]]):
        shot.pop('dialogue', None)
        shot['dialogue_refs'] = refs
    _mock_model(monkeypatch, response=response)
    plan = prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1, source_dialogue=source)
    assert plan['shots'][0]['dialogues'] == source[:1]
    assert 'dialogues' not in plan['shots'][1]
    assert plan['shots'][2]['dialogues'] == source[1:]


def test_h3_planner_uses_actual_inputs_without_mutating_or_persisting_them(monkeypatch):
    beat = {
        "id": 2,
        "plot": "林川在会议室等待答复。",
        "estimated_duration_sec": 8,
        "source_excerpt": "当前段的原文背景。",
        "model_hints": {
            "minimax_h3_local_ref2va": {
                "shots": [{"start_sec": 0, "end_sec": 8, "action": "过期镜头细节"}]
            }
        },
    }
    manifest = [{
        "id": "lin-voice", "type": "audio", "path": "lin.wav", "role": "林川",
        "metadata": {"identity_id": "lin", "duration_sec": 6.5, "transcript": ""},
    }]
    previous = {"scene_id": "meeting_room", "ending_shot": {"action": "林川仍站在桌边。"}}
    original = copy.deepcopy((beat, manifest, previous))
    calls = _mock_model(monkeypatch)

    def unexpected_save(*args, **kwargs):
        pytest.fail("pure prompt planning must not save project data")

    monkeypatch.setattr(prompt_planner, "save_json", unexpected_save)
    plan = prompt_planner.plan_h3_prompt(
        beat, 2, duration_sec=10, asset_manifest=manifest, previous_segment_context=previous,
    )

    assert (beat, manifest, previous) == original
    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert payload["duration_sec"] == 10
    assert payload["previous_segment_context"] == previous
    assert "model_hints" not in payload["current_beat"]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "过期镜头细节" not in serialized
    assert "lin-voice" in serialized and "lin.wav" in serialized
    assert plan["shots"][-1]["end_sec"] == 10
    assert isinstance(plan["planning_fingerprint"], str) and plan["planning_fingerprint"]


def test_h3_planner_reuses_matching_cached_plan_without_a_model_call(monkeypatch):
    beat = {"plot": "人物等待答复。", "estimated_duration_sec": 8}
    calls = _mock_model(monkeypatch)
    first = prompt_planner.plan_h3_prompt(beat, 1, asset_manifest=[])
    second = prompt_planner.plan_h3_prompt(beat, 1, asset_manifest=[], cached_plan=first)

    assert len(calls) == 1
    assert second == first


@pytest.mark.parametrize("changed", ["duration", "dialogue", "manifest", "previous_context", "rules"])
def test_h3_planner_invalidates_cache_when_effective_inputs_change(monkeypatch, changed):
    beat = {"plot": "人物等待答复。", "estimated_duration_sec": 8}
    manifest = [{"id": "voice", "type": "audio", "path": "one.wav", "role": "林川"}]
    previous = {"ending_shot": {"action": "人物站在桌边。"}}
    calls = _mock_model(monkeypatch)
    first = prompt_planner.plan_h3_prompt(
        beat, 1, duration_sec=8, asset_manifest=manifest, previous_segment_context=previous,
    )
    duration = 8
    if changed == "duration":
        duration = 9
    elif changed == "dialogue":
        beat["dialogue_units"] = [{"speaker": "林川", "scope": "onscreen", "line": "请回答。"}]
        # Keep the response legal so this test isolates cache invalidation.
        monkeypatch.setattr(prompt_planner, "_call_model", lambda *args, **kwargs: (
            calls.append({"changed": True}) or _plan(8, beat["dialogue_units"]), 0.1,
        ))
    elif changed == "manifest":
        manifest[0]["path"] = "two.wav"
    elif changed == "rules":
        original_rules = prompt_planner.h3_segment_planning_rules()
        monkeypatch.setattr(prompt_planner, "h3_segment_planning_rules", lambda: original_rules + "\n新规则版本")
    else:
        previous["ending_shot"]["action"] = "人物已经坐下。"

    second = prompt_planner.plan_h3_prompt(
        beat, 1, duration_sec=duration, asset_manifest=manifest,
        previous_segment_context=previous, cached_plan=first,
    )

    assert len(calls) == 2
    assert second["planning_fingerprint"] != first["planning_fingerprint"]


@pytest.mark.parametrize("shots, mode", [
    ([{"start_sec": float("nan"), "end_sec": 8}], "one_take"),
    ([{"start_sec": 0, "end_sec": float("nan")}], "one_take"),
    ([{"start_sec": 0, "end_sec": float("inf")}], "one_take"),
    ([{"start_sec": 1, "end_sec": 8}], "one_take"),
    ([{"start_sec": 0, "end_sec": 3}, {"start_sec": 4, "end_sec": 8}], "multi_shot"),
    ([{"start_sec": 0, "end_sec": 5}, {"start_sec": 4, "end_sec": 8}], "multi_shot"),
    ([{"start_sec": 0, "end_sec": 7}], "one_take"),
    ([{"start_sec": 0, "end_sec": 4}, {"start_sec": 4, "end_sec": 8}], "one_take"),
])
def test_h3_planner_rejects_invalid_structural_timing(shots, mode):
    with pytest.raises(ValueError):
        prompt_planner._validated_plan({"shots": shots, "shot_mode": mode}, 8)


def test_h3_planner_surfaces_model_time_conflict(monkeypatch):
    reason = "当前台词与必要动作无法在八秒内完成。"
    _mock_model(monkeypatch, response={"status": "needs_replan", "reason": reason})

    with pytest.raises(ValueError, match=reason):
        prompt_planner.plan_h3_prompt({"plot": "人物交流。", "estimated_duration_sec": 8}, 1)


@pytest.mark.parametrize("defect", ["added", "omitted", "duplicated", "wrong_speaker", "reordered"])
def test_h3_planner_rejects_dialogue_identity_or_order_changes(monkeypatch, defect):
    expected = [
        {"speaker": "林川", "scope": "onscreen", "line": "请把事情说清楚。"},
        {"speaker": "张青", "scope": "onscreen", "line": "我现在解释。"},
    ]
    actual = copy.deepcopy(expected)
    if defect == "added":
        actual.append({"speaker": "林川", "scope": "onscreen", "line": "我已经决定了。"})
    elif defect == "omitted":
        actual.pop()
    elif defect == "duplicated":
        actual.insert(1, copy.deepcopy(actual[0]))
    elif defect == "wrong_speaker":
        actual[0]["speaker"] = "张青"
    else:
        actual.reverse()
    _mock_model(monkeypatch, response=_plan(8, actual))

    with pytest.raises(ValueError):
        prompt_planner.plan_h3_prompt({
            "plot": "林川与张青交流。", "estimated_duration_sec": 8, "dialogue_units": expected,
        }, 1)


def test_h3_planner_allows_unchanged_dialogue_split_across_shots(monkeypatch):
    expected = [{"speaker": "林川", "scope": "onscreen", "line": "你先坐下，听我解释。"}]
    split = [
        {"speaker": "林川", "scope": "onscreen", "line": "你先坐下，"},
        {"speaker": "林川", "scope": "onscreen", "line": "听我解释。", "continues_previous": True},
    ]
    _mock_model(monkeypatch, response=_plan(8, split))

    plan = prompt_planner.plan_h3_prompt({
        "plot": "林川继续解释。", "estimated_duration_sec": 8, "dialogue_units": expected,
    }, 1)

    assert len(plan["shots"]) == 2
    assert "".join(shot["dialogue"]["line"] for shot in plan["shots"]) == expected[0]["line"]


def test_h3_planner_preserves_explicit_source_dialogue_when_beat_has_no_units(monkeypatch):
    explicit = [{"speaker": "林川", "scope": "onscreen", "line": "请把事情说清楚。"}]
    _mock_model(monkeypatch, response=_plan(8, explicit))
    beat = {"plot": "林川要求解释。", "estimated_duration_sec": 8}
    plan = prompt_planner.plan_h3_prompt(beat, 1, source_dialogue=explicit)

    assert plan["shots"][0]["dialogue"] == explicit[0]

    _mock_model(monkeypatch, response=_plan(8))
    with pytest.raises(ValueError):
        prompt_planner.plan_h3_prompt(beat, 1, source_dialogue=explicit)


def test_h3_prompt_regenerator_excludes_old_hints_and_persists_fresh_plan(monkeypatch):
    beats = {
        "beats": [
            {
                "title": "跳船降落",
                "plot": "金色熊猫在蓝色泡泡，糖宝宝在绿色泡泡，禾宝宝在粉色泡泡。",
                "estimated_duration_sec": 15,
                "model_hints": {
                    "minimax_h3_local_ref2va": {
                        "shots": [{"start_sec": 0, "end_sec": 15, "action": "旧的三个金色泡泡"}]
                    }
                },
            }
        ]
    }
    captured = {}
    saved = {}

    def fake_llm(system, user, **kwargs):
        captured["payload"] = json.loads(user)
        return {
            "core_idea": "三个不同颜色的泡泡救生舱降落",
            "shot_mode": "multi_shot",
            "shots": [
                {"start_sec": 0, "end_sec": 5, "action": "金色熊猫进入蓝色泡泡"},
                {"start_sec": 5, "end_sec": 10, "action": "糖宝宝进入绿色泡泡"},
                {"start_sec": 10, "end_sec": 15, "action": "禾宝宝乘粉色泡泡落入山林"},
            ],
        }, 0.1

    monkeypatch.setattr(prompt_planner, "_call_model", fake_llm)
    monkeypatch.setattr(prompt_planner, "save_json", lambda name, value: saved.update(name=name, value=value))

    plan = prompt_planner.regenerate_h3_prompt_plan(beats, 1)

    assert "model_hints" not in captured["payload"]["current_beat"]
    assert "三个金色泡泡" not in json.dumps(captured["payload"], ensure_ascii=False)
    assert plan["shots"][0]["action"] == "金色熊猫进入蓝色泡泡"
    assert saved["name"] == "beats.json"
    assert beats["beats"][0]["model_hints"]["minimax_h3_local_ref2va"] == plan


@pytest.mark.parametrize('chosen', [4, 7, 12, 15])
def test_dialogue_first_auto_duration_uses_model_time_and_preserves_dialogue(monkeypatch, chosen):
    dialogue = [{'speaker': '林川', 'scope': 'onscreen', 'line': '请把事情说明白。'}]
    response = {**_plan(chosen, dialogue), 'duration_sec': chosen, 'duration_reason': '按对白自然说完，手势与说话并行。'}
    calls = _mock_model(monkeypatch, response=response)
    beat = {'estimated_duration_sec': 8, 'plot': '林川要求解释。', 'dialogue_units': dialogue}
    first = prompt_planner.plan_h3_prompt(beat, 1, auto_duration=True)
    second = prompt_planner.plan_h3_prompt(beat, 1, auto_duration=True, cached_plan=first)
    assert first == second and len(calls) == 1
    assert first['duration_sec'] == chosen
    assert first['shots'][-1]['end_sec'] == chosen
    assert calls[0]['payload']['duration_policy']['reference_duration_sec'] == 8
    assert first['shots'][0]['dialogue'] == dialogue[0]
    assert beat['estimated_duration_sec'] == 8


@pytest.mark.parametrize('scope', ['onscreen', 'offscreen', 'telephone', 'narration'])
def test_auto_duration_preserves_speech_scope_and_silent_shot(monkeypatch, scope):
    dialogue = {'speaker': 'character_001', 'scope': scope, 'line': '已经确认，继续。'}
    shots = [
        {'start_sec': 0, 'end_sec': 3.5, 'subject': 'character_002',
         'action': '人物注视前方，保持原位。', 'dialogue': copy.deepcopy(dialogue)},
        {'start_sec': 3.5, 'end_sec': 6, 'subject': 'character_002',
         'action': '人物收起物件，表情放松。', 'audio': '只有环境音和轻微衣料摩擦声。'},
    ]
    response = {'core_idea': '人物收到消息后完成动作', 'shot_mode': 'multi_shot',
                'shots': shots, 'duration_sec': 6, 'duration_reason': '首镜按台词估时，末镜按必要动作完成估时。'}
    calls = _mock_model(monkeypatch, response=response)
    beat = {'estimated_duration_sec': 8, 'dialogue_units': [dialogue]}
    before = copy.deepcopy(beat)
    plan = prompt_planner.plan_h3_prompt(beat, 1, auto_duration=True)
    assert plan['duration_sec'] == 6
    assert plan['shots'] == shots
    assert 'dialogue' not in plan['shots'][1]
    assert beat == before
    assert calls[0]['payload']['current_beat']['dialogue_units_ref'] == '#/source_dialogue'
    assert calls[0]['payload']['source_dialogue'] == [dialogue]


@pytest.mark.parametrize('scope', ['onscreen', 'offscreen', 'telephone', 'narration'])
def test_beat_segment_speech_rules_survive_compile_and_editor_read(monkeypatch, scope):
    from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig, H3_REF2VA_MODEL_ID
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt

    source = {'speaker': '林川', 'scope': scope, 'line': '请先保留下单支付。'}
    response = _plan(6)
    response['shot_mode'] = 'multi_shot'
    response['shots'] = [
        {'start_sec': 0, 'end_sec': 2.8, 'subject': '张青', 'dialogue_refs': [],
         'action': '本镜无对白，张青不做说话口型，走到桌边。', 'audio': '脚步声与设备低鸣。'},
        {'start_sec': 2.8, 'end_sec': 6, 'subject': '林川、张青' if scope == 'onscreen' else '张青',
         'dialogue_refs': [1], 'action': '张青闭口倾听，不代说、不做说话口型。',
         'audio': '林川语气沉稳，设备低鸣。'},
    ]
    calls = _mock_model(monkeypatch, response=response)
    beat = {'estimated_duration_sec': 6, 'dialogue_units': [source]}
    before = copy.deepcopy(beat)
    plan = prompt_planner.plan_h3_prompt(beat, 1)
    assert '听者镜头有画外续说时保留实际发声记录' in calls[0]['system']
    assert '实际入画、画外发声和被提及相区分' in calls[0]['system']
    config = H3GenerationConfig.from_mapping({'asset_manifest': []})
    compiled = H3PromptCompiler().compile(
        {**beat, 'model_hints': {H3_REF2VA_MODEL_ID: plan}}, config,
        H3AssetBinder().bind(config), duration_sec=6,
    ).text
    read_back = parse_prompt(compiled, 'segment_test')
    silent, speaking = read_back['shots']
    assert silent['dialogues'] == []
    assert '本镜无对白' in silent['body']
    assert [(s['original_start_cs'], s['original_end_cs']) for s in read_back['shots']] == [(0, 280), (280, 600)]
    assert [(d['scope'], d['line']) for d in speaking['dialogues']] == [(scope, source['line'])]
    assert '闭口倾听' in speaking['body']
    assert beat == before


@pytest.mark.parametrize('chosen', [None, True, 3, 16, 9.5])
def test_auto_duration_rejects_missing_or_out_of_range_time(monkeypatch, chosen):
    _mock_model(monkeypatch, response={**_plan(8), 'duration_sec': chosen, 'duration_reason': '对白需要'})
    with pytest.raises(ValueError):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'plot': '人物交流。'}, 1, auto_duration=True)


def test_auto_duration_surfaces_required_split_instead_of_forcing_fifteen_seconds(monkeypatch):
    _mock_model(monkeypatch, response={'status': 'needs_replan', 'reason': '原台词超过十五秒，需要上游拆段。'})
    with pytest.raises(ValueError, match='需要上游拆段'):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'plot': '人物交流。'}, 1, auto_duration=True)


def test_editor_prompt_supersedes_old_beat_roles_and_dialogue(monkeypatch):
    dialogue = [{'speaker': '<Subject 1>', 'line': '这是修改后的台词。'}]
    calls = _mock_model(monkeypatch, response={**_plan(10, dialogue), 'duration_sec': 10,
                                               'duration_reason': '按编辑稿对白自然说完。'})
    beat = {'estimated_duration_sec': 8, 'plot': '旧人物离开。', 'visible_roles': ['旧人物'],
            'dialogue_units': [{'speaker': '旧人物', 'line': '已经删除的旧台词。'}]}
    editor = '<Subject 1> 新人物。\n<Subject 1> (S1)说：<d>[Chinese]这是修改后的台词。</d>'
    plan = prompt_planner.plan_h3_prompt(beat, 1, editor_prompt=editor, auto_duration=True,
        previous_segment_context={'plot': '旧人物继续发言。'})
    payload = calls[0]['payload']
    assert payload['current_beat'] == {} and payload['source_dialogue'] == []
    assert payload['previous_segment_context'] is None
    assert '旧人物' not in json.dumps(payload, ensure_ascii=False)
    assert payload['editor_prompt'] == editor
    assert plan['duration_sec'] == 10


@pytest.mark.parametrize('draft_mode', [False, True])
@pytest.mark.parametrize('speaker, line, scope, valid', [
    ('陈默', '请核对文件。', 'onscreen', True),
    ('王振国', '请核对文件。', 'onscreen', False),
    ('陈默', '请修改文件。', 'onscreen', False),
    ('陈默', '请核对文件。', 'telephone', False),
])
def test_editor_defined_name_and_subject_share_identity(monkeypatch, draft_mode, speaker, line, scope, valid):
    editor = ('【主体定义】\n<Subject 1>：陈默；可见内容来自 <Picture 1>。\n'
              '<Subject 2>：王振国；可见内容来自 <Picture 2>。\n'
              '【详细镜头描述】\n<Subject 1> (S1)说：<d>[Chinese]请核对文件。</d>')
    response = _plan(8, [{'speaker': speaker, 'scope': scope, 'line': line}])
    original = copy.deepcopy(response)
    _mock_model(monkeypatch, response=response)
    kwargs = dict(editor_prompt=editor, draft_mode=draft_mode, force_replan=True,
                  dialogue_contract=[{'speaker': '<Subject 1>', 'scope': 'onscreen', 'line': '请核对文件。'}])
    if not valid and not draft_mode:
        with pytest.raises(ValueError, match='对白'):
            prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1, **kwargs)
    else:
        plan = prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1, **kwargs)
        if draft_mode:
            mismatches = [i for i in plan['planning_issues'] if i['code'] == 'dialogue_mismatch']
            assert bool(mismatches) == (not valid)
        assert plan['shots'][0]['dialogue']['speaker'] == speaker
    assert response == original


@pytest.mark.parametrize('padding', [' ', '\n', '\t '])
def test_language_tag_padding_does_not_trigger_dialogue_repair(monkeypatch, padding):
    line = '请核对 OA 文件。'
    editor = '<Subject 1>说：<d>[Chinese]' + padding + line + '</d>'
    calls = _mock_model(monkeypatch, response=_plan(8, [{'speaker': '<Subject 1>', 'line': line}]))
    plan = prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1,
                                       editor_prompt=editor, force_replan=True)
    assert plan['shots'][0]['dialogue']['line'] == line
    assert len(calls) == 1


def test_editor_duplicate_names_are_not_resolved_by_guessing():
    text = '【主体定义】\n<Subject 1>：陈默；图1\n<Subject 2>：陈默；图2\n【任务概述】\n事件'
    assert prompt_planner._editor_speaker_aliases(text) == {}


@pytest.mark.parametrize('bad_shape', ['top_level', 'missing', 'facts_list'])
@pytest.mark.parametrize('repair_valid', [True, False])
def test_continuity_structure_uses_one_existing_repair_attempt(monkeypatch,bad_shape,repair_valid):
    valid={**_plan(8),'scene_relation':'immediate'}
    valid['shots'][0].update(visible_roles=['a'],world_events=[])
    invalid=copy.deepcopy(valid)
    if bad_shape=='top_level':
        invalid['world_events']=invalid['shots'][0].pop('world_events')
    elif bad_shape=='missing':
        invalid['shots'][0].pop('world_events')
    else:
        invalid['shots'][0]['world_events']=[{'role_id':'a','facts':[]}]
    calls=[]
    def model(system,user,**kwargs):
        calls.append(json.loads(user))
        return copy.deepcopy(valid if len(calls)==2 and repair_valid else invalid),0
    monkeypatch.setattr(prompt_planner,'_call_model',model)
    kwargs={'continuity_context':{'tracked_role_ids':['a'],'input_state':{'a':{}}},
            'fixed_shots':[{'shot_uid':'locked','start_sec':0,'end_sec':8}],'force_replan':True}
    if repair_valid:
        result=prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,**kwargs)
        assert result['shots'][0]['world_events']==[]
        assert result['shots'][0]['shot_uid']=='locked'
    else:
        with pytest.raises(ValueError,match='自动修正一次后仍未通过'):
            prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,**kwargs)
    assert len(calls)==2
    assert calls[1]['validation_feedback']['code']=='INVALID_PLAN'
    assert calls[1]['invalid_plan']==invalid


@pytest.mark.parametrize('changed', ['line', 'speaker'])
def test_editor_tagged_dialogue_is_validated_against_editor_instead_of_beat(monkeypatch, changed):
    line = '新台词。' if changed == 'speaker' else '旧台词。'
    speaker = '<Subject 2>' if changed == 'speaker' else '<Subject 1>'
    _mock_model(monkeypatch, response=_plan(8, [{'speaker': speaker, 'line': line}]))
    with pytest.raises(ValueError, match='编辑区'):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 1,
                                     editor_prompt='<Subject 1>说：<d>[Chinese]新台词。</d>')


def test_fixed_continuity_compacts_request_and_repair_without_mutating_sources(monkeypatch):
    from generation.pipelines.minimax_h3.continuity.mapping import model_references
    world={'a':{'position':{'value':'桌前','source_quote':'历史剧情','origin':{'source_quote':'历史剧情'}}}}
    ctx={'tracked_role_ids':['a'],'input_state':world,'role_aliases':{'甲':'a'}}
    assets=[{'asset_id':'long-private-id','type':'image','path':'private.png','role':'甲',
             'metadata':{'asset_kind':'character','role_id':'a','is_first_frame':True}}]
    original=copy.deepcopy((ctx,assets))
    valid={**_plan(8),'scene_relation':'immediate'}
    valid['shots'][0].update(world_events=[],visible_roles=['a'])
    bad=copy.deepcopy(valid);bad['shots'][0].pop('world_events')
    calls=[]
    def model(system,user,**kwargs):
        calls.append((system,json.loads(user)))
        return copy.deepcopy(bad if len(calls)==1 else valid),0
    monkeypatch.setattr(prompt_planner,'_call_model',model)
    monkeypatch.setattr('services.speech_timing.planning_context',lambda *a:pytest.fail('fixed timing must not read statistics'))
    monkeypatch.setattr('services.video_experience.retrieve',lambda *a,**k:pytest.fail('locked composition must not retrieve historical samples'))
    result=prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,
        asset_manifest=assets,continuity_context=ctx,fixed_shots=[{'shot_uid':'s','start_sec':0,'end_sec':8,'body':'重复正文'}],force_replan=True)
    assert len(calls)==2 and result['shots'][0]['shot_uid']=='s'
    from generation.pipelines.minimax_h3.rules import h3_visual_prompt_rules
    assert h3_visual_prompt_rules() in calls[0][0]
    assert '每段 H3 总时长必须' not in calls[0][0]
    for _,payload in calls:
        text=json.dumps(payload,ensure_ascii=False)
        for forbidden in ('approved_prompt_experiences','speech_timing_context','旧剧情全文','历史剧情','private.png','long-private-id','role_aliases','重复正文'):
            assert forbidden not in text
        assert payload['references'][0]['anchor']=='首帧锚点'
    assert calls[1][1]['validation_feedback']['code']=='INVALID_PLAN'
    assert 'actual' not in calls[1][1]['validation_feedback']
    assert (ctx,assets)==original


def test_auto_timing_with_continuity_still_reads_statistics(monkeypatch):
    calls=[]
    monkeypatch.setattr('services.speech_timing.planning_context',lambda *a:calls.append(True) or {})
    response={**_plan(8),'duration_sec':8,'duration_reason':'对白自然说完','scene_relation':'immediate'}
    response['shots'][0]['world_events']=[]
    _mock_model(monkeypatch,response=response)
    prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,auto_duration=True,
        continuity_context={'tracked_role_ids':['a'],'input_state':{'a':{}}},force_replan=True)
    assert calls==[True]


@pytest.mark.parametrize('has_initial',[True,False])
def test_pending_scene_initializes_in_same_single_call(monkeypatch,has_initial):
    response={**_plan(8),'scene_relation':'immediate'}
    response['shots'][0].update(world_events=[],visible_roles=['a'])
    if has_initial:response['initial_state']={'a':{'position':{'value':'桌前'},'posture':{'value':'站着'},'present':{'value':True}}}
    calls=_mock_model(monkeypatch,response=response)
    kwargs=dict(continuity_context={'tracked_role_ids':['a'],'input_state':{'a':{}},'initializing':True},
                fixed_shots=[{'shot_uid':'s','start_sec':0,'end_sec':8}],max_model_calls=1,force_replan=True)
    if has_initial:
        result=prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,**kwargs)
        assert result['initial_state']['a']['position']['value']=='桌前'
    else:
        with pytest.raises(ValueError,match='initial_state'):
            prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,**kwargs)
    assert len(calls)==1
    assert calls[0]['payload']['continuity_context']['initializing'] is True
    assert 'input_state' not in calls[0]['payload']['continuity_context']
    assert 'known_opening_facts' in calls[0]['payload']['continuity_context']


@pytest.mark.parametrize('bad_time',[False,True])
def test_fixed_shot_mode_is_local_and_single_call_error_is_truthful(monkeypatch,bad_time):
    response={**_plan(8),'scene_relation':'immediate','shot_mode':'fixed'}
    response['shots'][0].update(world_events=[],visible_roles=['a'])
    if bad_time:response['shots'][0]['end_sec']=7
    calls=_mock_model(monkeypatch,response=response)
    args=dict(continuity_context={'tracked_role_ids':['a'],'input_state':{'a':{}}},
              fixed_shots=[{'shot_uid':'locked','start_sec':0,'end_sec':8}],max_model_calls=1,force_replan=True)
    if bad_time:
        with pytest.raises(ValueError,match='本次仅调用一次，未自动重试.*结束于 7 秒，本段要求 8 秒'):
            prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,**args)
    else:
        plan=prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,**args)
        assert plan['shot_mode']=='one_take'
        assert response['shot_mode']=='fixed'
    assert len(calls)==1



def test_initial_state_table_cannot_replace_required_shots(monkeypatch):
    response={'scene_relation':'immediate','shots':[],'initial_state':{'a':{'present':{'value':True}}}}
    calls=_mock_model(monkeypatch,response=response)
    with pytest.raises(ValueError,match='需要1个镜头，模型实际返回0个'):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},1,force_replan=True,max_model_calls=1,
            continuity_context={'tracked_role_ids':['a'],'input_state':{'a':{}},'initializing':True},
            fixed_shots=[{'shot_uid':'s','start_sec':0,'end_sec':8}])
    assert len(calls)==1
    contract=calls[0]['payload']['output_contract']
    assert contract['shot_count']==1 and 'initial_state' in contract['top_level_fields']
    assert 'shots' in contract['top_level_fields']


@pytest.mark.parametrize('reason',['对白镜头可用时长不足',''])
def test_fixed_continuity_surfaces_needs_replan_before_missing_shots(monkeypatch,reason):
    calls=_mock_model(monkeypatch,response={'status':'needs_replan','reason':reason})
    with pytest.raises(ValueError) as error:
        prompt_planner.plan_h3_prompt({'estimated_duration_sec':8},2,force_replan=True,max_model_calls=1,
            continuity_context={'tracked_role_ids':['a'],'input_state':{'a':{}}},
            fixed_shots=[{'shot_uid':'s','start_sec':0,'end_sec':8}])
    message=str(error.value)
    assert '第 2 段' in message
    assert '分镜规划需要调整：'+(reason or '当前时长无法完成当前事件') in message
    assert '完整分镜' not in message and '人物状态表' not in message
    assert len(calls)==1


def test_composition_preserves_long_dialogue_without_timing_assessment(monkeypatch):
    line='相关材料已经全部核对完毕，交接记录也已归档，请按照既定流程联系负责人员处理。'
    response={**_plan(6),'scene_relation':'immediate'}
    response['shots']=[
        {'start_sec':0,'end_sec':1.8,'action':'站在桌前','visible_roles':['a'],'world_events':[]},
        {'start_sec':1.8,'end_sec':6,'action':'原位发言','visible_roles':['a'],'world_events':[],
         'dialogue':[{'speaker':'a','scope':'onscreen','line':line}]}]
    calls=_mock_model(monkeypatch,response=response)
    monkeypatch.setattr('services.speech_timing.planning_context',lambda *a:pytest.fail('composition must not assess speech timing'))
    plan=prompt_planner.plan_h3_prompt({'estimated_duration_sec':6},2,
        editor_prompt='[Shot 1]（0–1.8 秒）：无对白。\n[Shot 2]（1.8–6 秒）：a说：<d>[Chinese]'+line+'</d>',
        dialogue_contract=[{'speaker':'a','scope':'onscreen','line':line}],
        continuity_context={'tracked_role_ids':['a'],'input_state':{'a':{}}},
        fixed_shots=[{'shot_uid':'s1','start_sec':0,'end_sec':1.8},{'shot_uid':'s2','start_sec':1.8,'end_sec':6}],
        force_replan=True,max_model_calls=1)
    assert len(calls)==1
    assert plan['shots'][1]['dialogue'][0]['line']==line
    assert [(s['start_sec'],s['end_sec']) for s in plan['shots']]==[(0,1.8),(1.8,6)]
    system=calls[0]['system']
    assert '不评估台词能否在给定时间内说完' in system
    assert '无法执行则返回status=needs_replan' not in system
    assert '不能自然容纳时返回' not in system
