from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.events import OFFSCREEN_SPEECH_TYPES, ensure_event_fields_on_beats
from generation.beat_policy import beat_policy_prompt, project_beat_policy
from generation.model_rules import rules_for_model
from prompt_builder.profile_loader import load_beat_writer_profile
from services.generation_policy import max_important_visible_roles
from services.llm import call_deepseek_chat_sdk


MIN_VIDEO_SEGMENT_SEC = 15
MIN_TARGETED_VIDEO_SEGMENT_SEC = 8
MAX_VIDEO_SEGMENT_SEC = 30


BEATS_SYSTEM_PROMPT = """
你是影视短剧编剧，也是 LiconMSR Direct 视频生成的结构规划器。

目标：
把输入 story/topic 拆成可直接生成视频的 beats。每个 beat 会直接编译成一个完整 LiconMSR 视频 prompt。

最重要的契约：
不要从 plot 里猜谁入画。每个 beat 必须显式输出角色可见性计划。

顶层 JSON 必须包含非空 locked_outcome 字段，用一句话锁定故事结局方向；不得省略。

每个 beat 必须包含：
- id
- title
- plot
- scene_id：必须来自 Project Bible allowed_backgrounds 的 id 或 alias；无法确定时用 ""。
- important_roles：本 beat 相关的注册角色总表，必须包含 visible_roles、reference_roles、offscreen_speakers、mentioned_roles 中出现的所有角色。
- visible_roles：真实出现在镜头里的注册角色。
- reference_roles：需要放入 LiconMSR 80-83 参考图槽的角色，必须是 visible_roles 的子集。
- offscreen_speakers：电话、门外、广播、对讲机、画外声音、屏幕外声音等说话者，不入画。
- mentioned_roles：只被提到、只作为所有物/地点/文件/办公室/电话对象出现的角色，不入画。
- dialogue_units：可选辅助结构；台词主来源是 plot 的自然中文描述。
- action_units：可选辅助结构；动作主来源是 plot 的自然中文描述。
- event_units：推荐辅助结构；用于描述 LiconMSR Event Flow，按顺序输出“角色动作 -> 独立对白 -> 新动作/环境事件”。
- identity_constraints
- background_extras
- estimated_duration_sec

入画规则：
- important_roles 必须是角色总表：visible_roles、reference_roles、offscreen_speakers、mentioned_roles 的并集都必须包含在 important_roles 内。
- 只有真实出现在镜头里的角色才能进 visible_roles/reference_roles。
- 每个 beat 的 visible_roles 是整段固定可见角色集合；同一个角色不能在同一 beat 内先作为画外声音再作为入画角色。
- 如果角色先在电话/门外/画外说话，随后又入画，必须拆成两个 beat：前一 beat 该角色只进 offscreen_speakers，后一 beat 该角色进 visible_roles/reference_roles。
- target_role 默认不入画；除非它也明确列入 visible_roles。
- offscreen_speakers 不得出现在 visible_roles/reference_roles。
- mentioned_roles 不得出现在 visible_roles/reference_roles。
- background_extras 只能是匿名远景群众，不能有注册身份、不能说话、不能互动、不能驱动剧情。
- reference_roles 数量必须 <= runtime limit，且最多 4。
- 如果一个段落需要更多清晰入画角色，或角色可见性发生变化，拆成多个 beat。

画外和提及示例规则：
- “老李电话里说”“老李声音从门外传来”“广播里传来老李声音” -> 老李进 offscreen_speakers，不进 visible_roles/reference_roles。
- “提到老李”“去老李办公室”“拿老李文件”“给老李打电话” -> 老李进 mentioned_roles，不进 visible_roles/reference_roles。
- “老李走进办公室，坐下说话” -> 老李进 visible_roles/reference_roles。

dialogue_units 规则：
- dialogue_units 是辅助字段；plot 仍保持自然中文描述，但每一句有效对白都必须在 dialogue_units 中有一条对应记录。
- 默认每个 beat 必须至少包含 1-2 句有效角色对白。
- 除非用户主题明确要求默片、无对白、纯动作、监控画面、广告展示或环境空镜，否则不要生成全程无台词 beat。
- 发现信息、做决定、产生危机、转折钩子，都必须通过当前可见角色的台词表达出来。
- 每条 dialogue_units 必须包含 speaker_name, line, speech_type, visible, target_role。
- speaker_name 必须是注册角色 id，不要使用中文名；line 只写台词正文，不要带引号。
- 如果某条 dialogue_units 缺少 speaker_name 或 line，本地代码会忽略该条，因此不要省略。
- speech_type 可以是 onscreen, offscreen_phone, offscreen, voice_over, intercom, broadcast, outside_door。
- visible=true 仅当 speaker 在 visible_roles。
- visible=false 时 speaker 应在 offscreen_speakers 或 mentioned_roles。

action_units 规则：
- action_units 是可选辅助字段，不是视频 prompt 的动作主来源。
- 动作主来源是 plot 的自然中文描述；不要使用“画面：”字段标题。
- 如果输出 action_units，每条建议包含 role, action, target_role, object, motion_scale, timing, visibility。
- 如果某条 action_units 缺少 role 或 action，本地代码会忽略该条，不应影响 beat 生成。
- role 必须在 visible_roles，visibility 必须是 onscreen。
- 不要为 offscreen_speakers 或 mentioned_roles 生成 onscreen action。

event_units 规则：
- event_units 是最高优先级辅助字段，用于让后续 prompt 编译器稳定生成 LiconMSR Event Flow。
- 每条 event_units 必须包含 type, actor, action, dialogue, target_role, object。
- type 可用 speech_event、silent_action、environment_event。
- 每一句有效对白都必须对应一条 speech_event；不要只把对白写在 plot 里。
- speech_event 必须有 actor、action、dialogue；actor 必须在 visible_roles；dialogue 只写台词正文，不要带引号。
- speech_event.action 必须包含说话前的表演节拍，例如看向对方、低头、吸气、停顿、抬手、指向道具、调整姿态。
- 不要把多个角色台词合并到同一条 speech_event。
- silent_action 必须有 actor、action，不要有 dialogue；actor 必须在 visible_roles。
- environment_event 不需要 actor，但必须有 action；用于电脑刷新、手机震动、文件滑动、灯光变化、设备提示等可见反馈。
- event_units 必须按视频发生顺序排列，并在 2-3 轮对白之间插入自然的 environment_event 或 silent_action。
- event_units 只能表达 beat 内已有剧情，不得新增人物、地点、支线或额外台词。

【Stable Initial State 第一帧稳定状态规则（最高优先级）】
- 任何 Beat 的第一帧都必须是稳定状态，不是事件切换过程。
- 第一帧人物必须全部已在场；visible_roles / reference_roles 中的所有角色必须从第一帧就已经出现在画面中，并保持身份连续。
- 第一帧人物位置必须已经确定，不能靠进入、离开、落座、起身、推门、走入、走出等过渡动作来完成布局。
- Beat 只描述当前事件，不描述事件切换过程。
- 画面中的主要人物数量必须严格等于 visible_roles（或 reference_roles）中的人物数量。
- 视频生成过程中禁止自动新增任何未注册主要人物。
- 不要生成主要人物从画外进入、推门进入、走入镜头、从拐角出现、落座、起身、走出画面、离开房间等过渡过程。
- 主要人物的位置变化只能发生在画面内部，不得通过新增人物完成。
- 任何具有对白、推动剧情、具有明确身份或参与主要动作的角色，都必须从第一帧开始已经在画面内，不允许中途入画。
- 如剧情需要后续有人进入画面，该人物只能属于 background_extras。
- background_extras 不属于剧情角色，没有参考图，没有固定身份，不参与角色绑定。
- background_extras 只能作为远景、侧景或模糊经过的人群存在，不允许成为画面主体。

Beat 拆分原则：
- 一个 Beat 必须是一个完整、稳定、能够独立生成的视频事件，而不是人物移动、镜头衔接或剧情过渡。
- 每个 Beat 必须有明确主体、明确事件，并且人物关系、空间关系和镜头主体保持稳定。
- 每个 Beat 都应推动剧情发展，而不仅仅连接前后剧情。
- 不要为了表现人物移动、进门、出门、走路、转身等过程而拆分 Beat。
- 不要单独生成“某人已离开/已不在画面内/离开后的空场反应/目送画面外方向”这类结果状态 Beat；这类信息只能合并进上一 Beat 的结尾语义，或直接跳到下一场新的稳定剧情事件。
- 如角色可见性发生变化，应优先切到下一个有明确剧情推进的新场景/新事件，而不是生成空场、目送、余波、沉默反应 Beat。
- AI 视频更适合生成稳定、完整且有剧情推进的事件；拆分 Beat 时优先保证每个 Beat 有独立剧情价值。

beat 的 plot 写法要求：
- 一个 story 必须拆成多个 beat；每个 beat 代表一个稳定、完整、可直接生成的视频事件。
- plot 是给视频模型看的自然中文视频提示词，不再使用传统分镜字段。
- plot 必须采用 LiconMSR Event Flow 写法：环境建立 -> 角色动作/表演节拍 -> 角色自然说出对白 -> 说完后的恢复动作 -> 新角色动作或环境事件。
- 禁止把 plot 写成“0-5秒、5-10秒、10-15秒”的时间轴对白格式。
- 禁止输出“画面：”“光线：”“音效：”“台词：”“字幕：”等字段标题。
- 禁止把进入、离开、走入、走出、来到、推门、开门、关门、落座、起身、转身、走廊、门口作为独立 beat 或主要动作。
- Beat 标题也必须遵守 Stable Initial State，不要写“角色进入某地”“角色离开某地”“角色走进/走出/推门/起身/落座”等过渡标题。
- 如果剧情需要表达离开，不要单独创建离开后的空场 Beat；只在上一 Beat 结尾用一句话交代“老张决定离开”，随后直接切到下一场稳定事件。
- 禁止输出“已不在画面内”“离开后的稳定结果状态”“目送画面外方向”“没有人追出门”等空场余波描述。
- 默认每个 beat 必须至少包含 1-2 句有效角色对白；只有用户主题明确要求无对白表达时，才允许写“全程无台词，所有人物闭口”。
- 音效不单独成段；只有会影响画面动作时，才合并进自然描述。
- plot 必须用一段或数段自然中文描述镜头、空间、人物位置、动作、对白归属和背景状态。
- 多人对白时，每句对白前必须先写当前说话角色的明确可见动作；每句对白后必须写自然恢复动作、对方反应或环境/道具事件。
- plot 中允许用自然电影语言把对白嵌入角色动作句，例如“小刘指向屏幕，停顿片刻，语气急促却克制地说：‘老张，22点有团雾，不能走。’”
- 不要只输出裸引号对白行；不要让台词脱离说话角色动作。
- 禁止使用“继续说道、坚持说道、最后说道、提高声音说道、反驳道、说完后闭口、动嘴：”等容易导致串词或拖嘴型的导演解释词；可以使用“停顿片刻、轻轻吸气、话音落下后、办公室安静下来”等自然表演节拍。
- 每 2-3 轮对白建议插入一个自然的环境/道具事件，例如手机震动、电脑刷新、文件滑动、灯光变化、设备提示、订单纸被推到桌边等。
- plot 是给视频模型看的直接提示词，不要写结构解释，不要写字段说明，不要写节点编号。

Beat 内部运动量要求：
- 每个 beat 是一个连续视频事件，不要求固定 15 秒，时长由剧情节奏和 estimated_duration_sec 决定。
- 无论 beat 是 8 秒、15 秒、20 秒还是 30 秒，都不能只写站桩对白。
- 每个 beat 的 plot 必须写成“动作连续发生的视频事件”，不是“对白摘要”；台词只是事件的一部分，不能成为唯一内容。
- 每句台词前后必须绑定动作、视线、手势、道具或环境反应。
- 不说话的清晰角色也必须有反应：看向某物、低头、握拳、停顿、后退半步、点头、移开视线、按住文件、攥紧钥匙等。
- 每个 beat 至少包含一种道具或环境变化：手机震动、电脑弹窗、灯光变化、文件滑动、钥匙碰响、窗外车灯扫过、设备提示音、订单纸被推到桌边等。
- 镜头可以缓慢推近、轻微横移、轻微跟随、轻微俯仰，但不要频繁切镜。
- 结尾必须落到一个清晰的新状态、情绪变化或决定性动作，不能停在无动作等待。

按时长安排事件密度：
- 短 beat，约 8-12 秒：至少 1 个镜头轻微运动、1 个明确人物动作、1 个道具或环境反馈；台词必须和动作同时发生，结尾必须有一个情绪或决定变化。
- 中等 beat，约 13-20 秒：至少 1 个镜头运动、2 个连续人物动作、1 个道具状态变化、1 个不说话角色的反应动作；必须有“开场状态 -> 冲突/信息变化 -> 短暂停顿 -> 新决定”的递进。
- 长 beat，约 21-30 秒：至少 2 个阶段性动作变化、2 个道具/环境反馈、1 次情绪转折或权力关系变化；说话角色和闭口角色都要持续有细微反应，结尾必须落到清晰新状态。
- estimated_duration_sec 越长，plot 内部变化越要丰富；不要把长 beat 写成几句台词后静止等待。

通用视频事件丰满度规则：
- 这是通用 AI 视频生成工具，不要固定生成物流、办公室、老板、司机、便利店、末日等单一题材；必须根据输入故事、Project Bible 和行业设定自适应。
- 每个 beat 都要围绕“一个清晰事件目标”组织，而不是围绕台词轮次组织。
- 每个 beat 应尽量包含一个可见核心道具或环境对象，并让它参与剧情：文件、手机、电脑屏幕、钥匙、货单、工具、商品、展示盒、机器、车辆、门禁、灯光、警报、地图、证据照片等，按当前故事选择，不要硬套示例。
- plot 要有连续递进：开场稳定状态 -> 角色主动动作 -> 对方反应或环境反馈 -> 信息/情绪变化 -> 结尾决定或新状态。
- 道具或环境反馈可以是闪烁、震动、提示音、屏幕弹窗、灯光变化、物体轻微移动、纸张滑动、设备响起、窗外光线扫过等；选择与当前场景自然匹配的反馈。
- 角色动作要小而明确，优先使用不破坏首帧稳定的动作：前倾、抬手、指向、按住桌面/柜台、握紧物件、推近文件、看向屏幕、短暂停顿、移开视线、点头、松开手、把物件放到桌面中央。
- 不要依赖“走入画面、离开画面、推门进入、走向远处”来制造动态；需要表达移动意图时，用“看向出口、拿起物件、转身准备、视线转移、身体微微侧向”等画面内动作替代。
- 如果用户给了丰满示例，只学习它的结构：镜头运动、人物动作、道具异常、环境反馈、情绪递进、结尾决定；不要复制示例的题材、人物、道具或具体剧情。

plot 格式参考，只学习格式，不复制剧情内容：
中景稳定镜头，办公室调度区，冷白荧光灯。镜头从桌面散开的物流单缓慢推近到两人之间；小刘站在桌后，双手把文件重重按在桌面上，纸张向老张方向滑开；老张站在桌前，身体绷紧，手指下意识攥住车钥匙。背景同事只是模糊远景，不参与剧情。全程无台词，所有人物闭口，最后几秒打印机提示灯闪了一下，压迫感继续升高。

中景稳定镜头，调度办公室，镜头从电脑屏幕上的预警区域缓慢推近到小刘和老张之间。小刘站在电脑前，右手指向屏幕上的红色预警区域，停顿片刻，语气急促却克制地说：“老张，22点有团雾，不能走！”话音落下后，小刘的手指仍停在屏幕旁，目光观察老张的反应。老张低头看了一眼电脑屏幕，轻轻吸了一口气，随后抬起头，向前迈出半步，右手挥了一下，声音坚定地回答：“我跑这条线十年了，没问题！”话音落下后，老张慢慢放下右手，仍站在原来的位置。电脑屏幕上的气象云图缓缓刷新一次。小刘重新抬起右手，轻轻点了一下屏幕，停顿一拍，语气更加严肃地说：“系统已经预警，天亮再走。”话音落下后，小刘把手从屏幕前慢慢收回。老张抬起右手，指向门外运输方向，声音明显提高地说：“客户要求24小时送达，不能等。”最后几秒，老张慢慢放下右手，小刘仍站在电脑前，两人互相注视，办公室安静下来。

中景稳定镜头，李总办公室，老张和李总第一帧已经面对面在场。镜头从办公桌上的工牌和车钥匙缓慢推近，老张把两样东西放到桌面中央，金属钥匙轻轻碰响。\n“李总，我辞职。”\n李总坐在桌后抬头，手里的笔停在文件上。\n“到底怎么回事？”\n老张手掌按在桌沿，看了一眼桌面的车钥匙。\n“我被小刘骂了半年，今天忍不下去了。”\n李总低头看工牌，又抬眼看老张。最后几秒，两人的视线都落在桌面的车钥匙上，重点进入下一场新的剧情事件，而不是表现离开过程或空场余波。

输出必须是 JSON object，不要 Markdown。

顶层输出格式必须严格类似：
{
  "title": "故事标题",
  "locked_outcome": "一句话锁定最终结局，例如：老张因长期被辱骂而决定离职，李总开始追责小刘。",
  "target_duration_sec": 60,
  "beats": []
}
locked_outcome 必须存在且不能为空；beats 必须是非空数组。
"""


