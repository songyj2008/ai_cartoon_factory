import re


BACKGROUND_EXTRAS = {
    "有人",
    "同事",
    "工作人员",
    "背景人物",
    "背景同事",
    "背景群众",
    "背景路人",
    "群众",
    "路人",
}


def _entries(project_bible):
    bible = project_bible if isinstance(project_bible, dict) else {}
    return list(bible.get("core_characters") or []) + list(
        bible.get("supporting_characters") or []
    )


def _display_name(entry):
    aliases = entry.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = [aliases]
    return str(
        entry.get("display_name")
        or entry.get("name_cn")
        or entry.get("name")
        or (aliases[0] if aliases else "")
        or entry.get("id")
        or ""
    ).strip()


def character_id(value, project_bible):
    text = str(value or "").strip()
    if not text:
        return ""
    for entry in _entries(project_bible):
        entry_id = str(entry.get("id") or "").strip()
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        references = {
            entry_id,
            _display_name(entry),
            str(entry.get("asset_identity_id") or "").strip(),
            *[str(alias or "").strip() for alias in aliases],
        }
        if text in references:
            return entry_id
    return ""


def canonical_name(value, project_bible=None):
    text = str(value or "").strip()
    if not text:
        return ""
    if text in BACKGROUND_EXTRAS:
        return ""
    for entry in _entries(project_bible):
        entry_id = str(entry.get("id") or "").strip()
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        references = {
            entry_id,
            _display_name(entry),
            str(entry.get("asset_identity_id") or "").strip(),
            *[str(alias or "").strip() for alias in aliases],
        }
        if text in references:
            return _display_name(entry)
    return text


def _reference_map(project_bible):
    mapping = {}
    for entry in _entries(project_bible):
        name = _display_name(entry)
        if not name:
            continue
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        for reference in (
            entry.get("id"),
            entry.get("asset_identity_id"),
            *aliases,
        ):
            reference = str(reference or "").strip()
            if reference and reference != name:
                mapping[reference] = name
    return mapping


