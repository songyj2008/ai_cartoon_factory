import json

from generation.pipelines.minimax_h3 import prompt_planner


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