def log(message, level="STEP"):
    print(str(message), flush=True)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_temp_dir(output_file):
    temp_dir = Path(output_file).resolve().parent / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def extract_json(text):
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("LLM response does not contain a JSON object")
    return json.loads(match.group(0))


def build_user_payload(args):
    if args.input:
        payload = load_json(args.input)
    else:
        payload = {"topic": args.topic or ""}
    if args.target_duration_sec:
        payload["target_duration_sec"] = int(args.target_duration_sec)
    if getattr(args, "target_beat_count", None):
        payload["target_beat_count"] = int(args.target_beat_count)
    return payload


def _normalise_target_beat_count(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        count = int(float(value))
    except Exception:
        return None
    return count if count > 0 else None


def _unique_text(values):
    result = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _beat_writer_base_prompt(policy) -> str:
    return rules_for_model(policy.model_id).beat_writer_prompt or BEATS_SYSTEM_PROMPT


def _beat_writer_profile_name(policy) -> str:
    return rules_for_model(policy.model_id).beat_writer_profile_name


def _target_count_range(target_duration_sec: int, policy=None) -> tuple[int, int]:
    policy = policy or project_beat_policy()
    target = max(policy.min_duration_sec, int(target_duration_sec or 60))
    minimum = max(1, math.ceil(target / policy.max_duration_sec))
    maximum = max(minimum, math.floor(target / policy.min_duration_sec))
    return minimum, maximum


def _clone_split_beat(beat: dict[str, Any], copy_index: int, copy_count: int) -> dict[str, Any]:
    item = dict(beat)
    item["title"] = f"{beat.get('title') or '剧情段落'} {copy_index}/{copy_count}"
    return item


def fit_beats_to_segment_count(beats, target_duration_sec, target_beat_count=None, policy=None):
    policy = policy or project_beat_policy()
    if not isinstance(beats, list) or not beats:
        raise ValueError("DeepSeek output must contain non-empty beats array")
    target_count = _normalise_target_beat_count(target_beat_count)
    if target_count:
        average = int(target_duration_sec or 60) / target_count
        preferred_min = policy.targeted_min_duration_sec
        effective_min = policy.min_duration_sec if policy.min_duration_sec <= average < preferred_min else preferred_min
        if average < effective_min or average > policy.max_duration_sec:
            raise ValueError(
                f"target_beat_count={target_count} is incompatible with target_duration_sec={target_duration_sec}; "
                f"average segment duration must be {effective_min}-{policy.max_duration_sec}s"
            )
        selected = [beat for beat in beats if isinstance(beat, dict)]
        if len(selected) != target_count:
            raise ValueError(f"DeepSeek output beat count {len(selected)} does not match target_beat_count={target_count}")
        return selected
    minimum, maximum = _target_count_range(int(target_duration_sec or 60), policy=policy)
    selected = [beat for beat in beats if isinstance(beat, dict)]
    if len(selected) > maximum:
        selected = selected[:maximum]
    while len(selected) < minimum:
        source = selected[-1] if selected else beats[0]
        selected.append(_clone_split_beat(source, len(selected) + 1, minimum))
    return selected


def distribute_seconds(total_sec, count, source_durations, min_segment_sec=MIN_VIDEO_SEGMENT_SEC, max_segment_sec=MAX_VIDEO_SEGMENT_SEC):
    total_sec = int(total_sec or 60)
    count = max(1, int(count or 1))
    min_segment_sec = max(1, int(min_segment_sec or MIN_VIDEO_SEGMENT_SEC))
    max_segment_sec = max(min_segment_sec, int(max_segment_sec or MAX_VIDEO_SEGMENT_SEC))
    durations = []
    for value in source_durations[:count]:
        try:
            duration = int(round(float(value)))
        except Exception:
            duration = min_segment_sec
        durations.append(max(min_segment_sec, min(max_segment_sec, duration)))
    while len(durations) < count:
        durations.append(min_segment_sec)

    current = sum(durations)
    while current < total_sec:
        changed = False
        for index in range(count):
            if durations[index] < max_segment_sec and current < total_sec:
                durations[index] += 1
                current += 1
                changed = True
        if not changed:
            break
    while current > total_sec:
        changed = False
        for index in range(count - 1, -1, -1):
            if durations[index] > min_segment_sec and current > total_sec:
                durations[index] -= 1
                current -= 1
                changed = True
        if not changed:
            break
    if sum(durations) != total_sec:
        raise ValueError(f"cannot distribute {total_sec}s into {count} segments of {min_segment_sec}-{max_segment_sec}s")
    return durations


def beat_structure_policy_prompt(policy=None):
    policy = policy or project_beat_policy()
    model_rules = rules_for_model(policy.model_id)
    if model_rules.beat_structure_prompt:
        return model_rules.beat_structure_prompt
    role_limit = model_rules.max_visible_roles or min(max_important_visible_roles(), 4)
    return f"""

Runtime limits:
- reference_roles length must be <= {role_limit}.
- visible_roles length should be <= {role_limit}; split the beat if more clear in-frame roles are needed.
- important_roles must contain every role from visible_roles, reference_roles, offscreen_speakers, and mentioned_roles.
- important_roles may include visible roles, offscreen speakers, and mentioned roles, but only reference_roles get images.
- background_extras must always be present; use [] when there are no extras.
"""


def beat_count_policy_prompt(target_duration_sec=None, target_beat_count=None, policy=None):
    policy = policy or project_beat_policy()
    count = _normalise_target_beat_count(target_beat_count)
    if not count:
        return ""
    total = int(target_duration_sec or 60)
    average = total / count
    return f"""

Target beat count:
- Output exactly {count} beats. Do not output fewer or more.
- Total target duration is {total}s, so each beat should average about {average:.1f}s.
- The sum of all estimated_duration_sec values must equal exactly {total}s.
- Short beats of {policy.targeted_min_duration_sec}-{min(12, policy.max_duration_sec)}s are allowed when target_beat_count is specified.
- Do not create empty transition, walking, entering, leaving, or aftermath beats just to reach the count.
- If the story currently feels like fewer events, split the richest events into smaller complete video events with clear dramatic value.
- If the source contains more events than the target count, merge adjacent cause-and-effect moments into one complete beat instead of dropping story turns.
"""


def _normalize_beat_extras(values, beat_index):
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"Beat {beat_index} background_extras must be an array")
    normalized = []
    for extra_index, value in enumerate(values, start=1):
        item = value if isinstance(value, dict) else {"description": value}
        if any(item.get(key) for key in ("role_id", "asset_identity_id", "reference_image", "speaker_role")):
            raise ValueError(f"Beat {beat_index} background extra {extra_index} must not own a registered identity")
        if item.get("speaking") is True or item.get("interacting") is True:
            raise ValueError(f"Beat {beat_index} background extra {extra_index} must be silent and non-interacting")
        description = str(item.get("description") or item.get("appearance") or "anonymous background people").strip()
        normalized.append(
            {
                "description": description,
                "clarity": "low",
                "occupancy": "small",
                "speaking": False,
                "interacting": False,
            }
        )
    return normalized