def normalize_prompt_refs(prompt, project_bible=None):
    text = str(prompt or "")
    for old, new in sorted(
        _reference_map(project_bible).items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        text = re.sub(re.escape(old), new, text)
    return text


def extract_prompt_characters(prompt, project_bible=None):
    text = str(prompt or "")
    found = []
    for entry in _entries(project_bible):
        name = _display_name(entry)
        if not name:
            continue
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        references = [
            str(entry.get("id") or ""),
            name,
            str(entry.get("asset_identity_id") or ""),
            *[str(alias or "") for alias in aliases],
        ]
        if any(reference and reference in text for reference in references):
            found.append(name)
    return list(dict.fromkeys(found))


def _unique_values(values):
    result = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def known_roles_from_project_bible(project_bible=None):
    roles = []
    for entry in _entries(project_bible):
        if not isinstance(entry, dict):
            continue
        for field in ("display_name", "name_cn", "name", "id", "asset_identity_id"):
            value = str(entry.get(field) or "").strip()
            if value:
                roles.append(value)
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        roles.extend(str(alias).strip() for alias in aliases if str(alias).strip())
    return _unique_values(roles)


def sanitize_role_list(values, known_roles):
    known = _unique_values(known_roles or [])
    if not known:
        return []
    result = []
    for value in _unique_values(values or []):
        for role in known:
            if value == role or role in value:
                if role not in result:
                    result.append(role)
    return result


def _prompt_section(prompt, title):
    match = re.search(
        rf"【{re.escape(title)}】\s*(.*?)(?=\n【[^】]+】|\Z)",
        str(prompt or ""),
        flags=re.S,
    )
    return match.group(1).strip() if match else ""


def _role_section_characters(prompt, known_roles):
    section = _prompt_section(prompt, "角色")
    if not section:
        return []
    return [
        role for role in known_roles
        if role and re.search(re.escape(role), section)
    ]


def _speaker_role_from_label(label, known_roles):
    """Return only the syntactic speaker at the start of a dialogue label.

    Avoid substring matching labels such as “角色甲对角色乙说”, where 角色乙 is
    the addressee, not the speaker.
    """
    label = str(label or "").strip()
    if not label:
        return ""
    roles = sorted(_unique_values(known_roles or []), key=len, reverse=True)
    offscreen_prefix = r"(?:(?:从|在)?(?:电话里|电话中|手机里|手机中|听筒里|对讲机里|广播里|门外|画外|镜头外|屏幕中|监控里|监控中)|旁白|voice-over|off-screen|offscreen|phone|intercom|broadcast)"
    for role in roles:
        escaped = _role_token_pattern(role)
        # 电话里角色乙说 / 画外角色乙回答: role is the speaker even with an
        # offscreen carrier prefix. The offscreen/visible decision is handled
        # later by _has_offscreen_speech_evidence().
        if re.match(rf"^(?:{offscreen_prefix}\s*)?{escaped}(?:\s*(?:对|向|朝|问|告诉|跟|和|与).*)?(?:说|说道|问|回答|喊|低声说|大声说|大喊|回应)?$", label, flags=re.I):
            return role
        if re.match(rf"^{escaped}.{{0,6}}{offscreen_prefix}.{{0,6}}(?:说|说道|问|回答|喊|低声说|大声说|大喊|回应)$", label, flags=re.I):
            return role
        if re.match(rf"^{escaped}$", label):
            return role
    return ""


def _dialogue_speakers_from_guide(guide, known_roles=None):
    speakers = []
    for key in ("speaker",):
        value = str((guide or {}).get(key) or "").strip()
        value = _speaker_role_from_label(value, known_roles or []) or value
        if value:
            speakers.append(value)
    for key in ("speakers", "dialogue_speakers"):
        for value in (guide or {}).get(key) or []:
            value = str(value or "").strip()
            value = _speaker_role_from_label(value, known_roles or []) or value
            if value:
                speakers.append(value)
    for item in (guide or {}).get("dialogue_lines") or []:
        if isinstance(item, dict):
            value = str(item.get("speaker") or "").strip()
            value = _speaker_role_from_label(value, known_roles or []) or value
            if value:
                speakers.append(value)
    prompt = str((guide or {}).get("prompt") or "")
    dialogue = _prompt_section(prompt, "对白") or _prompt_section(prompt, "对话") or prompt
    for speaker, _line in re.findall(
        r"([A-Za-z0-9_\u4e00-\u9fff]{1,40})\s*[：:]\s*[『「“'\"]([^』」”'\"]{1,200})[』」”'\"]",
        dialogue,
    ):
        speaker = _speaker_role_from_label(speaker, known_roles or [])
        if speaker:
            speakers.append(speaker)
    # Also support unquoted dialogue labels, e.g. 角色甲对角色乙说：请确认。
    for speaker in re.findall(
        r"([A-Za-z0-9_\u4e00-\u9fff]{1,40}?\s*(?:对|向|朝|问|告诉|跟|和|与)?.{0,20}?(?:说|说道|问|回答|喊|低声说|大声说|大喊|回应))\s*[：:]",
        dialogue,
    ):
        speaker = _speaker_role_from_label(speaker, known_roles or [])
        if speaker:
            speakers.append(speaker)
    # Narrative off-screen voice labels such as “角色甲的声音从门外传来：『...』”
    # are not syntactic “X说” labels, but they still define a speaking owner.
    offscreen_terms = r"(?:电话里|电话中|手机里|手机中|听筒里|对讲机里|广播里|门外|画外|镜头外|屏幕中|监控里|监控中|phone|intercom|broadcast|off-screen|offscreen)"
    quote = r"[：:]\s*[『「“'\"]"
    for role in sorted(_unique_values(known_roles or []), key=len, reverse=True):
        escaped = _role_token_pattern(role)
        patterns = (
            rf"{escaped}(?:的)?声音.{{0,24}}{offscreen_terms}.{{0,12}}(?:传来|响起|出现|heard|comes).{{0,8}}{quote}",
            rf"{offscreen_terms}.{{0,12}}(?:传来|响起|听到).{{0,8}}{escaped}(?:的)?声音.{{0,8}}{quote}",
        )
        if any(re.search(pattern, dialogue, flags=re.I) for pattern in patterns):
            speakers.append(role)
    return _unique_values(speakers)


def _role_appears(text, role):
    return bool(role and re.search(_role_token_pattern(role), str(text or ""), flags=re.I))


def _role_token_pattern(role):
    escaped = re.escape(str(role or ""))
    if re.fullmatch(r"[A-Za-z0-9_ -]+", str(role or "")):
        return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
    return escaped


def _has_multi_person_visible_phrase(text):
    text = str(text or "")
    patterns = (
        r"两人", r"二人", r"三人", r"多人",
        r"两名", r"三名", r"多名",
        r"同时在画面内", r"同时出现在画面", r"同框", r"面对面",
        r"both characters", r"two characters", r"three characters",
        r"multiple characters", r"in the same frame", r"face to face",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _has_single_visible_constraint(text, role=None):
    text = str(text or "")
    if _has_multi_person_visible_phrase(text):
        return False
    generic_patterns = (
        r"\u5355\u72ec\u51fa\u955c",
        r"\u72ec\u81ea\u51fa\u955c",
        r"\u53ea\u6709\u4e00\u4eba",
        r"\u753b\u9762\u53ea\u6709\u4e00\u4eba",
        r"\u5355\u4eba\u51fa\u955c",
        r"单独出现在",
        r"独自出现在",
        r"单人镜头",
        r"单人出镜",
        r"画面只有一名角色",
        r"画面里只有一名角色",
        r"only one character",
        r"single character shot",
        r"single-character shot",
        r"\balone\b",
    )
    if any(re.search(pattern, text, flags=re.I) for pattern in generic_patterns):
        return True
    if role:
        escaped = _role_token_pattern(role)
        role_patterns = (
            rf"只有\s*{escaped}",
            rf"画面中只有\s*{escaped}",
            rf"only\s+{escaped}\s+is\s+visible",
            rf"only\s+{escaped}\s+appears\s+in\s+frame",
        )
        return any(re.search(pattern, text, flags=re.I) for pattern in role_patterns)
    return False


def _has_strong_onscreen_evidence(text, role, known_roles=None):
    """True when generic staging language puts the role physically in-frame."""
    if not role:
        return False
    escaped = _role_token_pattern(role)
    local = r"[^，。；\n]"
    direct_patterns = (
        rf"{escaped}{local}{{0,14}}(?:站在|坐在|位于|进入|走进|走出|面对|面向|同框|镜头内|画面中|画面里|画面左侧|画面右侧|桌前|桌后|门口|旁边|附近|拿起|拿着|举起|递给|交给|转身|低头|抬头|站着|坐着|看向|看着|望向|指向|指着|靠近)",
        rf"{escaped}\s*(?:stands|sits|enters|exits|faces|turns|looks|holds|raises|hands|is in frame|appears in frame)",
        rf"(?:画面中|画面里|镜头内|镜头里|同框|左侧|右侧|桌前|桌后|门口|旁边|附近){local}{{0,14}}{escaped}",
        rf"{escaped}{local}{{0,24}}(?:站在|坐在|停在|立在){local}{{0,12}}(?:桌前|桌后|门口|旁边|附近|左侧|右侧|面前|身后|走廊|房间|现场)",
        rf"{escaped}{local}{{0,24}}(?:拿起|拿着|举起|递给|交给|转身|低头|抬头){local}{{0,12}}(?:手机|文件|物品|道具|手|头)?",
    )
    if any(re.search(pattern, text, flags=re.I) for pattern in direct_patterns):
        return True

    relation_patterns = (
        r"(?:面对面|同框|对峙|并肩|一起站着|一起坐着|站在一起|坐在一起|坐着聊天|站着聊天)",
        r"(?:face to face|in the same frame|side by side|standing together|sitting together)",
    )
    for other in known_roles or []:
        if other == role:
            continue
        other_escaped = _role_token_pattern(other)
        pair_patterns = (
            rf"{escaped}.{{0,12}}(?:和|与|同).{{0,8}}{other_escaped}.{{0,16}}(?:{'|'.join(relation_patterns)})",
            rf"{other_escaped}.{{0,12}}(?:和|与|同).{{0,8}}{escaped}.{{0,16}}(?:{'|'.join(relation_patterns)})",
            rf"{escaped}.{{0,16}}(?:面对|面向|看向|看着|望向).{{0,8}}{other_escaped}",
            rf"{other_escaped}.{{0,16}}(?:面对|面向|看向|看着|望向).{{0,8}}{escaped}",
        )
        if any(re.search(pattern, text, flags=re.I) for pattern in pair_patterns):
            return True
    return False


def _owner_object_pattern_for_role(role):
    escaped = _role_token_pattern(role)
    owned = r"(?:的)?(?:办公室|房间|工位|家|车辆|车|文件|门牌|办公桌|座位|名字|照片|旁边的椅子|旁边的桌子|附近的椅子|office|room|desk|home|car|file|nameplate|name tag|photo)"
    return rf"{escaped}{owned}"


def _role_has_non_owner_occurrence(text, role):
    if not role:
        return False
    escaped = _role_token_pattern(role)
    stripped = re.sub(_owner_object_pattern_for_role(role), "", str(text or ""), flags=re.I)
    return bool(re.search(escaped, stripped, flags=re.I))


def _has_visible_evidence(text, role, known_roles=None):
    if not role:
        return False
    escaped = _role_token_pattern(role)
    negated_visibility_patterns = (
        rf"{escaped}.{{0,12}}(?:不|未|不能|不可|禁止|没有).{{0,8}}(?:出现|入画|出镜|在画面中|同框|在场)",
        rf"(?:不|未|不能|不可|禁止|没有).{{0,8}}{escaped}.{{0,8}}(?:出现|入画|出镜|在画面中|同框|在场)",
        rf"{escaped}.{{0,8}}(?:不在|未在|没有在).{{0,8}}(?:办公室|房间|现场|画面中|镜头里)",
        rf"{escaped}.{{0,8}}(?:不|未|没有).{{0,4}}在(?:办公室|房间|现场|画面中|镜头里)",
        rf"{escaped}.{{0,12}}(?:只是|仅是).{{0,12}}(?:提及|名称|办公室|房间)",
    )
    if any(re.search(pattern, text, flags=re.I) for pattern in negated_visibility_patterns):
        return False
    # Possessive/name mentions such as “角色乙办公室/角色乙房间/角色乙文件” are
    # location/object ownership, not proof that 角色乙 is in frame.  Check this
    # before broad staging patterns so an owner name cannot absorb nearby actions
    # performed by another visible role.
    owner_mention = re.search(_owner_object_pattern_for_role(role), text, flags=re.I)
    if owner_mention and not _role_has_non_owner_occurrence(text, role):
        return False
    if _has_strong_onscreen_evidence(text, role, known_roles):
        return True
    if _has_offscreen_speech_evidence(text, role) or _has_indirect_visual_evidence(text, role):
        return False
    # Only visual staging/action terms count as visibility evidence. Dialogue
    # ownership or verbal verbs (说/开口/张嘴/says/speaks/骂/训斥等) must not
    # pull an off-screen or merely mentioned role into visible characters.
    unicode_cn_patterns = (
        rf"{escaped}.{{0,8}}(?:\u671d|\u5f80|\u524d\u5f80|\u8d70\u5411|\u8d70\u8fdb|\u8d70\u51fa|\u8d70\u5411\u753b\u9762|\u770b\u5411|\u9012\u7ed9|\u62ff\u8d77|\u8f6c\u8eab|\u62cd\u684c|\u4f4e\u5934|\u62ac\u5934|\u7ed9.+\u6253\u7535\u8bdd)",
        rf"{escaped}\s*(?:\u7ad9\u7740|\u5750\u7740|\u8d70\u8fdb|\u8d70\u51fa|\u770b\u5411|\u9012\u7ed9|\u62ff\u8d77|\u8f6c\u8eab|\u62cd\u684c|\u4f4e\u5934|\u62ac\u5934|\u51b7\u7b11)",
        rf"{escaped}.{{0,18}}(?:\u4fdd\u6301\u5728\u80cc\u666f\u4e2d|\u5728\u753b\u9762\u4e2d|\u51fa\u73b0\u5728\u753b\u9762\u4e2d|\u4f4d\u4e8e\u753b\u9762|\u540c\u6846|\u5750\u5728\u684c\u540e|\u7ad9\u5728\u684c\u524d|\u7ad9\u5728.{{0,12}}(?:\u684c\u524d|\u95e8\u53e3|\u529e\u516c\u684c\u524d)|\u4f4d\u4e8e\u753b\u9762\u5de6\u4fa7|\u4f4d\u4e8e\u753b\u9762\u53f3\u4fa7|\u9762\u5bf9\u9762|\u9762\u5bf9|\u9762\u5411)",
    )
    cn_patterns = (
        rf"{escaped}\s*(?:站着|坐着|站在|坐在|进入|走进|走出|朝|往|前往|走向|走向画面|看向|看着|望向|指向|指着|面对|面向|递给|交给|递出|递文件|抓住|拉住|推开|靠近|拿着|拿起|转身|拍桌|低头|抬头|给.+打电话|接听电话|拿起电话|挂断电话)",
        rf"{escaped}.{{0,24}}(?:保持在背景中|在画面中|出现在画面中|位于画面|同框|坐在桌后|站在桌前|站在门口|站在旁边|坐在旁边|站在附近|坐在附近|在.{{0,18}}(?:门口|旁边|附近|走廊|外|办公室外|房间外).{{0,8}}(?:站着|坐着)|站在.{{0,12}}(?:桌前|门口|旁边|附近|外|办公室外|房间外|办公桌前)|坐在.{{0,12}}(?:桌前|门口|旁边|附近|办公桌前|椅子上|沙发上)|位于画面左侧|位于画面右侧|面对面|面对|面向)",
        rf"{escaped}(?:的)?背影.{{0,12}}(?:出现在|位于|进入|走进|站在)",
        rf"{escaped}.{{0,12}}(?:身后|身边|旁边|前方|后方|左侧|右侧).{{0,4}}是",
        rf"{escaped}.{{0,24}}(?:椅子上|沙发上|座位上).{{0,4}}坐下",
        rf"{escaped}.{{0,8}}(?:进入|走进).{{0,12}}(?:办公室|房间|走廊|门口)",
    )
    en_patterns = (
        rf"{escaped}\s+(?:stands|sits|enters|exits|turns|looks|hands|picks up)",
        rf"{escaped}.{{0,40}}(?:walks into frame|remains in the background|is visible|is in frame|appears in frame|lowers head|raises head|sitting behind the desk|standing in front of the desk|standing at the door|on the left side of frame|on the right side of frame)",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in unicode_cn_patterns + cn_patterns + en_patterns)


def _has_mentioned_only_evidence(text, role):
    if not role:
        return False
    escaped = _role_token_pattern(role)
    unicode_cn_patterns = (
        rf"(?:\u53bb|\u524d\u5f80|\u671d|\u5f80)\s*{escaped}(?:\u529e\u516c\u5ba4|\u623f\u95f4|\u5de5\u4f4d|\u5bb6|\u8f66\u91cc|\u6240\u5728\u5904)",
        rf"(?:\u53bb\u627e|\u627e|\u5bfb\u627e|\u901a\u77e5|\u8054\u7cfb|\u60f3\u8d77|\u63d0\u5230|\u6295\u8bc9|\u4e3e\u62a5)\s*{escaped}",
        rf"(?:\u7ed9|\u62e8\u901a)\s*{escaped}(?:\u6253\u7535\u8bdd|\u7535\u8bdd)",
        rf"(?:\u76d1\u63a7(?:\u91cc|\u4e2d)?(?:\u770b\u5230)?|\u5c4f\u5e55\u4e0a\u663e\u793a|\u58f0\u97f3(?:\u91cc|\u4e2d)?|\u542c\u5230)\s*{escaped}(?:\u7684\u58f0\u97f3)?",
        rf"{escaped}(?:\u7684)?\u58f0\u97f3.{{0,16}}(?:\u7535\u8bdd\u91cc|\u7535\u8bdd\u4e2d|\u624b\u673a\u91cc|\u542c\u7b52\u91cc|\u5bf9\u8bb2\u673a\u91cc|\u5e7f\u64ad\u91cc|\u95e8\u5916|\u753b\u5916|\u955c\u5934\u5916)",
        rf"(?:\u9a82|\u6012\u65a5|\u8d23\u9a82|\u8bad\u65a5)\s*{escaped}",
        rf"{escaped}(?:\u7684)?(?:\u529e\u516c\u5ba4|\u623f\u95f4|\u5de5\u4f4d|\u5bb6|\u8f66\u8f86|\u6587\u4ef6)",
    )
    cn_patterns = (
        rf"(?:去|前往|朝|往)\s*{escaped}(?:办公室|房间|工位|家|车里|所在处)",
        rf"(?:去找|找|寻找|通知|联系|想起|提到|投诉|举报)\s*{escaped}",
        rf"(?:给|拨通)\s*{escaped}(?:打电话|电话)",
        rf"(?:监控(?:里|中|画面里|画面中)?(?:看到|显示|出现)?|屏幕(?:上|里|中)?(?:显示|出现)?|屏幕(?:上|里|中)?的?|照片(?:里|中)?|视频(?:里|中)?|听到)\s*{escaped}(?:的声音)?",
        rf"(?:听见|听到).{{0,8}}{escaped}(?:的)?(?:声音|骂声|说话声)",
        rf"(?:听见|听到).{{0,8}}{escaped}.{{0,8}}(?:骂|怒斥|责骂|训斥|说|喊)",
        rf"{escaped}(?:的)?声音.{{0,16}}(?:电话里|电话中|手机里|听筒里|对讲机里|广播里|门外|画外|镜头外)",
        rf"{escaped}(?:的)?(?:办公室|房间|工位|家|车辆|文件)",
        rf"{escaped}(?:不在|未在|没有在).{0,8}(?:办公室|房间|现场)",
        rf"(?:听见|听到).{0,8}{escaped}.{0,8}(?:骂|怒斥|责骂|训斥|说|喊|声音)",
        rf"{escaped}(?:旁边|身边|附近|对面)(?:的)?(?:椅子|桌子|门|门牌|位置)",
    )
    en_patterns = (
        rf"(?:go to|walk toward)\s+{escaped}'s\s+(?:office|room|home|car|desk|location)",
        rf"(?:go find|look for|call|phone|contact|notify|mention|think of|complain about|report)\s+{escaped}",
        rf"(?:see\s+{escaped}\s+on\s+(?:monitor|screen)|hear\s+{escaped}'s\s+voice)",
        rf"{escaped}'s\s+(?:office|room|desk|home|car|file)",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in unicode_cn_patterns + cn_patterns + en_patterns)


def _has_indirect_visual_evidence(text, role):
    """True when the role appears only inside a monitor/screen/photo/video/feed."""
    if not role:
        return False
    escaped = _role_token_pattern(role)
    carrier = r"(?:监控(?:画面)?(?:里|中|上)?|屏幕(?:上|里|中)|手机屏幕(?:上|里|中)|电脑屏幕(?:上|里|中)|照片(?:里|中|上)|视频(?:里|中)|录像(?:里|中)|画面(?:里|中)|monitor|screen|photo|video|recording|feed)"
    verbs = r"(?:看到|显示|出现|播放|拍到|录到|shows?|displays?|appears?|seen)"
    patterns = (
        rf"{carrier}[^，。；\n]{{0,10}}(?:{verbs})[^，。；\n]{{0,10}}{escaped}",
        rf"{carrier}[^，。；\n]{{0,8}}{escaped}[^，。；\n]{{0,8}}(?:的)?(?:监控画面|照片|影像|视频|录像|画面)",
        rf"{escaped}[^，。；\n]{{0,12}}(?:在|出现在|显示在)[^，。；\n]{{0,8}}{carrier}",
        rf"{escaped}(?:的)?(?:监控画面|照片|影像|视频|录像)[^，。；\n]{{0,8}}(?:在)?{carrier}",
        rf"(?:{verbs})[^，。；\n]{{0,8}}{escaped}[^，。；\n]{{0,8}}(?:在)?{carrier}",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _is_owner_object_target(text, role):
    """True when role is part of an owned location/object target, not the person."""
    if not role:
        return False
    escaped = _role_token_pattern(role)
    owned = r"(?:的)?(?:办公室|房间|工位|家|车辆|车|文件|门牌|办公桌|座位|名字|照片|旁边的椅子|旁边的桌子|附近的椅子|office|room|desk|home|car|file|nameplate|name tag|photo)"
    patterns = (
        rf"{escaped}{owned}",
        rf"(?:看向|望向|走向|朝向|面对|指向|指着|靠近).{{0,6}}{escaped}{owned}",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _has_offscreen_speech_evidence(text, role):
    if not role:
        return False
    escaped = _role_token_pattern(role)
    local = r"[^，。；\n]"
    offscreen_terms = r"(?:(?:从|在)?(?:电话里|电话中|手机里|手机中|听筒里|对讲机里|广播里|门外|画外|镜头外|屏幕中|监控里|监控中)|旁白|voice-over|off-screen|offscreen|phone|intercom|broadcast)"
    speech_verbs = r"(?:说|说道|开口|回答|问|喊|传来|响起|says|speaks|asks|answers|shouts|replies)"
    patterns = (
        # Tight coupling only: 电话里角色乙说 / 电话里传来角色乙的声音.
        rf"{offscreen_terms}{local}{{0,8}}{escaped}{local}{{0,8}}{speech_verbs}",
        rf"{offscreen_terms}{local}{{0,12}}(?:传来|响起|听到){local}{{0,8}}{escaped}(?:的)?声音",
        # 角色乙的声音从电话里传来 / 角色乙在电话里说.
        rf"{escaped}(?:的)?声音{local}{{0,16}}{offscreen_terms}",
        rf"(?:听见|听到){local}{{0,8}}{escaped}(?:的)?(?:声音|骂声|说话声)",
        rf"(?:听见|听到){local}{{0,8}}{escaped}{local}{{0,8}}(?:骂|怒斥|责骂|训斥|说|喊)",
        rf"(?:接听电话|电话接通|通话中|正在通话){local}{{0,16}}{escaped}{local}{{0,8}}{speech_verbs}",
        rf"{escaped}{local}{{0,8}}{offscreen_terms}{local}{{0,8}}{speech_verbs}",
        rf"{escaped}.{{0,12}}(?:不|未|没有|禁止).{{0,8}}(?:出现|入画|出镜|在画面中|同框)",
        rf"(?:不|未|没有|禁止).{{0,8}}{escaped}.{{0,8}}(?:出现|入画|出镜|在画面中|同框)",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _speaker_default_visible(text, role):
    if not role or role in {"旁白", "Narrator", "narrator"}:
        return False
    if _has_offscreen_speech_evidence(text, role) or _has_indirect_visual_evidence(text, role):
        return False
    escaped = _role_token_pattern(role)
    patterns = (
        rf"{escaped}.{{0,8}}(?:说|说道|开口|回答|问|质问|喊|低声说|大声说)",
        rf"{escaped}\s*(?:says|speaks|asks|answers|shouts|replies)",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _actor_visible_evidence(text, role):
    if not role:
        return False
    escaped = _role_token_pattern(role)
    patterns = (
        rf"{escaped}.{{0,8}}(?:骂|怒斥|责骂|训斥|批评|指责|命令|催促)",
        rf"{escaped}\s*(?:scolds|criticizes|orders|urges)",
    )
    matched = any(re.search(pattern, text, flags=re.I) for pattern in patterns)
    if not matched:
        return False
    if _has_offscreen_speech_evidence(text, role) or _has_indirect_visual_evidence(text, role):
        return False
    return True


def _visual_interaction_target_evidence(text, role, known_roles):
    if not role or _is_owner_object_target(text, role):
        return False
    escaped = _role_token_pattern(role)
    # Object/target side of clearly visual two-person interactions. Keep phone/call/find/office-owner
    # patterns out of this list so mentioned-only roles remain off-screen.
    visual_verbs = r"(?:看向|看着|望向|面对|面向|背对|递给|交给|递出|递文件|抓住|拉住|推开|指向|指着|靠近)"
    relation = r"(?:旁边|身边|对面|面前|身后|左侧|右侧|附近)"
    for other in known_roles or []:
        if other == role:
            continue
        other_escaped = _role_token_pattern(other)
        patterns = (
            rf"{other_escaped}.{{0,10}}{visual_verbs}.{{0,6}}{escaped}(?!的?(?:办公室|房间|工位|家|车辆|车|文件|门牌|办公桌|座位|名字|照片))",
            rf"{other_escaped}.{{0,8}}(?:给|向|朝).{{0,4}}{escaped}.{{0,6}}(?:递文件|递出文件|递交文件|递|交文件)",
            rf"{other_escaped}.{{0,10}}(?:站在|坐在|停在|立在).{{0,6}}{escaped}.{{0,3}}{relation}",
            rf"{other_escaped}.{{0,10}}(?:站在|坐在|停在|立在).{{0,6}}{relation}.{{0,3}}{escaped}",
            rf"{escaped}.{{0,10}}(?:和|与|同).{{0,10}}{other_escaped}.{{0,10}}(?:面对面|同框|并肩(?:站着|坐着)?|一起(?:站着|坐着|站在|坐在)?|站在一起|坐在一起|坐着聊天|站着聊天)",
            rf"{other_escaped}.{{0,10}}(?:和|与|同).{{0,10}}{escaped}.{{0,10}}(?:面对面|同框|并肩(?:站着|坐着)?|一起(?:站着|坐着|站在|坐在)?|站在一起|坐在一起|坐着聊天|站着聊天)",
        )
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            return True
    if _has_offscreen_speech_evidence(text, role) or _has_indirect_visual_evidence(text, role):
        return False
    return False



def _visual_interaction_actor_evidence(text, role, known_roles):
    """True when role is the actor/subject of a visual two-person interaction."""
    if not role or _is_owner_object_target(text, role):
        return False
    escaped = _role_token_pattern(role)
    visual_verbs = r"(?:看向|看着|望向|面对|面向|背对|递给|交给|递出|递文件|抓住|拉住|推开|指向|指着|靠近)"
    relation = r"(?:旁边|身边|对面|面前|身后|左侧|右侧|附近)"
    owned_object = r"(?:的)?(?:办公室|房间|工位|家|车辆|车|文件|门牌|办公桌|座位|名字|照片|旁边的椅子|旁边的桌子|附近的椅子|office|room|desk|home|car|file|nameplate|name tag|photo)"
    for other in known_roles or []:
        if other == role:
            continue
        other_escaped = _role_token_pattern(other)
        patterns = (
            # 角色甲指着角色乙 / 角色甲将文件交给角色乙
            rf"{escaped}.{{0,16}}{visual_verbs}.{{0,8}}{other_escaped}(?!{owned_object})",
            rf"{escaped}.{{0,8}}(?:给|向|朝).{{0,4}}{other_escaped}.{{0,6}}(?:递文件|递出文件|递交文件|递|交文件)",
            # 角色甲站在角色乙旁边 / 角色甲坐在角色乙对面
            rf"{escaped}.{{0,10}}(?:站在|坐在|停在|立在).{{0,8}}{other_escaped}.{{0,3}}{relation}",
            rf"{escaped}.{{0,10}}(?:站在|坐在|停在|立在).{{0,8}}{relation}.{{0,3}}{other_escaped}",
            rf"{escaped}.{{0,10}}(?:和|与|同).{{0,10}}{other_escaped}.{{0,10}}(?:面对面|同框|并肩(?:站着|坐着)?|一起(?:站着|坐着|站在|坐在)?|站在一起|坐在一起|坐着聊天|站着聊天)",
            rf"{other_escaped}.{{0,10}}(?:和|与|同).{{0,10}}{escaped}.{{0,10}}(?:面对面|同框|并肩(?:站着|坐着)?|一起(?:站着|坐着|站在|坐在)?|站在一起|坐在一起|坐着聊天|站着聊天)",
        )
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            return True
    if _has_offscreen_speech_evidence(text, role) or _has_indirect_visual_evidence(text, role):
        return False
    return False

def split_visible_and_mentioned_characters(guide: dict, known_roles: list[str]) -> dict:
    """Split guide roles into visible characters and off-frame mentions.

    The function is intentionally story-agnostic: it relies on the current
    role registry and generic film-language evidence, never on fixed names.
    """
    guide = guide or {}
    prompt = str(guide.get("prompt") or "")
    text = "\n".join(
        str(guide.get(key) or "")
        for key in (
            "prompt",
            "action_state",
            "scene_state",
            "character_state",
            "continuity_note",
        )
    )
    known = _unique_values(list(known_roles or []))
    if not known:
        return {"characters": [], "visible_characters": [], "mentioned_characters": []}

    declared = _unique_values(
        sanitize_role_list(guide.get("visible_characters") or [], known)
    )
    declared = [role for role in declared if role in known]
    speakers = [role for role in _unique_values(_dialogue_speakers_from_guide(guide, known)) if role in known]
    offscreen_speech_evidence = [role for role in known if _has_offscreen_speech_evidence(text, role)]
    visible_evidence = [role for role in known if _has_visible_evidence(text, role, known)]
    speaker_visible_evidence = [role for role in speakers if _speaker_default_visible(text, role)]
    actor_visible_evidence = [role for role in known if _actor_visible_evidence(text, role)]
    interaction_visible_evidence = [
        role for role in known
        if _visual_interaction_target_evidence(text, role, known)
        or _visual_interaction_actor_evidence(text, role, known)
    ]
    mentioned_evidence = [role for role in known if _has_mentioned_only_evidence(text, role)]
    appearing = [role for role in known if _role_appears(text, role)]

    if _has_single_visible_constraint(text):
        primary = ""
        for role in known:
            if _has_single_visible_constraint(text, role):
                primary = role
                break
        if not primary:
            for source in (visible_evidence, speaker_visible_evidence, actor_visible_evidence, interaction_visible_evidence, speakers, declared):
                if source:
                    primary = source[0]
                    break
        visible = [primary] if primary else []
        mentioned = [
            role for role in _unique_values(appearing + declared + speakers + offscreen_speech_evidence + mentioned_evidence)
            if role not in visible
        ]
        speaking_characters = _unique_values(speakers + offscreen_speech_evidence)
        return {
            "characters": visible,
            "visible_characters": list(visible),
            "mentioned_characters": mentioned,
            "speaking_characters": speaking_characters,
            "offscreen_speakers": [role for role in speaking_characters if role not in visible],
            "silent_characters": [role for role in visible if role not in speaking_characters],
        }

    evidence_visible = _unique_values(
        visible_evidence
        + speaker_visible_evidence
        + actor_visible_evidence
        + interaction_visible_evidence
    )
    declared_visible = []
    for role in declared:
        # Treat LLM-provided visible_characters as advisory, not absolute.
        # A role that is only mentioned in dialogue/text and has no visual evidence
        # must not become a clear on-screen reference image.
        if role in mentioned_evidence and role not in evidence_visible and role not in speakers:
            continue
        declared_visible.append(role)
    visible = _unique_values(declared_visible + evidence_visible)
    speaking_characters = _unique_values(speakers + offscreen_speech_evidence)
    mentioned = sanitize_role_list(guide.get("mentioned_characters") or [], known)
    for role in mentioned_evidence:
        if role not in visible and not _is_owner_object_target(text, role):
            mentioned.append(role)
    for role in speaking_characters:
        if role not in visible and not _is_owner_object_target(text, role):
            mentioned.append(role)
    for role in appearing:
        if role not in visible and not _is_owner_object_target(text, role):
            mentioned.append(role)
    mentioned = [role for role in _unique_values(mentioned) if role not in visible]
    return {
        "characters": visible,
        "visible_characters": list(visible),
        "mentioned_characters": mentioned,
        "speaking_characters": speaking_characters,
        "offscreen_speakers": [role for role in speaking_characters if role not in visible],
        "silent_characters": [role for role in visible if role not in speaking_characters],
    }


def normalize_character_refs(guide, project_bible=None):
    item = dict(guide or {})
    item["prompt"] = normalize_prompt_refs(item.get("prompt"), project_bible)

    listed = item.get("characters") or []
    if not isinstance(listed, list):
        listed = [listed]
    characters = [
        canonical_name(value, project_bible)
        for value in listed
        if str(value or "").strip() not in BACKGROUND_EXTRAS
    ]
    known_roles = known_roles_from_project_bible(project_bible)
    characters = sanitize_role_list(characters, known_roles)
    split_roles = split_visible_and_mentioned_characters(
        {**item, "characters": characters},
        known_roles,
    )
    characters = [value for value in dict.fromkeys(split_roles["characters"]) if value]
    item["characters"] = characters
    item["visible_characters"] = list(characters)
    item["mentioned_characters"] = split_roles["mentioned_characters"]

    speaker = canonical_name(item.get("speaker"), project_bible)
    item["speaker"] = speaker
    item["speaking_characters"] = split_roles.get("speaking_characters") or ([speaker] if speaker else [])
    item["offscreen_speakers"] = list(split_roles.get("offscreen_speakers") or [])
    if speaker and speaker not in characters and speaker not in item["offscreen_speakers"]:
        item["offscreen_speakers"].append(speaker)
    if speaker and speaker not in characters and speaker not in item["mentioned_characters"]:
        item["mentioned_characters"].append(speaker)

    silent = item.get("silent") or []
    if not isinstance(silent, list):
        silent = [silent]
    item["silent"] = [
        name
        for name in dict.fromkeys(
            canonical_name(value, project_bible) for value in silent
        )
        if name in characters and name != item["speaker"]
    ]

    poses = item.get("character_poses")
    if isinstance(poses, dict):
        item["character_poses"] = {
            canonical_name(role, project_bible): pose
            for role, pose in poses.items()
            if canonical_name(role, project_bible) in characters
        }
    return item
