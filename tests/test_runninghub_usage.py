from workflow.runninghub_usage import (
    record_runninghub_attempt,
    refresh_runninghub_usage_summaries,
    runninghub_attempt_model_id,
    task_usage_from_outputs,
)


def test_task_usage_uses_max_duration_and_sums_unique_output_coins():
    usage = task_usage_from_outputs(
        [
            {"nodeId": "20", "fileUrl": "https://example.test/a.mp4", "taskCostTime": "174", "consumeCoins": "35"},
            {"nodeId": "21", "fileUrl": "https://example.test/preview.jpg", "taskCostTime": "174", "consumeCoins": "2"},
            {"nodeId": "20", "fileUrl": "https://example.test/a.mp4", "taskCostTime": "174", "consumeCoins": "35"},
        ]
    )

    assert usage["task_cost_seconds"] == 174
    assert usage["consume_coins"] == 37
    assert len(usage["billing_outputs"]) == 2


def test_attempt_history_preserves_retries_and_final_video_sums_segment_costs():
    first_segment = {}
    record_runninghub_attempt(
        first_segment,
        "task-1",
        status="success",
        outputs=[{"nodeId": "20", "fileUrl": "https://example.test/1.mp4", "taskCostTime": "80", "consumeCoins": "12"}],
    )
    record_runninghub_attempt(
        first_segment,
        "task-2",
        status="success",
        outputs=[{"nodeId": "20", "fileUrl": "https://example.test/2.mp4", "taskCostTime": "95", "consumeCoins": "15"}],
    )
    # A later asynchronous poll must update the matching task, not add a retry.
    record_runninghub_attempt(
        first_segment,
        "task-2",
        status="success",
        outputs=[{"nodeId": "20", "fileUrl": "https://example.test/2.mp4", "taskCostTime": "95", "consumeCoins": "15"}],
    )

    second_segment = {}
    record_runninghub_attempt(
        second_segment,
        "task-3",
        status="success",
        outputs=[{"nodeId": "20", "fileUrl": "https://example.test/3.mp4", "taskCostTime": "60", "consumeCoins": "8"}],
    )
    payload = {"segments": [first_segment, second_segment]}

    refresh_runninghub_usage_summaries(payload)

    assert [item["task_id"] for item in first_segment["runninghub_attempts"]] == ["task-1", "task-2"]
    assert first_segment["runninghub_usage"] == {
        "generation_count": 2,
        "total_task_cost_seconds": 175,
        "task_cost_seconds_known": True,
        "total_consume_coins": 27,
        "consume_coins_known": True,
        "total_consume_points": 0,
        "consume_points_known": False,
    }
    assert payload["final_video_usage"]["generation_count"] == 3
    assert payload["final_video_usage"]["total_task_cost_seconds"] == 235
    assert payload["final_video_usage"]["total_consume_coins"] == 35


def test_attempt_history_keeps_the_submitted_prompt_and_downloaded_video():
    segment = {}

    record_runninghub_attempt(
        segment,
        "task-1",
        status="running",
        submitted_prompt="full prompt submitted to the workflow",
    )
    record_runninghub_attempt(
        segment,
        "task-1",
        status="success",
        video_path="F:/project/videos/part_001_task-1.mp4",
    )

    attempt = segment["runninghub_attempts"][0]
    assert attempt["submitted_prompt"] == "full prompt submitted to the workflow"
    assert attempt["video_path"] == "F:/project/videos/part_001_task-1.mp4"


def test_attempt_history_records_the_model_that_created_the_task():
    segment = {
        "task_id": "task-h3",
        "model_id": "minimax_h3_local_ref2va",
        "resolved_model": {"id": "minimax_h3_local_ref2va"},
    }

    record_runninghub_attempt(segment, "task-h3", status="running")

    attempt = segment["runninghub_attempts"][0]
    assert attempt["model_id"] == "minimax_h3_local_ref2va"
    assert runninghub_attempt_model_id(segment, attempt) == "minimax_h3_local_ref2va"


def test_render_provenance_wins_over_the_current_segment_model():
    segment = {
        "task_id": "new-task",
        "model_id": "minimax_h3_local_ref2va",
        "resolved_model": {"id": "minimax_h3_local_ref2va"},
        "renders": [
            {
                "render_id": "render-old-ltx",
                "model": {"id": "ltx23_licon_msr_v2"},
                "submission": {"task_id": "old-task"},
                "output": {},
            }
        ],
    }
    attempt = {"task_id": "old-task", "render_id": "render-old-ltx"}

    assert runninghub_attempt_model_id(segment, attempt) == "ltx23_licon_msr_v2"


