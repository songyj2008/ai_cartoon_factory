import copy
import json
from types import SimpleNamespace

import pytest

from generation.pipelines.minimax_h3 import prompt_planner
from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig, H3_REF2VA_MODEL_ID
from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler


@pytest.fixture
def planning_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.asset_matcher.match_beat_assets", lambda beat, **kwargs: beat)
    monkeypatch.setattr("generation.pipelines.minimax_h3.auto_binder.auto_bind_asset_manifest", lambda beat: [])
    monkeypatch.setattr("generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata", lambda values: values)
    monkeypatch.setattr("services.character_voice.attach_character_voices", lambda values, *args: values)
    monkeypatch.setattr("generation.pipelines.minimax_h3.prompt_compiler.load_project_bible", lambda: {})
    pipeline = MiniMaxH3Ref2VAPipeline()
    pipeline._config = {"video_submit_backend": "runninghub"}
    monkeypatch.setattr(pipeline, "_validate_source_assets", lambda *args, **kwargs: None)
    state = SimpleNamespace(pipeline=pipeline, calls=[], workflows=[], response=None)

    def fake_model(system, user, **kwargs):
        payload = json.loads(user)
        state.calls.append(payload)
        if state.response is not None:
            return copy.deepcopy(state.response), 0
        shot = {
            "world_events": [],
            "start_sec": 0,
            "end_sec": payload["duration_sec"],
            "subject": "林川",
            "action": "林川收住笑意，将手轻放在桌边。",
            "blocking": "林川站在桌边，视线落在对面的人物。",
            "camera": "侧面中景，缓慢靠近。",
        }
        dialogues = payload.get("source_dialogue") or payload["current_beat"].get("dialogue_units") or []
        if dialogues:
            shot["dialogues"] = copy.deepcopy(dialogues)
        return {
            "scene_relation": "immediate",
            "core_idea": "林川克制地回应当前事件",
            "shot_mode": "one_take",
            "shots": [shot],
        }, 0

    def save_workflow(workflow, part_id):
        path = tmp_path / f"{part_id}.json"
        path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")
        state.workflows.append(path)
        return path

    monkeypatch.setattr(prompt_planner, "_call_model", fake_model)
    monkeypatch.setattr(pipeline, "_save_workflow", save_workflow)
    return state


def _beat():
    return {
        "id": 1,
        "title": "等待答复",
        "plot": "林川站在会议桌边，等待对方答复。",
        "estimated_duration_sec": 8,
        "model_hints": {
            H3_REF2VA_MODEL_ID: {
                "shot_mode": "one_take",
                "shots": [{"start_sec": 0, "end_sec": 8, "action": "旧的准备动作"}],
            }
        },
    }


def test_failed_batch_keeps_validated_draft_plans_until_resume(planning_pipeline, tmp_path, monkeypatch):
    from services import context, text_jobs

    state = planning_pipeline
    work = tmp_path / 'work'
    work.mkdir()
    (work / '.draft').touch()
    monkeypatch.setattr(text_jobs, 'current', lambda: {'work': str(work)})
    monkeypatch.setattr(text_jobs, 'phase', lambda *_: None)
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: {'segments': []})
    saved = []
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda value: saved.append(copy.deepcopy(value)))
    beats = []
    for index in (1, 2):
        beat = _beat()
        beat.update(id=index, dialogue_units=[{'speaker': '林川', 'scope': 'onscreen', 'line': f'第{index}句。'}])
        beats.append(beat)
    original_model = prompt_planner._call_model
    fail_second = True

    def model(system, user, **kwargs):
        result, cost = original_model(system, user, **kwargs)
        if json.loads(user)['segment_index'] == 2 and fail_second:
            first = copy.deepcopy(result['shots'][0])
            second = copy.deepcopy(first)
            first['end_sec'] = second['start_sec'] = 4
            result.update(shot_mode='multi_shot', shots=[first, second])
        return result, cost

    monkeypatch.setattr(prompt_planner, '_call_model', model)
    with context.pin_runtime((work, 'test', '')):
        state.pipeline.generate({'beats': beats}, submit=False, workflow_only=True)
        assert saved[-1]['segments'][1]['planning_state'] == 'warning'
        assert saved[-1]['segments'][1]['planning_issues']
        fail_second = False
        state.pipeline.generate({'beats': beats}, submit=False, workflow_only=True)

    assert [call['segment_index'] for call in state.calls] == [1, 2, 2]
    assert len(saved) == 2
    assert [segment['segment_index'] for segment in saved[0]['segments']] == [1, 2]


