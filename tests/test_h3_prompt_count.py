import copy
from types import SimpleNamespace

from generation.service import GenerationService
from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler


def test_preview_counts_final_prompt_without_writing_or_mutating_jobs(monkeypatch):
    pipeline = object.__new__(MiniMaxH3Ref2VAPipeline)
    pipeline.adapter = SimpleNamespace(prompt_compiler=H3PromptCompiler())
    pipeline.spec = SimpleNamespace(capabilities=SimpleNamespace(prompt_max_chars=7000))
    service = object.__new__(GenerationService)
    service._pipeline_for = lambda model: pipeline
    config = {"asset_manifest": [{"id": "person", "type": "image", "path": "person.png", "role": "陈默", "metadata": {"asset_kind": "character"}}]}
    segment = {"segment_index": 10, "model_id": "minimax_h3_local_ref2va", "generation_config": config}
    jobs = {"segments": [segment]}
    original = copy.deepcopy(jobs)
    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: jobs)
    monkeypatch.setattr("workflow.video_merge.save_video_jobs", lambda *args: (_ for _ in ()).throw(AssertionError("preview must not write")))
    monkeypatch.setattr("generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata", lambda value: value)
    monkeypatch.setattr("services.character_voice.refresh_character_voices", lambda value, root: value)
    text = "【详细镜头描述】\n陈默说：<d>[Chinese]刘洋，你好😀。</d>"
    preview = service.preview_segment_prompt(10, text, config["asset_manifest"])
    canonical = service._prepare_h3_prompt(segment, config, config, text)["prompt_override"]
    assert preview["prompt_chars"] == len(canonical) > len(text)
    assert preview["prompt_max_chars"] == 7000
    assert jobs == original
    assert service.preview_segment_prompt(10, "a" * 8207, [], force_save=True) == {
        "prompt_chars": 8207, "prompt_max_chars": 7000, "over_limit": True,
    }