def _normalize_dialogue_units(values, beat_index, important_roles, visible_roles, offscreen_speakers, mentioned_roles):
    if not isinstance(values, list):
        return []
    known = set(important_roles)
    visible_set = set(visible_roles)
    allowed_nonvisible = set(offscreen_speakers) | set(mentioned_roles)
    normalized = []
    for value in values:
        if not isinstance(value, dict):
            continue
        speaker_name = str(value.get("speaker_name") or value.get("speaker") or "").strip()
        line = str(value.get("line") or value.get("text") or "").strip()
        if not speaker_name or not line or speaker_name not in known:
            continue
        target_role = str(value.get("target_role") or "").strip()
        if target_role and target_role not in known:
            target_role = ""
        speech_type = str(value.get("speech_type") or "onscreen").strip().lower()
        visible = bool(value.get("visible", speech_type == "onscreen"))
        if speech_type in OFFSCREEN_SPEECH_TYPES:
            visible = False
        if visible and speaker_name not in visible_set:
            visible = False
            speech_type = "offscreen" if speech_type == "onscreen" else speech_type
        if not visible and speaker_name not in allowed_nonvisible:
            continue
        normalized.append(
            {
                "speaker_name": speaker_name,
                "line": line,
                "speech_type": "onscreen" if visible else (speech_type or "offscreen"),
                "visible": visible,
                "target_role": target_role,
            }
        )
    return normalized


