"""Signed official brand information for the app UI."""
from __future__ import annotations

import base64
import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from services.context import BASE_DIR
from services.embedded_brand_assets import DOUYIN_QR_PNG_BASE64
from services.subprocess_utils import popen_platform_kwargs


BRAND_DIR = BASE_DIR / "resources" / "brand"
BRAND_INFO_PATH = BRAND_DIR / "brand_info.json"
BRAND_PUBLIC_KEY_PATH = BRAND_DIR / "brand_public_key.pem"
BRAND_NAMESPACE = "ai-cartoon-factory-brand"
BRAND_SIGNER_ID = "ai-cartoon-factory-brand"
RUNNINGHUB_ID = "2045455167452876802"
_BRAND_INFO_CACHE: tuple[float | None, dict[str, Any]] | None = None


def _canonical_payload(data: dict[str, Any]) -> bytes:
    clean = {str(key): value for key, value in data.items() if key != "signature"}
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def _verify_signature(data: dict[str, Any]) -> bool:
    signature = str(data.get("signature") or "").strip()
    if not signature:
        return False
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen or not BRAND_PUBLIC_KEY_PATH.exists():
        if getattr(sys, "frozen", False) and BRAND_INFO_PATH.exists() and BRAND_PUBLIC_KEY_PATH.exists():
            return True
        return False
    try:
        signature_bytes = base64.b64decode(signature.encode("ascii"), validate=True)
    except Exception:
        return False

    with tempfile.TemporaryDirectory(prefix="aicf_brand_") as temp:
        temp_dir = Path(temp)
        signature_path = temp_dir / "brand_payload.json.sig"
        allowed_path = temp_dir / "allowed_signers"
        signature_path.write_bytes(signature_bytes)
        public_key = BRAND_PUBLIC_KEY_PATH.read_text(encoding="ascii").strip()
        allowed_path.write_text(f"{BRAND_SIGNER_ID} {public_key}\n", encoding="ascii")
        result = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "verify",
                "-f",
                str(allowed_path),
                "-I",
                BRAND_SIGNER_ID,
                "-n",
                BRAND_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            input=_canonical_payload(data),
            capture_output=True,
            text=False,
            timeout=10,
            **popen_platform_kwargs(),
        )
        return result.returncode == 0


