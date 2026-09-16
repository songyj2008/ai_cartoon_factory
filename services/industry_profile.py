"""Load optional project-level industry context for LLM prompts."""

import json
from pathlib import Path

from services.context import BASE_DIR, get_current_project_name
from services.logger import log


def _project_name(project_name=None):
    name = str(project_name or get_current_project_name() or "current").strip()
    return Path(name).name or "current"


def load_industry_profile(project_name=None):
    """Return a project's industry profile, or an empty dict on any failure."""
    name = _project_name(project_name)
    profile_path = BASE_DIR / "projects" / name / "industry_profile.json"
    if not profile_path.exists():
        return {}

    try:
        data = json.loads(profile_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            log(
                f"[industry_profile][warning] {profile_path} 必须是 JSON 对象，已忽略",
                "WARNING",
            )
            return {}
        return data
    except json.JSONDecodeError as exc:
        log(
            f"[industry_profile][warning] {profile_path} JSON 格式错误，已忽略: {exc}",
            "WARNING",
        )
    except Exception as exc:
        log(
            f"[industry_profile][warning] 读取 {profile_path} 失败，已忽略: {exc}",
            "WARNING",
        )
    return {}


def industry_profile_prompt(profile):
    """Format a non-empty profile as a reusable LLM prompt block."""
    if not isinstance(profile, dict) or not profile:
        return ""
    return (
        "当前项目行业设定：\n"
        + json.dumps(profile, ensure_ascii=False, indent=2)
        + "\n\n"
        "行业生成要求：\n"
        "- 剧情围绕行业目标、真实业务流程、岗位协作、决策逻辑和行业冲突展开。\n"
        "- 角色身份、工作空间、当前任务和岗位关系必须符合行业设定。\n"
        "- 对白至少约 30% 自然体现岗位职责、术语、流程、风险或决策，像真实从业人员交流。\n"
        "- 业务风险和规则背景优先由角色对白自然表达；除非用户明确要求旁白，否则不要使用旁白。\n"
        "- 遵守 avoid_topics，避免泛娱乐、家庭矛盾、爱情主线和无行业背景闲聊。\n"
        "- 使用 keywords 时必须服务于剧情，不要机械堆砌或背诵术语。\n"
    )