def _normalize_action_units(values, beat_index, visible_roles, important_roles):
    if not isinstance(values, list):
        return []
    visible_set = set(visible_roles)
    known = set(important_roles)
    allowed_scales = {"none", "small", "medium", "large"}
    allowed_timings = {"before_speech", "with_speech", "after_speech", "while_listening", "silent_transition"}
    normalized = []
    for value in values:
        if not isinstance(value, dict):
            continue
        role = str(value.get("role") or "").strip()
        action = str(value.get("action") or "").strip()
        if not role or not action or role not in visible_set:
            continue
        target_role = str(value.get("target_role") or "").strip()
        if target_role and target_role not in known:
            target_role = ""
        motion_scale = str(value.get("motion_scale") or "small").strip().lower()
        timing = str(value.get("timing") or "with_speech").strip().lower()
        visibility = str(value.get("visibility") or "onscreen").strip().lower()
        if visibility != "onscreen":
            continue
        if motion_scale not in allowed_scales:
            motion_scale = "small"
        if timing not in allowed_timings:
            timing = "with_speech"
        normalized.append(
            {
                "role": role,
                "action": action,
                "target_role": target_role,
                "object": str(value.get("object") or "").strip(),
                "motion_scale": motion_scale,
                "timing": timing,
                "visibility": "onscreen",
            }
        )
    return normalized


