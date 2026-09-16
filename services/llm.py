"""LLM client, diagnostics, retries, and JSON response parsing."""
import json
import re
import time
from pathlib import Path

from openai import OpenAI

from services.context import CONFIG
from services.file_utils import get_project_temp_dir
from services.logger import log
from services.secrets import DEEPSEEK_API_KEY_ENV, secret_value


_CLIENT: OpenAI | None = None
_CLIENT_KEY = ""


def _deepseek_api_key() -> str:
    key = secret_value(DEEPSEEK_API_KEY_ENV)
    if not key:
        raise ValueError(f"DeepSeek API key is not configured. Set {DEEPSEEK_API_KEY_ENV} in UI settings.")
    return key


def _deepseek_client() -> OpenAI:
    global _CLIENT, _CLIENT_KEY
    key = _deepseek_api_key()
    if _CLIENT is None or _CLIENT_KEY != key:
        _CLIENT = OpenAI(
            api_key=key,
            base_url=CONFIG.get("deepseek_base_url", "https://api.deepseek.com"),
        )
        _CLIENT_KEY = key
    return _CLIENT

JSON_ONLY_INSTRUCTION = (
    "只输出一个完整 JSON 对象，不要 markdown，不要代码块，不要解释文字。"
)


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "llm")).strip("_") or "llm"