def test_h3_prepare_uses_fresh_plan_without_rewriting_source_beat(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    original = copy.deepcopy(beat)

    prepared = state.pipeline._prepare_segment(
        state.pipeline._load_template(), beat, 1, None, check_files=False,
    )

    assert beat == original
    assert len(state.calls) == 1
    assert prepared["h3_prompt_plan"]["planning_fingerprint"]
    assert "林川收住笑意，将手轻放在桌边。" in prepared["job"]["final_prompt"]
    assert "旧的准备动作" not in prepared["job"]["final_prompt"]
    assert prepared["compiled_prompt_snapshot"] == prepared["job"]["final_prompt"]
    assert len(state.workflows) == 1 and state.workflows[0].is_file()


def test_h3_prepare_reuses_saved_plan_for_unchanged_inputs(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    template = state.pipeline._load_template()
    first = state.pipeline._prepare_segment(template, beat, 1, None, check_files=False)
    second = state.pipeline._prepare_segment(template, beat, 1, first, check_files=False)

    assert len(state.calls) == 1
    assert second["h3_prompt_plan"] == first["h3_prompt_plan"]
    assert second["job"]["final_prompt"] == first["job"]["final_prompt"]


def test_planning_only_keeps_long_prompt_without_building_workflow(planning_pipeline):
    state = planning_pipeline
    state.response = {'duration_sec': 18, 'duration_reason': '对白自然完成需要十八秒。',
                      'shot_mode': 'one_take', 'shots': [{'start_sec': 0, 'end_sec': 18,
                      'action': '人物自然解释。', 'dialogue_refs': [1]}]}
    beat = _beat()
    beat['dialogue_units'] = [{'speaker': '林川', 'scope': 'onscreen', 'line': '原文对白必须保留。'}]
    result = state.pipeline._prepare_segment(state.pipeline._load_template(), beat, 1,
                                             None, check_files=False, planning_only=True)
    assert result['job']['duration_sec'] == 18
    assert '原文对白必须保留。' in result['job']['final_prompt']
    assert result['planning_state'] == 'warning'
    assert result['workflow_path'] == '' and state.workflows == []


def test_invalid_response_does_not_prevent_next_beat_and_preserves_raw(planning_pipeline, monkeypatch):
    state = planning_pipeline
    from workflow import video_merge
    monkeypatch.setattr(video_merge, 'load_video_jobs', lambda: {'segments': []})
    monkeypatch.setattr(video_merge, 'save_video_jobs', lambda *_: None)
    original = prompt_planner._call_model
    def model(system, user, **kwargs):
        if json.loads(user)['segment_index'] == 1:
            return {'_raw_text': '{broken', '_parse_error': 'JSON 不完整'}, 0
        return original(system, user, **kwargs)
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    result = state.pipeline.generate({'beats': [_beat(), _beat()]}, submit=False, workflow_only=True)
    first, second = result['segments']
    assert first['planning_state'] == 'invalid'
    assert first['planning_raw_response']['_raw_text'] == '{broken'
    assert second['planning_state'] == 'ready' and second['job']['final_prompt']


def test_numbered_beat_dialogue_reaches_final_compiled_prompt_without_transcription(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    lines = ['先核对支付。', '给我具体办法。', '文档中有方案。', '是谁写的？', '项目负责人。', '原来是他。']
    beat['dialogue_units'] = [
        {'speaker': '林川', 'scope': 'onscreen', 'line': line} for line in lines
    ]
    state.response = {'shot_mode': 'multi_shot', 'shots': [
        {'start_sec': 0, 'end_sec': 5, 'subject': '林川', 'action': '人物交流。',
         'dialogue_refs': [1, 2, 3, 4]},
        {'start_sec': 5, 'end_sec': 8, 'subject': '林川', 'action': '人物继续回应。',
         'dialogue_refs': [5, 6]},
    ]}
    prepared = state.pipeline._prepare_segment(state.pipeline._load_template(), beat, 1,
                                               None, check_files=False)
    prompt = prepared['job']['final_prompt']
    assert all(prompt.count(line) == 1 for line in lines)
    assert [prompt.index(line) for line in lines] == sorted(prompt.index(line) for line in lines)
    assert len(state.calls) == 1
    assert 'dialogue_refs' not in prompt


def test_h3_manual_override_bypasses_planning_and_stale_beat_timing(planning_pipeline, monkeypatch):
    state = planning_pipeline
    manual = "[reference generation] 5 秒。\n[Shot 1]（0–5 秒）：人物在原地停下。"
    existing = {
        "segment_index": 1,
        "h3_auto_duration": True,
        "model_id": H3_REF2VA_MODEL_ID,
        "generation_config": {
            "duration_sec": 5, "asset_manifest": [], "prompt_override": manual,
        },
    }

    def unexpected_plan(*args, **kwargs):
        pytest.fail("a saved manual prompt must bypass model planning")

    monkeypatch.setattr(prompt_planner, "plan_h3_prompt", unexpected_plan)
    prepared = state.pipeline._prepare_segment(
        state.pipeline._load_template(), _beat(), 1, existing, check_files=False,
    )

    assert prepared["job"]["duration_sec"] == 5
    assert prepared["job"]["final_prompt"] == manual
    assert state.calls == []


def test_auto_duration_compiles_selected_time_into_job_and_workflow_once(planning_pipeline):
    state = planning_pipeline
    state.response = {'duration_sec': 11, 'duration_reason': '对白需要自然停顿，动作同时完成。',
                      'shot_mode': 'one_take', 'shots': [{'start_sec': 0, 'end_sec': 11,
                      'subject': '林川', 'action': '林川解释，简短抬手。', 'camera': '中近景'}]}
    beat = _beat()
    prepared = state.pipeline._prepare_segment(state.pipeline._load_template(), beat, 1,
        {'h3_auto_duration': True, 'h3_prompt_source': 'current_beat',
         'generation_config': {'duration_sec': 8, 'asset_manifest': []}}, check_files=False)
    assert len(state.calls) == 1
    assert prepared['job']['duration_sec'] == 11
    assert prepared['generation_config']['duration_sec'] == 11
    assert prepared['h3_duration_adjustment']['reference_sec'] == 8
    assert 'h3_auto_duration' not in prepared
    assert prepared['h3_prompt_plan']['shots'][-1]['end_sec'] == 11
    assert len(state.workflows) == 1
    assert beat['estimated_duration_sec'] == 8


def test_editor_replan_does_not_reintroduce_old_beat_dialogue_or_cast(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    beat['dialogue_units'] = [{'speaker': '旧角色', 'line': '已经删掉的旧对白。'}]
    beat['visible_roles'] = ['旧角色']
    state.response = {'duration_sec': 10, 'duration_reason': '根据编辑区台词安排。',
        'shots': [{'start_sec': 0, 'end_sec': 10, 'subject': '<Subject 1>', 'action': '新角色解释。',
                   'dialogue': {'speaker': '<Subject 1>', 'line': '新的说法。'}}]}
    prepared = state.pipeline._prepare_segment(state.pipeline._load_template(), beat, 1,
        {'h3_auto_duration': True, 'h3_editor_prompt': '<Subject 1>说：<d>[Chinese]新的说法。</d>',
         'generation_config': {'duration_sec': 8, 'asset_manifest': [
             {'asset_id': 'new-person', 'type': 'image', 'path': 'new.png', 'role': '新角色',
              'metadata': {'asset_kind': 'character'}}]}}, check_files=False)
    assert prepared['job']['duration_sec'] == 10
    assert '新的说法。' in prepared['job']['final_prompt']
    assert '旧对白' not in prepared['job']['final_prompt']
    assert '旧角色' not in prepared['job']['final_prompt']
    assert 'h3_editor_prompt' not in prepared
    assert state.calls[0]['current_beat'] == {}


def test_saved_timing_only_custom_prompt_compiles_without_rewriting_or_planning(planning_pipeline, monkeypatch):
    state = planning_pipeline
    manual = ('[reference generation] 11 秒。\n[Shot 1] (0-11 秒)：新角色抬眼解释。\n'
              '<Subject 1>说：<d>[Chinese]编辑区的新台词。</d>\n\n'
              '【人物音色绑定】\n保持当前编辑稿指定的音色关系。\n【人物音色绑定结束】')
    existing = {'segment_index': 1, 'h3_prompt_source': 'duration_replan',
                'generation_config': {'duration_sec': 11, 'asset_manifest': [], 'prompt_override': manual}}
    beat = _beat()
    beat['dialogue_units'] = [{'speaker': '旧人物', 'line': '旧台词不能回来。'}]
    monkeypatch.setattr('services.character_voice.attach_character_voices',
                        lambda *a: pytest.fail('retimed custom prompts must not restore old Beat voices'))
    template = state.pipeline._load_template()
    first = state.pipeline._prepare_segment(template, beat, 1, existing, check_files=False)
    second = state.pipeline._prepare_segment(template, beat, 1, first, check_files=False)
    assert first['job']['final_prompt'] == second['job']['final_prompt'] == manual
    assert second['generation_config']['prompt_override'] == manual
    assert second['job']['duration_sec'] == 11
    assert state.calls == []


def test_h3_explicit_regeneration_invalidates_plan_and_calls_model_once(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    template = state.pipeline._load_template()
    existing = state.pipeline._prepare_segment(template, beat, 1, None, check_files=False)
    existing["generation_config"]["prompt_override"] = "需要替换的手工稿"
    old_duration = existing["generation_config"]["duration_sec"]

    state.pipeline.reset_prompt_for_regeneration(existing)

    assert "prompt_override" not in existing["generation_config"]
    assert "h3_prompt_plan" not in existing
    assert existing["generation_config"]["duration_sec"] == old_duration
    assert not callable(getattr(state.pipeline, "regenerate_prompt_source", None))
    state.calls.clear()
    prepared = state.pipeline._prepare_segment(template, beat, 1, existing, check_files=False)

    assert len(state.calls) == 1
    assert prepared["h3_prompt_plan"]["planning_fingerprint"]


def test_h3_compiler_preserves_planned_dialogue_split_across_shots(monkeypatch):
    monkeypatch.setattr("generation.pipelines.minimax_h3.prompt_compiler.load_project_bible", lambda: {})
    line = "你先坐下，听我解释。"
    segment = {
        "dialogue_units": [{"speaker": "林川", "scope": "onscreen", "line": line}],
        "model_hints": {
            H3_REF2VA_MODEL_ID: {
                "planning_fingerprint": "validated-plan",
                "shot_mode": "multi_shot",
                "core_idea": "林川继续解释",
                "shots": [
                    {"start_sec": 0, "end_sec": 4, "action": "林川抬眼。", "dialogue": {
                        "speaker": "林川", "scope": "onscreen", "line": "你先坐下，",
                    }},
                    {"start_sec": 4, "end_sec": 8, "action": "林川继续解释。", "dialogue": {
                        "speaker": "林川", "scope": "onscreen", "line": "听我解释。", "continues_previous": True,
                    }},
                ],
            }
        },
    }
    original = copy.deepcopy(segment)
    config = H3GenerationConfig.from_mapping({"asset_manifest": []})
    compiled = H3PromptCompiler().compile(segment, config, H3AssetBinder().bind(config), duration_sec=8)

    assert compiled.text.count("<d>[Chinese]你先坐下，</d>") == 1
    assert compiled.text.count("<d>[Chinese]听我解释。</d>") == 1
    assert f"<d>[Chinese]{line}</d>" not in compiled.text
    assert "接着上个 Shot 继续说" in compiled.text
    assert segment == original


def test_h3_planning_conflict_does_not_save_workflow_or_change_existing_segment(planning_pipeline):
    state = planning_pipeline
    reason = "八秒无法完成当前台词与必要动作。"
    state.response = {"status": "needs_replan", "reason": reason}
    beat = _beat()
    existing = {
        "segment_index": 1,
        "status": "success",
        "video_path": "previous.mp4",
        "generation_config": {"duration_sec": 8, "asset_manifest": []},
        "h3_prompt_plan": {"planning_fingerprint": "old-plan", "shots": []},
    }
    original = copy.deepcopy((beat, existing))

    with pytest.raises(ValueError, match=reason):
        state.pipeline._prepare_segment(
            state.pipeline._load_template(), beat, 1, existing, check_files=False,
        )

    assert (beat, existing) == original
    assert state.workflows == []


def test_h3_explicit_regeneration_excludes_old_hint_dialogue(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    beat["model_hints"][H3_REF2VA_MODEL_ID]["shots"][0]["dialogue"] = {
        "speaker": "林川", "scope": "onscreen", "line": "已经删除的旧台词。",
    }
    existing = {
        "segment_index": 1,
        "h3_prompt_source": "current_beat",
        "generation_config": {"duration_sec": 8, "asset_manifest": []},
    }
    prepared = state.pipeline._prepare_segment(
        state.pipeline._load_template(), beat, 1, existing, check_files=False,
    )

    assert "已经删除的旧台词" not in json.dumps(state.calls[0], ensure_ascii=False)
    assert "已经删除的旧台词" not in prepared["job"]["final_prompt"]


@pytest.mark.parametrize("source_key", ["h3_shots", "shots"])
def test_h3_first_prepare_preserves_existing_string_dialogue_sources(planning_pipeline, source_key):
    state = planning_pipeline
    beat = _beat()
    beat.pop("model_hints")
    beat[source_key] = [{
        "start_sec": 0, "end_sec": 8, "action": "林川要求解释。", "dialogue": "林川：请说清楚。",
    }]
    prepared = state.pipeline._prepare_segment(
        state.pipeline._load_template(), beat, 1, None, check_files=False,
    )

    source = state.calls[0]["source_dialogue"]
    assert source[0]["speaker"] == "林川"
    assert source[0]["line"] == "请说清楚。"
    assert "<d>[Chinese]请说清楚。</d>" in prepared["job"]["final_prompt"]


def test_h3_voice_over_contract_accepts_narration_and_compiles_as_offscreen(planning_pipeline):
    state = planning_pipeline
    beat = _beat()
    beat["dialogue_units"] = [{
        "speaker_name": "旁白", "speech_type": "voice_over", "visible": False, "line": "会议室里安静下来。",
    }]
    state.response = {
        "core_idea": "会议室恢复安静",
        "shot_mode": "one_take",
        "shots": [{
            "start_sec": 0, "end_sec": 8, "action": "会议室中人物安静等待。",
            "dialogue": {"speaker": "旁白", "scope": "narration", "line": "会议室里安静下来。"},
        }],
    }
    prepared = state.pipeline._prepare_segment(
        state.pipeline._load_template(), beat, 1, None, check_files=False,
    )

    assert "以画外旁白说：<d>[Chinese]会议室里安静下来。</d>" in prepared["job"]["final_prompt"]
    assert "<d>[Chinese]会议室里安静下来。</d>" in prepared["job"]["final_prompt"]


@pytest.mark.parametrize('source', ['shot_edit', 'duration_replan', None])
def test_saved_screenplay_keeps_automatic_images_when_rebuilding_for_submit(planning_pipeline, monkeypatch, source):
    state = planning_pipeline
    manifest = [
        {'asset_id': 'person', 'type': 'image', 'path': 'person.png', 'role': '林川',
         'metadata': {'asset_kind': 'character', 'identity_id': 'person',
                      'binding_source': 'auto_character_match'}},
        {'asset_id': 'scene', 'type': 'image', 'path': 'scene.png', 'role': '会议室',
         'metadata': {'asset_kind': 'background', 'binding_source': 'auto_background_match'}},
        {'asset_id': 'voice', 'type': 'audio', 'path': 'voice.wav',
         'metadata': {'identity_id': 'person', 'voice_only': True, 'duration_sec': 3}}]
    prompt = ('【主体定义】\n<Subject 1>：林川；可见内容来自 <Picture 1>。\n'
              '<Subject 2>：会议室；可见内容来自 <Picture 2>。\n'
              '【人物音色绑定】\n<Audio 1> → <Subject 1>：音色参考。\n'
              '【详细镜头描述】\n[Shot 1]（0–8 秒）：场景使用 <Subject 2>；'
              '<Subject 1>说：<d>[Chinese]你好。</d>')
    existing = {'h3_prompt_source': source, 'generation_config': {
        'duration_sec': 8, 'asset_manifest': copy.deepcopy(manifest),
        'reference_selection_source': 'automatic', 'prompt_override': prompt}}
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.merge_auto_backgrounds',
                        lambda *a: pytest.fail('saved prompt must not rematch references'))
    before = copy.deepcopy(existing)
    prepared = state.pipeline._prepare_segment(state.pipeline._load_template(), _beat(), 1,
                                                existing, check_files=False)
    assert existing == before
    assert [a['type'] for a in prepared['generation_config']['asset_manifest']] == ['image', 'image', 'audio']
    assert [a['type'] for a in prepared['job']['asset_manifest']] == ['image', 'image', 'audio']
    assert '<Audio 1>' in prepared['job']['final_prompt']
    assert state.calls == []