def _normalize_event_units(values, beat_index, visible_roles, important_roles):
    if not isinstance(values, list):
        return []
    visible_set = set(visible_roles)
    known = set(important_roles)
    allowed_types = {"speech_event", "silent_action", "environment_event"}
    normalized = []
    for value in values:
        if not isinstance(value, dict):
            continue
        event_type = str(value.get("type") or "speech_event").strip().lower()
        if event_type not in allowed_types:
            event_type = "speech_event"
        actor = str(value.get("actor") or value.get("role") or "").strip()
        action = str(value.get("action") or value.get("description") or "").strip()
        dialogue = str(value.get("dialogue") or value.get("line") or value.get("text") or "").strip()
        if event_type == "environment_event":
            actor = ""
            dialogue = ""
            if not action:
                continue
        else:
            if not actor or actor not in visible_set or not action:
                continue
            if event_type == "speech_event" and not dialogue:
                continue
            if event_type == "silent_action":
                dialogue = ""
        target_role = str(value.get("target_role") or "").strip()
        if target_role and target_role not in known:
            target_role = ""
        normalized.append(
            {
                "type": event_type,
                "actor": actor,
                "action": action,
                "dialogue": dialogue,
                "target_role": target_role,
                "object": str(value.get("object") or value.get("prop") or "").strip(),
            }
        )
    return normalized


def _normalize_identity_constraints(value, beat_index, reference_roles, model_id=None):
    item = value if isinstance(value, dict) else {}
    locked_roles = [role for role in _unique_text(item.get("locked_roles") or reference_roles) if role in reference_roles]
    if not locked_roles:
        locked_roles = list(reference_roles)
    default_note = "只使用注册身份锚点；参考素材由当前模型的分段配置单独选择"
    return {
        "registered_identity_only": True,
        "locked_roles": locked_roles,
        "forbidden_unregistered_appearance": _unique_text(item.get("forbidden_unregistered_appearance") or ["未注册身份外观"]),
        "notes": _unique_text(item.get("notes") or [default_note]),
    }


def _normalize_model_hints(value, beat, model_id):
    """Keep model-owned creative semantics out of the shared Beat contract."""
    raw = value if isinstance(value, dict) else {}
    if model_id == "minimax_h3_local_ref2va" and raw:
        from generation.pipelines.minimax_h3.rules import normalize_h3_hints
        return normalize_h3_hints(raw, model_id)
    result = {
        str(key): item
        for key, item in raw.items()
        if str(key).strip() and isinstance(item, dict)
    }
    return result


def _call_deepseek_messages(config, messages, temp_dir, name="beats_deepseek"):
    config = config or {}
    # Detailed Beats repeat excerpts, dialogue and shot descriptions. Give V4
    # enough output room while preserving explicit user token budgets.
    model = str(config.get("deepseek_model") or "deepseek-v4-flash")
    default_max_tokens = 65536 if model.startswith("deepseek-v4") else 16384
    max_tokens = int(config.get("beats_max_tokens", config.get("max_tokens", default_max_tokens)))
    request_path = Path(temp_dir) / f"{name}_request.json"
    full_path = Path(temp_dir) / f"{name}_full_response.json"
    raw_path = Path(temp_dir) / f"{name}_response.txt"
    log(f"[beats] DeepSeek request start: {name}")
    content, full_data, details = call_deepseek_chat_sdk(
        config,
        messages,
        temperature=float(config.get("temperature", 0.2)),
        max_tokens=max_tokens,
        json_mode=True,
        request_path=request_path,
        full_response_path=full_path,
        raw_response_path=raw_path,
        timeout=float(config.get("beats_timeout_sec", 600)),
    )
    log(f"[beats] DeepSeek request saved: {request_path}")
    log(f"[beats] DeepSeek finish_reason={details.get('finish_reason') or 'unknown'}")
    if not content:
        raise ValueError("DeepSeek returned empty message content")
    if str(details.get("finish_reason") or "").strip().lower() == "length":
        usage = full_data.get("usage") if isinstance(full_data, dict) else {}
        raise RuntimeError(
            "DeepSeek beats response was truncated by max_tokens; "
            f"completion_tokens={(usage or {}).get('completion_tokens')}, max_tokens={max_tokens}; "
            f"请提高 config.json 的 beats_max_tokens 或减少本次输入篇幅；原始响应已保存: {raw_path}"
        )
    return extract_json(content)


