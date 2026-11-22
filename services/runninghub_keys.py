"""Resolve RunningHub credentials at runtime and keep task bindings stable."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from threading import RLock

from services.context import CONFIG
from services.secrets import RUNNINGHUB_API_KEY_ENV, secret_value


RUNNINGHUB_API_KEYS_ENV = "RUNNINGHUB_API_KEYS"
AUTO = "round_robin"
CONFIG_KEYS_FIELD = "runninghub_api_keys"
SELECTION_FIELD = "runninghub_key_selection"
LEGACY_ALIAS = "原有 Key"
_lock = RLock()
_next_key = 0


@dataclass(frozen=True)
class RunningHubKey:
    alias: str
    value: str = field(repr=False)

    @property
    def id(self) -> str:
        return sha256(("runninghub:" + self.value).encode("utf-8")).hexdigest()[:24]


def _parse_keys(data) -> list[RunningHubKey]:
    if not isinstance(data, dict):
        raise ValueError("RunningHub 多 Key 配置应为别名到 Key 的 JSON 对象")
    keys = []
    for alias, value in data.items():
        if (not isinstance(alias, str) or not alias or alias != alias.strip()
                or len(alias) > 24 or alias in {AUTO, LEGACY_ALIAS}
                or not alias.isprintable()):
            raise ValueError("RunningHub Key 别名须为 1 到 24 个可见字符，且不能使用保留名称")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("RunningHub 多 Key 配置中有空 Key")
        keys.append(RunningHubKey(alias, value.strip()))
    if len({key.value for key in keys}) != len(keys):
        raise ValueError("RunningHub 多 Key 配置中有重复 Key")
    return keys


def _environment_keys() -> list[RunningHubKey]:
    value = secret_value(RUNNINGHUB_API_KEYS_ENV, allow_plaintext=True)
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError("RunningHub 多 Key 环境变量的 JSON 格式无效") from None
    return _parse_keys(data)


def _config_keys() -> list[RunningHubKey]:
    return _parse_keys(CONFIG.get(CONFIG_KEYS_FIELD, {}))


def _legacy_keys() -> list[RunningHubKey]:
    value = secret_value(RUNNINGHUB_API_KEY_ENV, allow_plaintext=True)
    return [RunningHubKey(LEGACY_ALIAS, value)] if value else []


def _active_keys() -> list[RunningHubKey]:
    return _environment_keys() or _legacy_keys() or _config_keys()


def runninghub_key_aliases() -> list[str]:
    return [key.alias for key in _active_keys()]


def update_config_keys(configured: dict, alias: str, value: str) -> dict[str, str]:
    """Prepare an update to the private config without copying environment keys."""
    updated = {key.alias: key.value for key in _parse_keys(configured)}
    if not alias:
        if len(updated) > 1:
            raise ValueError("请填写本次保存的 RunningHub Key 别名")
        alias = next(iter(updated), "default")
    updated[alias] = value
    return {key.alias: key.value for key in _parse_keys(updated)}


def resolve_runninghub_key(key_id: str | None = None) -> RunningHubKey:
    """Choose a key for a new task, or resolve a previously submitted task."""
    global _next_key
    if key_id is not None:
        if not key_id:
            legacy = _legacy_keys()
            if legacy:
                return legacy[0]
            config = _config_keys()
            if len(config) == 1:
                return config[0]
            raise ValueError("旧 RunningHub 任务缺少 Key 标识，请恢复提交时使用的单 Key 配置")
        for load_keys in (_environment_keys, _config_keys, _legacy_keys):
            for key in load_keys():
                if key.id == key_id:
                    return key
        raise ValueError("缺少此 RunningHub 任务提交时使用的 Key，请恢复原 Key 后再同步")

    with _lock:
        keys = _active_keys()
        if not keys:
            raise ValueError("请在应用设置中配置 RunningHub API Key")
        selection = CONFIG.get(SELECTION_FIELD) or AUTO
        if selection != AUTO:
            for key in keys:
                if key.alias == selection:
                    return key
            raise ValueError("指定的 RunningHub Key 不可用，请在设置中重新选择")
        key = keys[_next_key % len(keys)]
        _next_key = (_next_key + 1) % len(keys)
        return key