def _to_plain_data(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    if hasattr(value, "__dict__"):
        return {
            str(key): _to_plain_data(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def _save_payload(label, kind, payload):
    temp_dir = get_project_temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_label = _safe_name(label)
    if safe_label == "story":
        filename = f"{safe_label}_{kind}.json"
    else:
        filename = f"{safe_label}_{kind}_{time.time_ns()}.json"
    path = temp_dir / filename
    path.write_text(
        json.dumps(_to_plain_data(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _save_deepseek_request(label, kind, request):
    base_url = str(CONFIG.get("deepseek_base_url", "https://api.deepseek.com")).rstrip("/")
    snapshot = {
        "method": "POST",
        "url": base_url + "/chat/completions",
        "timeout": "openai-sdk-default",
        "headers": {
            "Authorization": "Bearer ***",
            "Content-Type": "application/json",
        },
        "json": _to_plain_data(request),
    }
    return _save_payload(label, f"{kind}_request", snapshot)


def _get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def response_details(response):
    choices = _get(response, "choices", []) or []
    choice = choices[0] if choices else {}
    message = _get(choice, "message", {}) or {}
    delta = _get(choice, "delta", {}) or {}
    content = _get(message, "content", "") or ""
    reasoning = _get(message, "reasoning_content", "") or ""
    delta_content = _get(delta, "content", "") or ""
    text = _get(choice, "text", "") or ""
    return {
        "choices": _to_plain_data(choices),
        "message": _to_plain_data(message),
        "content": str(content),
        "reasoning_content": str(reasoning),
        "delta_content": str(delta_content),
        "text": str(text),
        "finish_reason": str(_get(choice, "finish_reason", "") or ""),
        "usage": _to_plain_data(_get(response, "usage")),
    }


def extract_response_text(response):
    details = response_details(response)
    for key in ("content", "reasoning_content", "delta_content", "text"):
        text = str(details.get(key) or "").strip()
        if text:
            return text, key, details
    return "", "", details


def _save_bad_response(label, raw_text, full_response=None, suffix=""):
    temp_dir = get_project_temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_label = _safe_name(label)
    if safe_label == "story":
        text_path = temp_dir / f"story_bad_response{suffix}.txt"
        json_path = temp_dir / f"story_bad_response{suffix}.json"
    else:
        timestamp = time.time_ns()
        text_path = temp_dir / f"bad_response_{safe_label}_{timestamp}{suffix}.txt"
        json_path = temp_dir / f"bad_response_{safe_label}_{timestamp}{suffix}.json"
    text_path.write_text(str(raw_text or ""), encoding="utf-8")
    json_path.write_text(
        json.dumps(_to_plain_data(full_response), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return text_path, json_path


def extract_json(text, full_response=None, label="llm"):
    source = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", source, flags=re.S | re.I)
    candidate = fenced.group(1) if fenced else ""
    if not candidate:
        start = source.find("{")
        end = source.rfind("}")
        if start >= 0 and end >= start:
            candidate = source[start:end + 1]
    if not candidate:
        bad_path, response_path = _save_bad_response(
            label, source, full_response
        )
        raise ValueError(
            f"未找到 JSON 内容，原文已保存: {bad_path}；"
            f"完整响应已保存: {response_path}"
        )

    try:
        return json.loads(candidate)
    except Exception:
        pass

    fixed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", candidate)
    fixed = re.sub(
        r'(")\s*\n\s*("[A-Za-z0-9_\u4e00-\u9fff]+"\s*:)',
        r"\1,\n  \2",
        fixed,
    )
    try:
        return json.loads(fixed)
    except Exception as exc:
        bad_path, response_path = _save_bad_response(
            label, source, full_response
        )
        fixed_path = bad_path.with_name(
            bad_path.stem + "_fixed.txt"
        )
        fixed_path.write_text(fixed, encoding="utf-8")
        raise ValueError(
            f"JSON 解析失败，原文已保存: {bad_path}；"
            f"完整响应已保存: {response_path}；"
            f"修复版已保存: {fixed_path}；错误: {exc}"
        ) from exc


def save_deepseek_sdk_request(path, config, request, timeout="openai-sdk-default"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    base_url = str(config.get("deepseek_base_url", "https://api.deepseek.com")).rstrip("/")
    snapshot = {
        "method": "POST",
        "url": base_url + "/chat/completions",
        "timeout": timeout,
        "headers": {
            "Authorization": "Bearer ***",
            "Content-Type": "application/json",
        },
        "json": _to_plain_data(request),
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_json_payload(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_plain_data(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def call_deepseek_chat_sdk(
    config,
    messages,
    *,
    temperature=0.2,
    max_tokens=12000,
    json_mode=True,
    request_path=None,
    full_response_path=None,
    raw_response_path=None,
    timeout="openai-sdk-default",
):
    """Call DeepSeek through the OpenAI SDK with thinking disabled.

    This is used by standalone pipeline scripts that need to keep their
    historical diagnostic file names while sharing the same SDK behavior as
    services.llm.call_llm.
    """
    model_name = config.get("deepseek_model", CONFIG.get("deepseek_model", "deepseek-v4-flash"))
    request = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if str(model_name).startswith("deepseek-v4"):
        request["extra_body"] = {"thinking": {"type": "disabled"}}
    if json_mode:
        request["response_format"] = {"type": "json_object"}

    if request_path:
        save_deepseek_sdk_request(request_path, config, request, timeout=timeout)

    client = OpenAI(
        api_key=_deepseek_api_key(),
        base_url=config.get("deepseek_base_url", "https://api.deepseek.com"),
    )
    response = client.chat.completions.create(**request)
    full_data = _to_plain_data(response)
    if full_response_path:
        write_json_payload(full_response_path, full_data)

    text, source, details = extract_response_text(response)
    details["content_source"] = source
    if raw_response_path:
        raw_path = Path(raw_response_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(text or "", encoding="utf-8")
    return text, full_data, details


def _completion(messages, max_tokens, temperature, label, kind, json_mode=True):
    model_name = CONFIG.get("deepseek_model", "deepseek-v4-flash")
    try:
        request = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if str(model_name).startswith("deepseek-v4-flash"):
            request["extra_body"] = {
                "thinking": {
                    "type": "disabled"
                }
            }
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        request_path = _save_deepseek_request(label, kind, request)
        log(f"{label} DeepSeek request saved: {request_path}", "STEP")
        response = _deepseek_client().chat.completions.create(
            **request
        )
    except Exception as exc:
        error_body = {
            "error": str(exc),
            "body": _to_plain_data(getattr(exc, "body", None)),
            "response": _to_plain_data(getattr(exc, "response", None)),
        }
        path = _save_payload(label, f"{kind}_error", error_body)
        log(f"{label} API 错误响应已保存: {path}", "ERROR")
        raise
    full_path = _save_payload(label, f"{kind}_response", response)
    text, source, details = extract_response_text(response)
    log(
        f"{label} finish_reason={details['finish_reason'] or 'unknown'}, "
        f"内容来源={source or 'empty'}",
        "STEP",
    )
    log(f"{label} 完整响应已保存: {full_path}", "STEP")
    return response, text, details


def repair_json(raw_text, label="大模型", max_tokens=3000):
    prompt = (
        "把下面可能截断或格式错误的内容修复成一个完整、合法的 JSON 对象。"
        "保留已有信息，补齐必要的闭合符号和缺失字段。"
        + JSON_ONLY_INSTRUCTION
        + "\n\n待修复内容：\n"
        + str(raw_text or "")
    )
    response, text, details = _completion(
        [
            {"role": "system", "content": "你是严格的 JSON 修复器。" + JSON_ONLY_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        max(max_tokens, 3000),
        0,
        label,
        "repair",
    )
    if not text:
        _save_bad_response(label, text, response_details(response), "_repair_empty")
        raise ValueError("JSON 修复请求返回空内容")
    return extract_json(text, response_details(response), label)


def _log_usage(label, details):
    usage = details.get("usage") or {}
    if usage:
        log(
            f"{label} 令牌统计: 输入={usage.get('prompt_tokens')}, "
            f"输出={usage.get('completion_tokens')}, "
            f"总计={usage.get('total_tokens')}",
            "STEP",
        )


def call_llm(system_prompt, user_prompt, max_tokens=2000, label="大模型"):
    model_name = CONFIG.get("deepseek_model", "deepseek-v4-flash")
    temperature = float(CONFIG.get("temperature", 0.1))
    max_tokens = max(int(max_tokens or 0), 3000)
    messages = [
        {"role": "system", "content": str(system_prompt) + "\n" + JSON_ONLY_INSTRUCTION},
        {"role": "user", "content": str(user_prompt) + "\n" + JSON_ONLY_INSTRUCTION},
    ]
    log(f"{label} 调用开始")
    log(f"模型: {model_name}", "STEP")
    log(f"最大令牌数: {max_tokens}，随机度: {temperature}", "STEP")

    started_at = time.time()
    response, text, details = _completion(
        messages, max_tokens, temperature, label, "raw"
    )
    _log_usage(label, details)
    log(f"{label} 返回字符数: {len(text)}", "STEP")

    if not text:
        _save_bad_response(label, text, response_details(response), "_empty")
        log(f"{label} 返回内容为空，自动重试一次", "WARN")
        response, text, details = _completion(
            messages, max(max_tokens, 4000), temperature, label, "retry"
        )
        _log_usage(label, details)
        log(f"{label} 重试返回字符数: {len(text)}", "STEP")

    if details.get("finish_reason") == "length":
        _save_bad_response(label, text, response_details(response), "_length")
        log(f"{label} 输出因 length 截断，开始自动修复 JSON", "WARN")
        data = repair_json(text, label=label, max_tokens=max_tokens)
    else:
        try:
            data = extract_json(text, response_details(response), label)
        except ValueError:
            if not text:
                raise
            log(f"{label} 首次 JSON 解析失败，开始自动修复", "WARN")
            data = repair_json(text, label=label, max_tokens=max_tokens)

    cost = round(time.time() - started_at, 2)
    log(f"{label} 耗时: {cost} 秒", "STEP")
    return data, cost


def call_llm_text(system_prompt, user_prompt, max_tokens=2000, label="大模型"):
    model_name = CONFIG.get("deepseek_model", "deepseek-v4-flash")
    temperature = float(CONFIG.get("temperature", 0.1))
    log(f"{label} 调用开始")
    log(f"模型: {model_name}", "STEP")
    log(f"最大令牌数: {max_tokens}，随机度: {temperature}", "STEP")
    started_at = time.time()
    response, text, details = _completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens,
        temperature,
        label,
        "text",
        json_mode=False,
    )
    _log_usage(label, details)
    log(f"{label} 返回字符数: {len(text)}", "STEP")
    log(f"{label} 耗时: {round(time.time() - started_at, 2)} 秒", "STEP")
    return text.strip(), round(time.time() - started_at, 2)