def test_an_old_unlinked_attempt_is_not_relabelled_after_a_model_switch():
    segment = {
        "task_id": "new-task",
        "model_id": "minimax_h3_local_ref2va",
        "resolved_model": {"id": "minimax_h3_local_ref2va"},
    }

    assert runninghub_attempt_model_id(segment, {"task_id": "old-task"}) == ""


def test_real_point_response_is_recorded_without_coin_conversion():
    from workflow.runninghub_client import _find_outputs
    from workflow.runninghub_usage import attempt_needs_usage_sync
    response = {"code": 0, "data": [
        {"fileUrl": "https://example.test/a.mp4", "nodeId": "160", "taskCostTime": "0",
         "consumeCoins": None, "settleMode": "POINT", "chargedPoint": "186.66"},
        {"fileUrl": "https://example.test/b.mp4", "nodeId": "161", "taskCostTime": "0",
         "consumeCoins": None, "settleMode": "POINT", "chargedPoint": "186.66"},
    ]}
    segment = {"runninghub_attempts": [{"task_id": "task", "status": "success", "usage_status": "recorded", "task_cost_seconds": 0}]}
    assert attempt_needs_usage_sync(segment, "task")
    attempt = record_runninghub_attempt(segment, "task", outputs=_find_outputs(response))
    assert attempt["consume_points"] == 186.66
    assert attempt["task_cost_seconds"] == 0
    assert attempt.get("consume_coins") is None
    assert not attempt_needs_usage_sync(segment, "task")
    payload = {"segments": [segment]}
    refresh_runninghub_usage_summaries(payload)
    assert payload["final_video_usage"]["total_consume_points"] == 186.66


def test_v2_and_webhook_task_usage_is_counted_once():
    from workflow.runninghub_client import _find_outputs
    event = {"status": "SUCCESS", "usage": {"chargedPoint": "12.345", "taskCostTime": "174"},
             "results": [{"url": "https://example.test/a.mp4"}, {"url": "https://example.test/b.mp4"}],
             "taskUsageList": [{"usage": {"chargedPoint": "12.345"}}]}
    for response in (event, {"event": "TASK_END", "eventData": event}):
        outputs = _find_outputs(response)
        assert len([item for item in outputs if item.get("url")]) == 2
        usage = task_usage_from_outputs(outputs)
        assert usage["consume_points"] == 12.345
        assert usage["task_cost_seconds"] == 174


def test_missing_and_zero_point_costs_remain_distinct():
    from workflow.runninghub_usage import attempt_needs_usage_sync
    missing = record_runninghub_attempt({}, "missing", status="success", outputs=[{"taskCostTime": "0"}])
    assert missing.get("consume_points") is None
    assert missing["usage_status"] == "unavailable"
    zero = record_runninghub_attempt({}, "zero", status="success", outputs=[{"chargedPoint": "0", "taskCostTime": "0"}])
    assert zero["consume_points"] == 0
    assert zero["usage_status"] == "recorded"
    assert not attempt_needs_usage_sync({"runninghub_attempts": [missing]}, "missing")


def test_task_level_usage_overrides_repeated_file_billing():
    from workflow.runninghub_client import _find_outputs
    usage = task_usage_from_outputs(_find_outputs({
        "usage": {"chargedPoint": "25", "taskCostTime": "120"},
        "results": [{"url": "https://example.test/a.mp4", "chargedPoint": "25"},
                    {"url": "https://example.test/b.mp4", "chargedPoint": "25"}],
    }))
    assert usage["consume_points"] == 25
    assert usage["task_cost_seconds"] == 120


def test_usage_ui_displays_points_and_does_not_claim_zero_execution_time():
    from ui.group_panel import _runninghub_usage_history, _format_rh_duration, _format_usage_number
    segment = {"segment_index": 1}
    record_runninghub_attempt(segment, "task", status="success", outputs=[{
        "nodeId": "160", "chargedPoint": "186.66", "settleMode": "POINT", "taskCostTime": "0"}])
    refresh_runninghub_usage_summaries({"segments": [segment]})
    rendered = _runninghub_usage_history(segment)
    assert "186.66 RH积分" in rendered
    assert "RH币" not in rendered
    assert "未提供（接口返回 0）" in rendered
    assert _format_usage_number(12.345, " RH积分") == "12.345 RH积分"
    assert _format_rh_duration(174) == "02:54"
    assert _format_rh_duration(None) == "待同步"
    assert _format_rh_duration("invalid") == "待同步"