def load_brand_info() -> dict[str, Any]:
    global _BRAND_INFO_CACHE
    fallback = {
        "brand": "\u963f\u8d85\u7528AI",
        "signature_valid": False,
        "tampered": True,
        "status_text": "\u4f5c\u8005\u4fe1\u606f\u672a\u901a\u8fc7\u7b7e\u540d\u6821\u9a8c\uff0c\u8bf7\u5230\u5b98\u65b9\u6e20\u9053\u83b7\u53d6\u6700\u65b0\u7248\u3002",
    }
    if not BRAND_INFO_PATH.exists():
        fallback["status_text"] = "\u4f5c\u8005\u4fe1\u606f\u6587\u4ef6\u7f3a\u5931\uff0c\u8bf7\u5230\u5b98\u65b9\u6e20\u9053\u83b7\u53d6\u6700\u65b0\u7248\u3002"
        return fallback
    try:
        mtime = BRAND_INFO_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if _BRAND_INFO_CACHE and _BRAND_INFO_CACHE[0] == mtime:
        return dict(_BRAND_INFO_CACHE[1])
    try:
        data = json.loads(BRAND_INFO_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        fallback["status_text"] = "\u4f5c\u8005\u4fe1\u606f\u6587\u4ef6\u65e0\u6cd5\u8bfb\u53d6\uff0c\u8bf7\u5230\u5b98\u65b9\u6e20\u9053\u83b7\u53d6\u6700\u65b0\u7248\u3002"
        return fallback
    if not isinstance(data, dict):
        return fallback
    valid = _verify_signature(data)
    data["signature_valid"] = valid
    data["tampered"] = not valid
    if not valid:
        data["status_text"] = "\u4f5c\u8005\u4fe1\u606f\u88ab\u7be1\u6539\uff0c\u8bf7\u5230\u5b98\u65b9\u6e20\u9053\u83b7\u53d6\u6700\u65b0\u7248\u3002"
    _BRAND_INFO_CACHE = (mtime, dict(data))
    return data


def brand_trace() -> dict[str, Any]:
    info = load_brand_info()
    return {
        "brand": str(info.get("brand") or ""),
        "bilibili_name": str(info.get("bilibili_name") or ""),
        "bilibili_username": str(info.get("bilibili_username") or ""),
        "douyin": str(info.get("douyin") or ""),
        "runninghub_id": str(info.get("runninghub_id") or RUNNINGHUB_ID),
        "version": str(info.get("version") or ""),
        "signature_valid": bool(info.get("signature_valid")),
    }


def is_brand_verified() -> bool:
    return bool(load_brand_info().get("signature_valid"))


def brand_block_message() -> str:
    info = load_brand_info()
    return str(
        info.get("status_text")
        or "\u4f5c\u8005\u4fe1\u606f\u88ab\u7be1\u6539\uff0c\u8bf7\u5230\u5b98\u65b9\u6e20\u9053\u83b7\u53d6\u6700\u65b0\u7248\u3002"
    )


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _ac_icon() -> str:
    return '<div class="account-icon" aria-hidden="true">AC</div>'


def _asset_file_url(filename: str) -> str:
    path = (BRAND_DIR / str(filename or "")).resolve()
    if not path.exists():
        return ""
    path_text = str(path).replace("\\", "/")
    return f"/gradio_api/file={path_text}"


def _embedded_douyin_qr_url() -> str:
    return f"data:image/png;base64,{DOUYIN_QR_PNG_BASE64}"


def _gift_icon() -> str:
    return """
    <svg class="account-gift-icon" viewBox="0 0 96 96" role="img" aria-label="gift">
        <rect x="18" y="38" width="60" height="42" rx="10"></rect>
        <rect x="14" y="28" width="68" height="18" rx="8"></rect>
        <path d="M48 28v52M18 48h60"></path>
        <path d="M47 27c-8-2-15-7-16-13-1-5 3-9 8-8 7 1 10 10 9 21Z"></path>
        <path d="M49 27c8-2 15-7 16-13 1-5-3-9-8-8-7 1-10 10-8 21Z"></path>
    </svg>
    """


def _lines_html(value: Any, fallback: list[str]) -> str:
    lines = value if isinstance(value, list) else fallback
    return "<br>".join(_esc(item) for item in lines if str(item or "").strip())


def render_brand_card() -> str:
    info = load_brand_info()
    if not info.get("signature_valid"):
        return f"""
        <div class="official-platform-area official-platform-warning">
            <div class="account-card account-card-warning">
                {_ac_icon()}
                <div class="account-content">
                    <div class="account-title">\u5b98\u65b9\u4fe1\u606f</div>
                    <div class="account-row">{_esc(info.get("status_text"))}</div>
                </div>
            </div>
        </div>
        """

    invite_url = str(info.get("runninghub_invite_url") or "").strip()
    runninghub_text = _esc(info.get("runninghub_text") or "\u6ce8\u518c\u9001500 RH\u5e01\n\u514d\u8d39\u4f53\u9a8cAI\u751f\u6210").replace("\n", "<br>")
    runninghub_cta = _esc(info.get("runninghub_cta") or "\u7acb\u5373\u9886\u53d6")
    douyin_intro = _lines_html(
        info.get("douyin_intro"),
        ["AI\u5de5\u4f5c\u6d41", "AI\u89c6\u9891\u5236\u4f5c", "ComfyUI", "\u9879\u76ee\u5b9e\u6218\u5206\u4eab"],
    )
    bilibili_intro = _lines_html(
        info.get("bilibili_intro"),
        ["AI\u6559\u7a0b", "\u9879\u76ee\u5206\u4eab", "\u5f00\u53d1\u8bb0\u5f55"],
    )
    douyin_cta = _esc(info.get("douyin_cta") or "\u626b\u7801\u5173\u6ce8")
    bilibili_cta = _esc(info.get("bilibili_cta") or "\u6b22\u8fce\u5173\u6ce8")
    runninghub_cta_html = (
        f'<a class="account-cta" href="{_esc(invite_url)}" target="_blank" rel="noopener noreferrer">{runninghub_cta}</a>'
        if invite_url
        else f'<span class="account-cta">{runninghub_cta}</span>'
    )
    runninghub_id = _esc(info.get("runninghub_id") or RUNNINGHUB_ID)
    douyin_qr_url = _embedded_douyin_qr_url()
    douyin_visual = f'<img class="account-qr" src="{_esc(douyin_qr_url)}" alt="\u6296\u97f3\u4e8c\u7ef4\u7801" onclick="window._previewKeyImg&&window._previewKeyImg(this)">'
    return f"""
    <details class="official-platform-disclosure" open>
        <summary>
            <span class="official-platform-summary-copy">
                <strong>官方账号与创作福利</strong>
                <small>抖音 · bilibili · RunningHub</small>
            </span>
            <span class="official-platform-chevron" aria-hidden="true"></span>
        </summary>
        <div class="official-platform-area">
        <div class="account-card">
            <div class="account-card-title">\u6211\u7684\u6296\u97f3\u8d26\u53f7</div>
            <div class="account-card-body">
                <div class="account-visual account-qr-wrap">{douyin_visual}</div>
                <div class="account-content">
                    <div class="account-copy">
                        <div class="account-name">{_esc(info.get("brand"))}</div>
                        <div class="account-row"><span>\u6296\u97f3\u53f7\uff1a</span><b>{_esc(info.get("douyin"))}</b></div>
                        <div class="account-desc">{douyin_intro}</div>
                    </div>
                    <div class="account-card-footer"><span class="account-cta">{douyin_cta}</span></div>
                </div>
            </div>
        </div>
        <div class="account-card">
            <div class="account-card-title">\u6211\u7684\u0062\u0069\u006c\u0069\u0062\u0069\u006c\u0069\u8d26\u53f7</div>
            <div class="account-card-body">
                <div class="account-visual">{_ac_icon()}</div>
                <div class="account-content">
                    <div class="account-copy">
                        <div class="account-name">{_esc(info.get("bilibili_name") or "\u963f\u8d85\u7528AI")}</div>
                        <div class="account-row"><span>\u7528\u6237\u540d\uff1a</span><b>{_esc(info.get("bilibili_username"))}</b></div>
                        <div class="account-desc">{bilibili_intro}</div>
                    </div>
                    <div class="account-card-footer"><span class="account-cta">{bilibili_cta}</span></div>
                </div>
            </div>
        </div>
        <div class="account-card">
            <div class="account-card-title">AI\u521b\u4f5c\u798f\u5229</div>
            <div class="account-card-body">
                <div class="account-visual account-gift-wrap">{_gift_icon()}</div>
                <div class="account-content">
                    <div class="account-copy">
                        <div class="account-name">RunningHub ID</div>
                        <div class="account-row"><b>{runninghub_id}</b></div>
                        <div class="account-desc">{runninghub_text}</div>
                    </div>
                    <div class="account-card-footer">{runninghub_cta_html}</div>
                </div>
            </div>
        </div>
        </div>
    </details>
    """
