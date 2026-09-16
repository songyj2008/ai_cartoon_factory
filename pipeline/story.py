"""Story generation from topic using LLM."""
import json
import traceback

from services.logger import log, get_logs
from services.file_utils import save_json
from prompt_builder.profile_loader import load_story_writer_profile
from services.project_bible import ensure_project_bible, project_bible_prompt
from services.industry_profile import load_industry_profile, industry_profile_prompt
from services.context import get_project_dir
from asset_index import load_asset_index


def generate_story(topic, duration_sec=60, model_id=None):
    """Generate a full story JSON from a topic string. Uses call_llm from app (lazy import).

    duration_sec: target duration in seconds (30/60/90/120 or custom).
    Controls story complexity, scene count, and pacing.
    """
    from services.llm import call_llm

    try:
        topic = (topic or "").strip()
        duration_sec = int(duration_sec or 60)
        from generation.model_rules import rules_for_model

        model_rules = rules_for_model(model_id)
        if not topic:
            raise ValueError("主题为空")

        log("=" * 60)
        log(f"第一步开始：主题 -> 完整剧情，主题：{topic}，时长：{duration_sec}s")
        bible = ensure_project_bible(topic=topic, asset_index=load_asset_index())
        bible_text = project_bible_prompt(bible)
        industry_profile = load_industry_profile(get_project_dir().name)
        industry_text = industry_profile_prompt(industry_profile)

        system_prompt = f"""
你是短剧编剧。

目标时长：约 {duration_sec} 秒。

请根据时长控制剧情复杂度：
- 30s：2~4 个主要剧情状态
- 60s：4~7 个主要剧情状态
- 90s：6~9 个主要剧情状态
- 120s：8~12 个主要剧情状态

默认采用通用 AI 视频短剧模式：以角色台词推动剧情，按镜头状态组织画面。不要把规则写死到某一个场景；故事可以跨多个场景，但每个剧情状态必须有清晰的当前背景、当前角色和当前对白/动作。

【核心剧情锁定，最高优先级】
- 用户主题是事实边界，不是灵感素材。不得改写核心事件、因果、结局方向或人物目标。
- 不得新增与主题无关的重大任务、外部使命、救灾/悬疑/逆袭支线，除非用户主题明确包含。
- 如果主题包含“辞职/投诉/离开/维权”等目标，结局必须围绕该目标推进，不得改成被挽留接新任务。
- 可以补充细节，但补充只能服务原主题，不能替换原主题。

- 对白、动作、环境和镜头共同服务剧情；不要用无信息量语气词或机械动作口令凑内容。
- 不新增未注册角色参与对白或推动剧情；背景群众不得承担明确身份和核心剧情。

【当前视频模型】
- {model_rules.label}（{model_rules.model_id}）
- 规则版本：{model_rules.revision}

{model_rules.story_constraints}

如果提供了行业设定，剧情必须优先表现真实业务事件、工作流程、岗位协作、
行业风险和决策逻辑。对白至少约 30% 自然体现业务知识，业务信息尽量由角色台词表达。
禁止机械堆砌术语或写成脱离行业的通用电视剧。

【必须严格遵守的JSON格式规则】
- 只输出纯JSON，不要任何解释、不要Markdown、不要代码块
- 所有字段名和字符串值必须用英文双引号 "
- 每个字段之间必须使用英文逗号 , 分隔
- 特别注意：最后一个字符串字段（如story）结束后也必须加英文逗号（如果后面还有字段）
- 字符串内部不要输出真实换行，如需换行请使用 \\n
- 字符串内部如有英文双引号，必须转义为 \\"

输出格式：
{{
  "title": "剧名",
  "summary": "一句话梗概",
  "story": "完整剧情（300字以内，内部无真实换行）",
  "main_conflict": "核心冲突",
  "ending": "结局"
}}
"""

        user_prompt = f"""
根据主题生成一个完整短剧剧情JSON。

主题：
{topic}

目标时长：约 {duration_sec} 秒

要求：
1. 接地气，适合短视频。
2. 有起因、冲突、转折、结尾。
3. 适合后续拆成多个剧情段落。
4. 不要直接拆guide。
5. story控制在300字以内。
6. 不要使用英文双引号包裹台词，避免JSON转义问题。
7. 根据时长 {duration_sec}s 调整剧情密度：短时长精简场景，长时长丰富细节。
8. 根据当前模型规则平衡对白、动作、镜头和声音；每个剧情状态都要有可见变化或有效信息推进。
9. 有对白时必须明确说话人，并让对白承担冲突、解释、推进或信息披露作用。
10. 禁止用无意义语气词、机械动作口令或无剧情价值的过渡内容凑时长。
11. 严格保留用户主题中的核心剧情目标、因果关系和结局方向；不要新增会改变主线的外部任务或支线。

【JSON格式提醒】
- 只输出纯JSON，不要markdown代码块
- 所有字段用英文双引号
- 字段之间必须有英文逗号
- story字段结束后必须加英文逗号
- 字符串内不要有真实换行

{bible_text}

{industry_text}
"""

        system_prompt += "\n只输出一个完整 JSON 对象，不要 markdown，不要代码块，不要解释文字。"
        user_prompt += "\n只输出一个完整 JSON 对象，不要 markdown，不要代码块，不要解释文字。"
        system_prompt = load_story_writer_profile(system_prompt, profile_name=model_rules.story_writer_profile_name)
        data, cost = call_llm(system_prompt, user_prompt, max_tokens=3200, label="story")
        data["target_duration_sec"] = duration_sec
        data["generation_policy"] = {"model_id": model_rules.model_id, "rules_revision": model_rules.revision}
        save_json("story.json", data)

        log(f"第一步完成：标题={data.get('title', '')}，耗时 {cost} 秒")
        return json.dumps(data, ensure_ascii=False, indent=2), get_logs()

    except Exception as e:
        traceback.print_exc()
        log(str(e), "ERROR")
        return "", get_logs()
