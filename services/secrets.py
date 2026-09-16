"""Runtime secret storage backed by environment variables."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
import uuid
from pathlib import Path
from typing import Iterable


DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
RUNNINGHUB_API_KEY_ENV = "RUNNINGHUB_API_KEY"
SECRET_PREFIX = "aicfenc:v1:"
SECRET_SALT = b"ai-cartoon-factory:env-secret:v1"


class SecretBindingError(ValueError):
    """Raised when a stored secret cannot be decrypted on this machine."""


def _machine_mac_id() -> str:
    node = uuid.getnode()
    return f"{node:012x}"


def _machine_key() -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        _machine_mac_id().encode("ascii"),
        SECRET_SALT,
        200_000,
        dklen=32,
    )


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        counter_bytes = counter.to_bytes(8, "big")
        chunks.append(hmac.new(key, nonce + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:length]


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _encrypt_for_machine(value: str) -> str:
    plain = str(value or "").encode("utf-8")
    key = _machine_key()
    nonce = os.urandom(16)
    cipher = _xor_bytes(plain, _keystream(key, nonce, len(plain)))
    tag = hmac.new(key, b"aicf-secret-v1" + nonce + cipher, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")
    return SECRET_PREFIX + payload


def _decrypt_for_machine(stored: str, name: str) -> str:
    text = str(stored or "").strip()
    if not text:
        return ""
    if not text.startswith(SECRET_PREFIX):
        raise SecretBindingError(f"{name} was saved in an old/plain format. Please reconfigure it in UI settings.")
    try:
        raw = base64.urlsafe_b64decode(text[len(SECRET_PREFIX):].encode("ascii"))
        nonce = raw[:16]
        tag = raw[16:48]
        cipher = raw[48:]
        key = _machine_key()
        expected = hmac.new(key, b"aicf-secret-v1" + nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise SecretBindingError(f"{name} does not match this machine. Please reconfigure it in UI settings.")
        plain = _xor_bytes(cipher, _keystream(key, nonce, len(cipher)))
        return plain.decode("utf-8").strip()
    except SecretBindingError:
        raise
    except Exception as exc:
        raise SecretBindingError(f"{name} cannot be decrypted. Please reconfigure it in UI settings.") from exc


def _linux_user_env_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "environment.d" / "ai-cartoon-factory.conf"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = value.strip()
    except Exception:
        return {}
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not values:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    lines = [f"{key}={value}" for key, value in sorted(values.items()) if key and value]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


def _linux_user_env_get(name: str) -> str:
    if not sys.platform.startswith("linux"):
        return ""
    return _read_env_file(_linux_user_env_path()).get(name, "").strip()


def _linux_user_env_set(name: str, value: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    values = _read_env_file(_linux_user_env_path())
    values[name] = str(value or "")
    _write_env_file(_linux_user_env_path(), values)


def _linux_user_env_delete(name: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    path = _linux_user_env_path()
    values = _read_env_file(path)
    values.pop(name, None)
    _write_env_file(path, values)


def _windows_user_env_get(name: str) -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _type = winreg.QueryValueEx(key, name)
            return str(value or "").strip()
    except Exception:
        return ""


def _windows_user_env_set(name: str, value: str) -> None:
    if not sys.platform.startswith("win"):
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value or ""))


def _windows_user_env_delete(name: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return
    except OSError:
        return


def _windows_broadcast_env_change() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        smto_abortifhung = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            hwnd_broadcast,
            wm_settingchange,
            0,
            "Environment",
            smto_abortifhung,
            5000,
            None,
        )
    except Exception:
        return


def secret_value(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return _decrypt_for_machine(value, name)
    value = _windows_user_env_get(name)
    if not value:
        value = _linux_user_env_get(name)
    if value:
        os.environ[name] = value
        return _decrypt_for_machine(value, name)
    return ""


def secret_is_configured(name: str) -> bool:
    try:
        return bool(secret_value(name))
    except SecretBindingError:
        return False


def set_secret(name: str, value: str) -> None:
    value = str(value or "").strip()
    if not value:
        return
    encrypted = _encrypt_for_machine(value)
    os.environ[name] = encrypted
    _windows_user_env_set(name, encrypted)
    _linux_user_env_set(name, encrypted)
    _windows_broadcast_env_change()


def clear_secret(name: str) -> None:
    os.environ.pop(name, None)
    _windows_user_env_delete(name)
    _linux_user_env_delete(name)
    _windows_broadcast_env_change()


def clear_secrets(names: Iterable[str]) -> None:
    for name in names:
        clear_secret(str(name))


def masked_secret(name: str) -> str:
    try:
        return "***" if secret_value(name) else ""
    except SecretBindingError:
        return "需重新配置"
