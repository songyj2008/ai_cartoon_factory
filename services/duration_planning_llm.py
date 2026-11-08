"""Ordered, receipted LLM calls for timing an existing H3 prompt only."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from services.logger import log


MODEL = "deepseek-v4-pro"
POLICY = "duration-pro-v2"
_API_SLOT = threading.BoundedSemaphore(1)
LABELS = {"chatgpt": "ChatGPT 网页版", "api": f"DeepSeek API · {MODEL}",
          "deepseek": "DeepSeek 网页版"}


def provider_order(selected, api_enabled=False):
    web = [p for p in dict.fromkeys(selected) if p not in ('api', 'inherit')]
    return (["chatgpt"] if "chatgpt" in web else []) + (["api"] if api_enabled else []) + (
        ["deepseek"] if "deepseek" in web else []) + [p for p in web if p not in ('chatgpt', 'deepseek')]


def provider_label(provider):
    return LABELS.get(provider, provider + ' 网页版')


def _load(label):
    from services import text_jobs
    job = text_jobs.current()
    return ((text_jobs.get(job["id"]).get("timing_calls") or {}).get(label) or {}) if job else {}


def _save(label, receipt):
    from services import text_jobs
    text_jobs.check_cancelled()
    job = text_jobs.current()
    if job:
        with text_jobs._LOCK:
            calls = dict(text_jobs.get(job["id"]).get("timing_calls") or {})
            calls[label] = receipt
            text_jobs.update(job["id"], timing_calls=calls,
                             timing_result={key: receipt[key] for key in ("provider", "model") if key in receipt})


def _api(system, user, max_tokens, label):
    from services import llm
    from services.context import CONFIG
    from services.file_utils import get_project_temp_dir

    name = get_project_temp_dir() / f"{llm._safe_name(label)}_pro_{time.time_ns()}"
    text, _, details = llm.call_deepseek_chat_sdk(
        {**CONFIG, "deepseek_model": MODEL},
        [{"role": "system", "content": str(system) + "\n" + llm.JSON_ONLY_INSTRUCTION},
         {"role": "user", "content": str(user) + "\n" + llm.JSON_ONLY_INSTRUCTION}],
        temperature=float(CONFIG.get("temperature", 0.1)), max_tokens=max_tokens,
        request_path=str(name) + "_request.json", full_response_path=str(name) + "_response.json",
        timeout=120.0, max_retries=0,
    )
    llm._log_usage(label, details)
    # Do not manufacture missing fields with an additional JSON-repair model call.
    if not text.strip() or details.get("content_source") != "content":
        raise ValueError("DeepSeek API 未返回最终 JSON 内容")
    if details.get("finish_reason") == "length":
        raise ValueError("DeepSeek API 的时长结果被截断")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("DeepSeek API 的时长结果必须是 JSON 对象")
    return data, details.get("usage") or {}


def call_duration_llm(system_prompt, user_prompt, *, max_tokens, label, provider_override="inherit"):
    from services import text_jobs
    from services.web_llm_bridge import routes_for, wait_for_llm

    text_jobs.check_cancelled()
    job = text_jobs.current()
    policy = (job or {}).get('timing_policy')
    if policy:
        if policy.get('version') != POLICY:
            raise text_jobs.NeedsAttentionError('时长渠道选择方式已更新，请重新提交规划任务；原记录已保留',
                                                reason='timing_input_changed')
        order = list(policy['order'])
    else:
        selected = routes_for("prompt", provider_override)
        order = provider_order(selected, api_enabled='api' in selected)
    if not order:
        raise ValueError('请勾选规划时长使用的网页 LLM 或 DeepSeek API')
    fingerprint = hashlib.sha256(json.dumps(
        [POLICY, MODEL, order, system_prompt, user_prompt, max_tokens],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    receipt = _load(label)
    if receipt and receipt.get("request_hash") != fingerprint:
        raise text_jobs.NeedsAttentionError("时长规划输入已变化，请新建规划任务；原调用记录已保留",
                                            reason="timing_input_changed")
    if not receipt:
        receipt = {"policy": POLICY, "request_hash": fingerprint, "attempts": {}}
    started = time.monotonic()
    failures = []
    for provider in order:
        text_jobs.check_cancelled()
        previous = receipt["attempts"].get(provider) or {}
        if previous.get("state") == "complete":
            return previous["data"], previous.get("elapsed_sec", 0)
        if previous.get("state") == "error":
            failures.append(f"{provider_label(provider)}：{previous['error']}")
            continue
        if provider == "api" and previous.get("state") == "running":
            # The server may already have charged/answered. Never replay a paid
            # request after a process crash without a persisted result.
            raise text_jobs.NeedsAttentionError("Pro API 调用曾中断且结果未确认，请检查原任务；不会自动重复请求",
                                                reason="api_result_unconfirmed")
        attempt = {"state": "queued" if provider == "api" else "running",
                   "started_at": previous.get("started_at") or time.time()}
        receipt["attempts"][provider] = attempt
        _save(label, receipt)
        text_jobs.phase("规划时长 · " + provider_label(provider))
        log(f"[{label}] 规划时长使用 {provider_label(provider)}", "STEP")
        attempt_started = time.monotonic()
        try:
            if provider == "api":
                while not _API_SLOT.acquire(timeout=0.25):
                    text_jobs.check_cancelled()
                try:
                    text_jobs.check_cancelled()
                    attempt.update(state="running", started_at=time.time())
                    _save(label, receipt)
                    data, usage = _api(system_prompt, user_prompt, max_tokens, label)
                finally:
                    _API_SLOT.release()
            else:
                data, _ = wait_for_llm(
                    system_prompt, user_prompt, kind="json", entry="prompt", provider=provider,
                    call_hint=f"{label}:{POLICY}:{provider}", metadata={"ordered_timing_fallback": True})
                usage = {}
            if not isinstance(data, dict):
                raise ValueError("时长规划结果必须为 JSON 对象")
        except text_jobs.NeedsAttentionError:
            raise
        except Exception as exc:
            text_jobs.check_cancelled()
            attempt.update(state="error", error=str(exc), finished_at=time.time(),
                           elapsed_sec=round(time.monotonic() - attempt_started, 2))
            _save(label, receipt)
            failures.append(f"{provider_label(provider)}：{exc}")
            log(f"[{label}] {provider_label(provider)} 调用失败，尝试下一渠道：{exc}", "WARN")
            continue
        text_jobs.check_cancelled()
        attempt.update(state="complete", data=data, usage=usage, finished_at=time.time(),
                       elapsed_sec=round(time.monotonic() - attempt_started, 2))
        receipt.update(provider=provider, model=MODEL if provider == "api" else None)
        _save(label, receipt)
        # Content validation (including needs_replan) stays in prompt_retimer;
        # an unsatisfactory planning decision must not trigger model shopping.
        return data, round(time.monotonic() - started, 2)
    raise RuntimeError("时长规划各渠道均调用失败：" + "；".join(failures))


def result_source(index):
    receipt = _load(f"h3_prompt_retime_{int(index):03d}")
    return {key: receipt[key] for key in ("provider", "model") if key in receipt}