def call_deepseek(config, payload, output_path, temp_dir):
    model_id = str((payload.get("generation_policy") or {}).get("model_id") or "")
    policy = project_beat_policy(model_id)
    policy_prompt = "\n\n".join(
        part
        for part in (
            beat_structure_policy_prompt(policy),
            beat_count_policy_prompt(payload.get("target_duration_sec"), payload.get("target_beat_count"), policy=policy),
            beat_policy_prompt(policy.model_id),
        )
        if str(part or "").strip()
    )
    messages = [
        {
            "role": "system",
            "content": load_beat_writer_profile(
                _beat_writer_base_prompt(policy),
                policy_prompt,
                profile_name=_beat_writer_profile_name(policy),
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    return _call_deepseek_messages(config, messages, temp_dir, name="beats_deepseek")


def repair_deepseek_beats(config, payload, raw_data, validation_error, temp_dir, extra_instruction=""):
    model_id = str((payload.get("generation_policy") or {}).get("model_id") or "")
    policy = project_beat_policy(model_id)
    policy_prompt = "\n\n".join(
        part
        for part in (
            beat_structure_policy_prompt(policy),
            beat_count_policy_prompt(payload.get("target_duration_sec"), payload.get("target_beat_count"), policy=policy),
            beat_policy_prompt(policy.model_id),
        )
        if str(part or "").strip()
    )
    repair_system = load_beat_writer_profile(
        _beat_writer_base_prompt(policy),
        policy_prompt,
        profile_name=_beat_writer_profile_name(policy),
    ) + """

你现在不是重新创作剧情，而是修复 beats JSON 的结构错误。
必须保持原故事、角色、场景、对白含义和结局方向不变。
只允许修复字段结构、角色归属、可见性、分段边界、时长分配和 JSON 格式。
必须输出完整 JSON object，不要 Markdown。
"""
    if str(extra_instruction or "").strip():
        repair_system += "\n\n" + str(extra_instruction).strip()
    repair_payload = {
        "validation_error": str(validation_error),
        "original_input": payload,
        "invalid_beats_json": raw_data,
        "repair_task": "修复 invalid_beats_json，使其通过结构校验。不要改写故事，不要新增支线。",
        "target_beat_count": payload.get("target_beat_count"),
    }
    messages = [
        {"role": "system", "content": repair_system},
        {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
    ]
    return _call_deepseek_messages(config, messages, temp_dir, name="beats_deepseek_repair")


def normalize_beats(data, target_duration_sec=None, target_beat_count=None, model_id=None):
    if not isinstance(data, dict):
        raise ValueError("DeepSeek output must be a JSON object")
    title = str(data.get("title", "") or "故事标题")
    locked_outcome = str(data.get("locked_outcome") or "").strip()
    if not locked_outcome:
        raise ValueError("DeepSeek output must declare top-level locked_outcome")
    target_duration_sec = int(target_duration_sec or data.get("target_duration_sec") or 60)
    target_beat_count = _normalise_target_beat_count(target_beat_count or data.get("target_beat_count"))
    policy = project_beat_policy(model_id)
    model_rules = rules_for_model(policy.model_id)
    selected_beats = fit_beats_to_segment_count(
        data.get("beats"),
        target_duration_sec,
        target_beat_count=target_beat_count,
        policy=policy,
    )
    drift_limit = model_rules.beat_planning.max_raw_duration_drift_ratio
    raw_duration_values: list[int] = []
    for beat in selected_beats:
        try:
            raw_duration_values.append(int(round(float((beat or {}).get("estimated_duration_sec") or 0))))
        except (TypeError, ValueError):
            raw_duration_values.append(0)
    if drift_limit is not None and raw_duration_values and all(value > 0 for value in raw_duration_values):
        raw_total = sum(raw_duration_values)
        drift_ratio = abs(raw_total - target_duration_sec) / max(1, target_duration_sec)
        if drift_ratio > float(drift_limit):
            raise ValueError(
                f"{model_rules.label} raw Beat plan totals {raw_total}s for a {target_duration_sec}s target "
                f"(drift {drift_ratio:.0%}); merge or expand macro events and regenerate the complete Beat/Shot plan"
            )

    cleaned = []
    raw_durations = []
    visible_role_limit = model_rules.max_visible_roles or max_important_visible_roles()
    reference_role_limit = model_rules.max_reference_roles or visible_role_limit
    last_role_anchor = {
        "important_roles": [],
        "visible_roles": [],
        "reference_roles": [],
    }
    for index, beat in enumerate(selected_beats, start=1):
        important_roles = _unique_text(beat.get("important_roles"))
        visible_roles = _unique_text(beat.get("visible_roles"))
        reference_roles = _unique_text(beat.get("reference_roles"))
        offscreen_speakers = _unique_text(beat.get("offscreen_speakers"))
        mentioned_roles = _unique_text(beat.get("mentioned_roles"))
        # 角色字段一致性兜底（LLM 偶发输出不一致时按语义自动纠正，而非整次失败）：
        # 1) 可见角色若同时被标为画外/被提及，以“可见”为准，从非可见集合剔除。
        offscreen_speakers = [role for role in offscreen_speakers if role not in visible_roles]
        mentioned_roles = [role for role in mentioned_roles if role not in visible_roles]
        # 2) 可见为空但有参考时，用参考补齐可见。
        if not visible_roles and reference_roles:
            visible_roles = list(reference_roles)
        # 3) reference_roles 必须是 visible_roles 子集，多余部分剔除。
        reference_roles = [role for role in reference_roles if role in visible_roles]
        if not reference_roles and visible_roles:
            reference_roles = list(visible_roles[:reference_role_limit])
        # 4) important_roles 是总表，必须包含其余所有角色字段，缺失的自动并入。
        important_roles = _unique_text(important_roles + visible_roles + reference_roles + offscreen_speakers + mentioned_roles)
        # 5) 超过模型可见角色上限时降级：有台词者 → offscreen_speakers，无台词者 → mentioned_roles。
        if len(visible_roles) > visible_role_limit:
            overflow = visible_roles[visible_role_limit:]
            visible_roles = visible_roles[:visible_role_limit]
            raw_units = beat.get("dialogue_units") if isinstance(beat.get("dialogue_units"), list) else []
            speakers = {
                str((unit or {}).get("speaker_name") or (unit or {}).get("speaker") or "").strip()
                for unit in raw_units
                if isinstance(unit, dict)
            }
            for role in overflow:
                if role in speakers:
                    offscreen_speakers = _unique_text(offscreen_speakers + [role])
                else:
                    mentioned_roles = _unique_text(mentioned_roles + [role])
            reference_roles = [role for role in reference_roles if role in visible_roles][:reference_role_limit]
        if len(reference_roles) > reference_role_limit:
            reference_roles = reference_roles[:reference_role_limit]
        dialogue_units = _normalize_dialogue_units(
            beat.get("dialogue_units"),
            index,
            important_roles,
            visible_roles,
            offscreen_speakers,
            mentioned_roles,
        )
        action_units = _normalize_action_units(beat.get("action_units"), index, visible_roles, important_roles)
        event_units = _normalize_event_units(beat.get("event_units"), index, visible_roles, important_roles)
        background_extras = _normalize_beat_extras(beat.get("background_extras", []), index)
        identity_constraints = _normalize_identity_constraints(
            beat.get("identity_constraints"),
            index,
            reference_roles,
            model_id=policy.model_id,
        )
        model_hints = _normalize_model_hints(beat.get("model_hints"), beat, policy.model_id)
        try:
            duration = int(beat.get("estimated_duration_sec", 25))
        except Exception:
            duration = 25
        raw_durations.append(duration)
        cleaned_beat = {
            "id": index,
            "title": str(beat.get("title", f"剧情段落{index}") or f"剧情段落{index}"),
            "plot": str(beat.get("plot", "") or "").strip(),
            "scene_id": str(beat.get("scene_id") or beat.get("background_id") or "").strip(),
            "important_roles": important_roles,
            "visible_roles": visible_roles,
            "reference_roles": reference_roles,
            "offscreen_speakers": offscreen_speakers,
            "mentioned_roles": mentioned_roles,
            "dialogue_units": dialogue_units,
            "action_units": action_units,
            "event_units": event_units,
            "identity_constraints": identity_constraints,
            "background_extras": background_extras,
            "model_hints": model_hints,
            "estimated_duration_sec": duration,
        }
        source_excerpt = str(beat.get("source_excerpt") or "").strip()
        if source_excerpt:
            cleaned_beat["source_excerpt"] = source_excerpt
        cleaned.append(cleaned_beat)
        if not model_hints:
            cleaned[-1].pop("model_hints", None)
        last_role_anchor = {
            "important_roles": list(important_roles),
            "visible_roles": list(visible_roles),
            "reference_roles": list(reference_roles),
        }
    if target_beat_count:
        average_duration = target_duration_sec / max(1, len(cleaned))
        min_segment_sec = (
            policy.min_duration_sec
            if policy.min_duration_sec <= average_duration < policy.targeted_min_duration_sec
            else policy.targeted_min_duration_sec
        )
    else:
        min_segment_sec = policy.min_duration_sec
    durations = distribute_seconds(
        target_duration_sec,
        len(cleaned),
        raw_durations,
        min_segment_sec=min_segment_sec,
        max_segment_sec=policy.max_duration_sec,
    )
    timeline_normalizer = model_rules.beat_planning.timeline_normalizer
    normalized_cleaned = []
    for item, raw_duration, duration in zip(cleaned, raw_durations, durations):
        normalized = timeline_normalizer(item, int(raw_duration), int(duration)) if timeline_normalizer else item
        normalized["estimated_duration_sec"] = int(duration)
        normalized_cleaned.append(normalized)
    cleaned = normalized_cleaned
    return ensure_event_fields_on_beats(
        {
            "title": title,
            "locked_outcome": locked_outcome,
            "target_duration_sec": target_duration_sec,
            "total_duration_sec": sum(item["estimated_duration_sec"] for item in cleaned),
            "generation_policy": {"model_id": policy.model_id, "rules_revision": model_rules.revision, "duration_min_sec": policy.min_duration_sec, "duration_max_sec": policy.max_duration_sec},
            "beats": cleaned,
        }
    )


NOVEL_BEATS_EXTRA = """
【已有小说 → Beats（切分原文，不创作新剧情）】
- 输入是一段已有小说原文。你的任务是把小说切分成可直接生成视频的 beats，而不是创作新故事。
- 不得新增人物、地点、支线、事件或台词；剧情走向、角色关系、对白含义和结局方向必须与小说原文完全一致。
- 每个 beat 必须输出 source_excerpt：该 beat 对应的原文逐字摘录（直接从小说中复制，禁止改写、缩写或扩写）。
- plot 尽量保留小说原文表述，只做镜头化所需的最小补充（景别、机位、环境声、情绪），不重写原句。
- locked_outcome 用一句话锁定小说现有的结局方向。
- 其余字段（scene_id、roles、estimated_duration_sec、model_hints 等）严格遵守本模型的结构与时长规则。
""".strip()


# 台词逐字保留指令按模型拆分：LTX 对白进 dialogue_units，H3 对白进 shots[].dialogue。
NOVEL_BEATS_DIALOGUE_LTX = """
台词必须逐字保留：小说里的每句对白都必须进入某个 beat 的 dialogue_units[].line；speaker_name 归一化为角色 id，line 原文一个字都不能改、一句都不能丢。
""".strip()


NOVEL_BEATS_DIALOGUE_H3 = """
台词必须逐字保留：小说里的每句对白都必须进入某个 beat 的 model_hints.minimax_h3_local_ref2va.shots[].dialogue.line；dialogue.speaker 归一化为角色 id，line 原文一个字都不能改、一句都不能丢。禁止把对白概括成“提问”“回答”“解释”等动作摘要。
""".strip()


def generate_novel_beats(payload, config=None, model_id="", temp_dir=None, extra_instruction=NOVEL_BEATS_EXTRA):
    """已有完整小说 → Beats：复用故事链路相同的 policy/结构/时长规则与修复流程。"""
    requested_model_id = str(model_id or (payload.get("generation_policy") or {}).get("model_id") or "").strip()
    active_rules = rules_for_model(requested_model_id)
    dialogue_instruction = (
        NOVEL_BEATS_DIALOGUE_H3
        if active_rules.model_id == "minimax_h3_local_ref2va"
        else NOVEL_BEATS_DIALOGUE_LTX
    )
    extra_instruction = "\n\n".join(
        part for part in (str(extra_instruction or "").strip(), dialogue_instruction) if part
    )
    payload = dict(payload or {})
    payload["generation_policy"] = {"model_id": active_rules.model_id, "rules_revision": active_rules.revision}
    policy = project_beat_policy(active_rules.model_id)
    target_duration_sec = int(payload.get("target_duration_sec") or 60)
    target_beat_count = _normalise_target_beat_count(payload.get("target_beat_count"))
    auto_count = active_rules.beat_planning.default_target_count(target_duration_sec)
    if not target_beat_count:
        if auto_count:
            target_beat_count = auto_count
            payload["target_beat_count"] = auto_count
    else:
        # 显式分段数若与总时长不兼容（平均时长超出模型范围），改用模型默认分段数，避免硬失败。
        average = target_duration_sec / max(1, target_beat_count)
        effective_min = (
            policy.min_duration_sec
            if policy.min_duration_sec <= average < policy.targeted_min_duration_sec
            else policy.targeted_min_duration_sec
        )
        if average < effective_min or average > policy.max_duration_sec:
            if auto_count and auto_count != target_beat_count:
                log(
                    f"[beats][novel] target_beat_count={target_beat_count} 与 {target_duration_sec}s 不兼容"
                    f"（平均需 {effective_min}-{policy.max_duration_sec}s），改用模型默认 {auto_count} 段"
                )
                target_beat_count = auto_count
                payload["target_beat_count"] = auto_count
    policy_prompt = "\n\n".join(
        part
        for part in (
            beat_structure_policy_prompt(policy),
            beat_count_policy_prompt(target_duration_sec, target_beat_count, policy=policy),
            beat_policy_prompt(policy.model_id),
        )
        if str(part or "").strip()
    )
    system = load_beat_writer_profile(
        _beat_writer_base_prompt(policy),
        policy_prompt,
        profile_name=_beat_writer_profile_name(policy),
    )
    if str(extra_instruction or "").strip():
        system += "\n\n" + str(extra_instruction).strip()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    temp_dir = Path(temp_dir) if temp_dir else Path(".")
    raw_data = _call_deepseek_messages(config, messages, temp_dir, name="beats_novel")
    try:
        result = normalize_beats(
            raw_data,
            target_duration_sec=target_duration_sec,
            target_beat_count=target_beat_count,
            model_id=active_rules.model_id,
        )
    except Exception as validation_error:
        log(f"[beats][novel][repair] validation failed: {validation_error}")
        repaired_data = repair_deepseek_beats(
            config, payload, raw_data, validation_error, temp_dir, extra_instruction=extra_instruction
        )
        result = normalize_beats(
            repaired_data,
            target_duration_sec=target_duration_sec,
            target_beat_count=target_beat_count,
            model_id=active_rules.model_id,
        )
    return result


def run_generation(config_path="config.json", input_path=None, topic=None, output_path=None, target_duration_sec=0, target_beat_count=None, model_id=None):
    if not output_path:
        raise ValueError("output_path is required")
    try:
        log("[beats] start Filmable Story Beats")
        config = load_json(config_path)
        args = argparse.Namespace(input=input_path, topic=topic, target_duration_sec=target_duration_sec, target_beat_count=target_beat_count, model_id=model_id)
        payload = build_user_payload(args)
        requested_model_id = str(model_id or (payload.get("generation_policy") or {}).get("model_id") or "").strip()
        if requested_model_id:
            active_rules = rules_for_model(requested_model_id)
            payload["generation_policy"] = {"model_id": active_rules.model_id, "rules_revision": active_rules.revision}
            if not _normalise_target_beat_count(payload.get("target_beat_count")):
                auto_count = active_rules.beat_planning.default_target_count(int(payload.get("target_duration_sec") or 60))
                if auto_count:
                    payload["target_beat_count"] = auto_count
                    payload["target_beat_count_source"] = "model_default"
        temp_dir = get_temp_dir(output_path)
        log(f"[temp] dir: {temp_dir}")
        raw_data = call_deepseek(config, payload, output_path, temp_dir)
        target_duration_sec = int(target_duration_sec or payload.get("target_duration_sec") or 60)
        target_beat_count = _normalise_target_beat_count(target_beat_count or payload.get("target_beat_count"))
        active_model_id = str((payload.get("generation_policy") or {}).get("model_id") or model_id or "")
        try:
            result = normalize_beats(raw_data, target_duration_sec=target_duration_sec, target_beat_count=target_beat_count, model_id=active_model_id)
        except Exception as validation_error:
            log(f"[beats][repair] validation failed: {validation_error}")
            repaired_data = repair_deepseek_beats(config, payload, raw_data, validation_error, temp_dir)
            result = normalize_beats(repaired_data, target_duration_sec=target_duration_sec, target_beat_count=target_beat_count, model_id=active_model_id)
        save_json(output_path, result)
        if target_beat_count:
            log(f"[beats] target_count={target_beat_count} actual_count={len(result['beats'])}")
        log(f"[beats] count={len(result['beats'])}")
        log(f"[beats] total_duration_sec={result['total_duration_sec']} target={target_duration_sec}")
        log(f"[beats] saved {output_path}")
        return 0
    except Exception as exc:
        log("[beats][error] generation failed")
        log(f"[beats][error] {exc}")
        traceback.print_exc()
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--input")
    parser.add_argument("--topic")
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-duration-sec", type=int, default=0)
    parser.add_argument("--target-beat-count", type=int, default=0)
    parser.add_argument("--model-id", default="")
    args = parser.parse_args()
    return run_generation(
        config_path=args.config,
        input_path=args.input,
        topic=args.topic,
        output_path=args.output,
        target_duration_sec=args.target_duration_sec,
        target_beat_count=args.target_beat_count,
        model_id=args.model_id,
    )


if __name__ == "__main__":
    sys.exit(main())
