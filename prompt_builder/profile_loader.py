"""Load reusable prompt profiles from the project profiles directory."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def _profiles_dir() -> Path:
    return Path.cwd() / "profiles"


def _read_profile(name: str, *, required: bool = False) -> str:
    path = _profiles_dir() / name
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Profile file not found: {path}")
        return ""
    return path.read_text(encoding="utf-8-sig").strip()


def _section(title: str, body: str) -> str:
    body = str(body or "").strip()
    if not body:
        return ""
    return f"\n[{title}]\n{body}\n"


@lru_cache(maxsize=1)
def load_ltx23_profile() -> str:
    """Return the system profile for LTX2.3 / LiconMSR prompt compilation."""
    parts = [
        "[Role]\n你是一位 LTX2.3 / LiconMSR Prompt Designer。\n",
        _section(
            "Knowledge: Writing Philosophy",
            _read_profile("ltx23_prompt_profile_v1.md", required=True),
        ),
        _section("Knowledge: Good Examples", _read_profile("ltx23_examples.md")),
        _section("Knowledge: Bad Cases To Avoid", _read_profile("ltx23_bad_cases.md")),
        _section("Knowledge: Changelog", _read_profile("ltx23_changelog.md")),
        """
[Task]
根据当前 PromptIR 生成自然、可拍摄、稳定的 LTX2.3 / LiconMSR 导演 Prompt。
必须遵守上面的写作哲学。
优秀案例只作为风格参考。
失败案例用于避免重复错误。
""".strip(),
    ]
    return "\n".join(part.strip() for part in parts if str(part or "").strip()).strip()


def load_beat_writer_profile(
    base_prompt: str,
    policy_prompt: str = "",
    *,
    profile_name: str = "beat_writer_profile.md",
) -> str:
    """Return a model-owned beat writer prompt with an optional override.

    ``beat_writer_profile.md`` remains the existing LTX override.  A model
    that has materially different prompt semantics can opt into its own file
    (for example ``h3_beat_writer_profile.md``) instead of inheriting Licon
    instructions by accident.
    """
    profile = _read_profile(profile_name) or str(base_prompt or "").strip()
    policy = str(policy_prompt or "").strip()
    return "\n\n".join(part for part in (profile.strip(), policy) if part).strip()


def load_story_writer_profile(base_prompt: str, *, profile_name: str = "story_writer_profile.md") -> str:
    """Return the story writer system prompt, with an optional project override."""
    return (_read_profile(profile_name) or str(base_prompt or "").strip()).strip()
