"""H3-native prompt construction for local Ref2VA workflows."""
from __future__ import annotations

from .speech_contract import preserve_delivery, render_delivery
from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Mapping

from generation.pipelines.minimax_h3.asset_binder import H3AssetBindings
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig, H3ReferenceAsset, normalize_h3_scope
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
from services.project_bible import load_project_bible


@dataclass(frozen=True)
class H3CompiledPrompt:
    text: str
    references: tuple[tuple[str, str, H3ReferenceAsset], ...]
    source: str


class H3PromptCompiler:
    """Compile a semantic beat into H3's reference-aware directing prompt.

    Local ComfyUI H3 tokenization recognizes ``<Picture N>``, ``<Video N>`` and
    ``<Audio N>``.  Cloud-facing ``@图片N`` syntax is deliberately not emitted
    here because it is a different execution adapter.
    """

    def compile(
        self,
        segment: Mapping[str, Any],
        config: H3GenerationConfig,
        bindings: H3AssetBindings,
        *,
        duration_sec: float,
        draft: bool = False,
    ) -> H3CompiledPrompt:
        if draft:
            segment = {**segment, 'h3_draft': True}
        override = self.strip_occupancy_section(str(config.prompt_override or ""))
        if override.strip():
            if not segment.get('h3_timing_only'):
                override = self.compact_reference_sections(override.strip(), bindings)
            if not draft:
                H3Ref2VAValidator().validate_prompt(override).raise_for_errors()
            return H3CompiledPrompt(text=override, references=bindings.prompt_references, source="prompt_override")

        if not self._raw_shots(segment):
            raise ValueError("H3 缺少分镜计划，无法确定开场调度；请先重新生成该段 H3 分镜提示词")

        references = self._retention_lines(segment, bindings)
        raw_shots = self._raw_shots(segment)
        visible = {r for shot in raw_shots for r in shot.get("visible_roles", [])} if raw_shots and all("visible_roles" in shot for shot in raw_shots) else None
        if not draft:
            H3Ref2VAValidator().validate_segment_structure(segment, duration_sec=duration_sec).raise_for_errors()
        core_idea = self._core_idea(segment, duration_sec=duration_sec, aspect_ratio=config.aspect_ratio)
        summary = self._summary_text(core_idea, segment, bindings)
        shots = self._shot_lines(segment, duration_sec=duration_sec, fallback=core_idea, bindings=bindings)
        subjects = self._reference_subject_lines('\n'.join(shots), bindings, visible_roles=visible)
        sections: list[str] = []
        if subjects:
            sections.append("【主体定义】\n" + "\n".join(subjects))
        sections.append("【任务概述】\n" + summary)
        # 小说原文摘录不在 H3 视频提示词里展示/提交，只在段卡片下方单独呈现，
        # 避免把大段原文一起塞进提交给模型的提示词。
        if references:
            sections.append("【参考保留关系】\n" + "\n".join(references))
        hints = self._model_hints(segment)
        shot_mode = str(hints.get("shot_mode") or segment.get("shot_mode") or "").strip().lower()
        if shot_mode == "one_take":
            shot_mode_text = "一镜到底；保持同一连续时空，不切镜。"
        elif shot_mode == "multi_shot":
            shot_mode_text = "多 Shot；切镜时明确景别与承接主体，保持人物和场景连续。"
        else:
            shot_mode_text = "按事件需要组织镜头，并保持人物、场景和动作连续。"
        detail_lines = [shot_mode_text, *shots]
        exclusions = self._text_items(
            hints.get("exclusions") or segment.get("h3_exclusions") or segment.get("negative_constraints")
        )
        if exclusions:
            detail_lines.append("限制：" + "；".join(exclusions))
        sections.append("【详细镜头描述】\n" + "\n".join(detail_lines))
        soundscape = self._overall_soundscape(segment)
        sections.append("【整体声音环境】\n" + (soundscape or "使用镜头中明确描述的对白、动作声与自然环境声。"))
        music = str(hints.get("non_diegetic_music") or segment.get("non_diegetic_music") or "").strip()
        sections.append("【非叙事性音乐】\n" + (music or "未指定额外配乐。"))
        text = "\n\n".join(section for section in sections if section.strip()).strip()
        if not draft:
            H3Ref2VAValidator().validate_prompt(text).raise_for_errors()
        if 'h3_scene_constraints' in segment:
            from .continuity.constraints import prompt_with_constraints
            text = prompt_with_constraints(text, segment['h3_scene_constraints'])
        text = self.compact_reference_sections(text, bindings)
        return H3CompiledPrompt(text=text, references=bindings.prompt_references, source="compiled")

    @classmethod
    def validate_reference_tags(cls, text, bindings):
        """Validate the submitted graph before rebuilding managed definitions."""
        from .dialogue_text import transform_outside_dialogue
        allowed = {tag.casefold() for tag, _, _ in bindings.prompt_references}
        allowed.update(e['subject_tag'].casefold() for e in cls()._subject_reference_entries(bindings))
        missing = set()
        def inspect(value):
            missing.update(tag for tag in re.findall(r'<(?:Subject|Picture|Audio|Video)\s+\d+>', value, re.I)
                           if tag.casefold() not in allowed)
            return value
        transform_outside_dialogue(str(text), inspect)
        if missing:
            raise ValueError('参考绑定不存在：' + '、'.join(sorted(missing)) + '；未覆盖已保存的配置')

    @classmethod
    def compact_reference_sections(cls, text, bindings):
        """Use one renderer for generated, edited, saved and submitted references.

        Only compiler-owned sections are rebuilt. Shot text and dialogue are
        left intact, and raw overrides without managed references remain raw.
        """
        text = cls.strip_occupancy_section(str(text or '').strip())
        text = cls.normalize_visible_role_lines(text)
        cls.validate_reference_tags(text, bindings)
        managed = bool(re.search(r'(?m)^【(?:主体定义|参考保留关系|人物音色绑定)】', text))
        has_voice = any(a.asset.metadata.get('voice_only') for a in bindings.audios)
        if not managed and not has_voice:
            return text
        text = cls.strip_voice_sections(text)
        compiler = cls()
        visible = None
        if '【详细镜头描述】' in text:
            from .shot_editor import inspect_prompt
            inspected = inspect_prompt(text, 'compact', H3GenerationConfig(
                asset_manifest=tuple(b.asset for b in bindings.all)).to_dict()['asset_manifest'])
            shots = (inspected.get('document') or {}).get('shots', [])
            if shots and all('visible_roles' in shot for shot in shots):
                visible = {role for shot in shots for role in shot['visible_roles']}
        subjects = compiler._reference_subject_lines(text, bindings, visible_roles=visible)
        if not re.search(r'(?m)^【[^】]+】[ \t]*$', text):
            text = '【详细镜头描述】\n' + text
        text = cls._set_reference_section(text, '主体定义', subjects)
        text = cls._set_reference_section(text, '参考保留关系',
            compiler._retention_lines_from_prompt(text, bindings), before='详细镜头描述')
        text = cls._set_reference_section(text, '人物音色绑定', compiler.voice_section_lines(text, bindings), before='详细镜头描述')
        return text.strip()

    @staticmethod
    def strip_occupancy_section(text):
        """Scene state is planning input; only Shot blocking goes to video."""
        from .dialogue_text import transform_outside_dialogue
        return transform_outside_dialogue(text, lambda value: re.sub(
            r'(?ms)^【场景人物占位】[^\n]*\n?.*?(?=^【[^】\n]+】|\Z)', '', value)).strip()

    @staticmethod
    def strip_voice_sections(text):
        from .dialogue_text import transform_outside_dialogue
        return transform_outside_dialogue(text, lambda value: re.sub(r'(?ms)^【人物音色绑定】[^\n]*\n?.*?(?=^【[^】\n]+】|\Z)', '', value).replace('【人物音色绑定结束】', '')).strip()

    def voice_section_lines(self, text, bindings):
        lines = self._voice_definition_lines(text, bindings)
        if lines:
            lines.append('参考音频仅用于对应人物音色，不播放参考录音、不复述其内容，不作为背景声或配乐播放；对白严格使用当前镜头指定台词，不新增台词。各镜头中，画内说话人口型与本人台词同步；当前未发言人物不代说、不对口型。')
        return lines

    @staticmethod
    def normalize_visible_role_lines(text):
        """Render standalone cast fields as prose without touching dialogue."""
        def render(match):
            value = match['roles']
            tags = re.findall(r'<Subject\s+\d+>', value)
            remainder = re.sub(r'<Subject\s+\d+>', '', value)
            if remainder.strip(' \t,，、\'"'):
                return match[0]
            return match['indent'] + '画面人物：' + ('、'.join(tags) if tags else '无') + '。'
        parts = re.split(r'(<d\b[^>]*>.*?</d>)', str(text or ''), flags=re.S | re.I)
        for index in range(0, len(parts), 2):
            parts[index] = re.sub(
                r'^(?P<indent>[ \t]*)visible_roles[ \t]*[:：][ \t]*\[(?P<roles>[^\]\r\n]*)\][ \t]*[。；;]?[ \t]*$',
                render, parts[index], flags=re.M)
        return ''.join(parts)

    def _reference_subject_lines(self, text, bindings, *, visible_roles=None):
        lines = self._subject_lines(bindings, visible_roles=visible_roles)
        return lines

    @staticmethod
    def _set_reference_section(text, title, lines, *, before=None):
        pattern = re.compile(r'(?ms)^【' + re.escape(title) + r'】[ \t]*\n?.*?(?=^【[^】\n]+】[ \t]*$|\Z)')
        replacement = '【' + title + '】\n' + '\n'.join(lines) + '\n\n' if lines else ''
        if pattern.search(text):
            used = False
            def replace(match):
                nonlocal used
                value = '' if used else replacement
                used = True
                return value
            return pattern.sub(replace, text)
        if not replacement:
            return text
        if before and '【' + before + '】' in text:
            return text.replace('【' + before + '】', replacement + '【' + before + '】', 1)
        return (text.rstrip() + '\n\n' + replacement) if before else replacement + text

    def _voice_definition_lines(self, text, bindings):
        entries = {e['asset_id']: e for e in self._subject_reference_entries(bindings)}
        speaker_ids = {m[1]: m[2] for m in re.finditer(
            r'(<(?:Subject|Audio)\s+\d+>|[\w\u4e00-\u9fff]+)\s*[（(](S\d+)[）)]'
            r'[^<>；\n]*?(?:说|报告)[：:]\s*<d>', text)}
        def speaker(value):
            return value + (f'（{speaker_ids[value]}）' if value in speaker_ids else '')
        lines = []
        for audio in bindings.audios:
            meta = audio.asset.metadata
            if not meta.get("voice_only"):
                continue
            usage = str(meta.get('voice_usage') or '')
            if usage in {'narration', 'telephone'}:
                voice_name = str(meta.get('voice_speaker') or audio.asset.role or '').strip()
                if not voice_name:
                    raise ValueError('旁白或电话音色必须填写声音角色名称')
                delivery = ('画外旁白' if usage == 'narration' else '电话另一端画外声音，保留电话质感；声源按镜头执行')
                lines.append(f'{audio.prompt_tag}：{speaker(voice_name)}的音色参考；{delivery}。')
                continue
            owners = [entries[image.asset.asset_id]['subject_tag'] if image.asset.asset_id in entries else image.prompt_tag
                      for image in bindings.images if meta.get('identity_id') and
                      image.asset.metadata.get('identity_id') == meta['identity_id']]
            if not owners:
                name = str(meta.get('voice_speaker') or meta.get('role_id') or audio.asset.label or '').strip()
                if not name: raise ValueError('音色缺少人物身份')
                owners = [name]
            lines.append(f"{audio.prompt_tag} → {'、'.join(speaker(owner) for owner in owners)}：音色参考。")
        return lines

    def _subject_lines(self, bindings: H3AssetBindings, *, visible_roles=None) -> list[str]:
        """Map character identities to images without reproducing their dossiers."""
        lines: list[str] = []
        has_character = False
        for entry in self._subject_reference_entries(bindings):
            asset_kind = entry['asset_kind']
            has_character |= asset_kind == 'character'
            meta = entry['metadata']
            if asset_kind == 'character':
                note = ('本段未安排入画；在场状态与站位以镜头描述为准。'
                        if visible_roles is not None and not any(
                            str(role) in entry.get('aliases', []) or str(role) == entry['subject_tag']
                            for role in visible_roles) else '')
                lines.append(f"{entry['subject_tag']}：{entry['name']}；可见内容来自 {entry['picture_tag']}。" + note)
                continue
            description = str(meta.get('identity_summary') or meta.get('subject_description') or
                              meta.get('identity_description') or meta.get('description_cn') or
                              meta.get('image_description') or '').strip()
            if asset_kind == 'background' and not meta.get('identity_summary'):
                description = ''
            # Prefer an author-supplied summary; never infer traits from a name.
            prefixes = [name + punctuation for name in entry.get('description_names', [entry['name']])
                        for punctuation in ('，', '；')]
            for prefix in prefixes:
                if description.startswith(prefix):
                    description = description[len(prefix):]
                    break
            description = description.rstrip('。；; ')
            if description == entry['name']:
                description = ''
            hint = str(meta.get('appearance_summary') or meta.get('reference_summary') or '').strip().rstrip('。；; ')
            notes = [description] if description else []
            if hint and hint not in description:
                notes.append(hint)
            if asset_kind == 'background':
                notes.append('保持空间、陈设与光线')
            lines.append(f"{entry['subject_tag']}：{entry['name']}；可见内容来自 {entry['picture_tag']}。" + ''.join(note + '。' for note in notes))
        if has_character:
            lines.append('人物保持参考外观；站位、姿势与视线按镜头执行，明确的首帧或构图锚点按其要求执行。')
        return lines

    def _subject_reference_entries(self, bindings: H3AssetBindings) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        bible_roles, bible_characters = self._bible_character_indices()
        for binding in bindings.images:
            asset = binding.asset
            metadata = asset.metadata if isinstance(asset.metadata, Mapping) else {}
            asset_kind = str(metadata.get("asset_kind") or "").strip().lower()
            if self._picture_anchor_role(metadata) and not asset_kind:
                continue
            description = str(
                metadata.get("subject_description")
                or metadata.get("identity_description")
                or metadata.get("description_cn")
                or metadata.get("image_description")
                or ""
            ).strip()
            # A bound project role owns its story name. Library labels and
            # imported drafts may still carry the source character's name.
            # Unbound references retain their editor-controlled role/label.
            source_name = str(asset.role or asset.label or "").strip()
            role_id = str(metadata.get("role_id") or "").strip()
            identity_id = str(metadata.get("identity_id") or "").strip()
            bible_char = bible_roles.get(role_id) or bible_characters.get(identity_id)
            name = str((bible_char or {}).get("display_name") or source_name).strip()
            if not name and not description:
                continue
            subject_index = len(entries) + 1
            name = name or f"主体 {subject_index}"
            description = description or f"以 {binding.prompt_tag} 中可见的{name}为准"
            aliases = list(dict.fromkeys([*self._role_aliases(name, metadata),
                                         *self._role_aliases(source_name, metadata)]))
            # Beats may address a character by its Project Bible role id
            # (e.g. ``character_004``) rather than its display name.  Resolve
            # that id back to this Subject so dialogue speakers render as
            # ``<Subject N>`` instead of leaking the raw role id.  Two links
            # are honored: the Bible's ``asset_identity_id`` (via the asset
            # ``metadata.identity_id``) and the role id the auto-binder stored
            # directly on ``metadata.role_id``.
            if bible_char:
                aliases = list(dict.fromkeys([*aliases, *self._character_aliases(bible_char)]))
            if role_id and role_id not in aliases:
                aliases.append(role_id)
            entries.append(
                {
                    "asset_id": asset.asset_id,
                    "subject_tag": f"<Subject {subject_index}>",
                    "picture_tag": binding.prompt_tag,
                    "name": name,
                    "description_names": list(dict.fromkeys([name, source_name])) if source_name else [name],
                    "description": description,
                    "aliases": aliases,
                    "asset_kind": asset_kind or "visible_content",
                    "metadata": metadata,
                }
            )
        return entries

    @staticmethod
    def _bible_character_indices() -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
        """Resolve explicit roles first; shared identities cannot imply a role."""
        bible = load_project_bible() or {}
        characters = list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])
        roles: dict[str, Mapping[str, Any]] = {}
        identities: dict[str, list[Mapping[str, Any]]] = {}
        for item in characters:
            if not isinstance(item, Mapping):
                continue
            role_id = str(item.get("id") or "").strip()
            identity_id = str(item.get("asset_identity_id") or "").strip()
            if role_id:
                roles[role_id] = item
            if identity_id:
                identities.setdefault(identity_id, []).append(item)
        return roles, {key: values[0] for key, values in identities.items() if len(values) == 1}

    @staticmethod
    def _character_aliases(character: Mapping[str, Any]) -> list[str]:
        values = [str(character.get("id") or "").strip(), str(character.get("display_name") or "").strip()]
        for alias in character.get("aliases") or []:
            values.append(str(alias or "").strip())
        return [value for value in dict.fromkeys(values) if len(value) >= 2]

    def _retention_lines(self, segment: Mapping[str, Any], bindings: H3AssetBindings) -> list[str]:
        lines: list[str] = []
        for entry in self._subject_reference_entries(bindings):
            metadata = entry["metadata"] if isinstance(entry["metadata"], Mapping) else {}
            marker = self._retention_marker(metadata, default="fully_preserved")
            shot_indices = self._subject_shot_indices(segment, entry, bindings)
            appearance = f"（出现于 {'、'.join(f'Shot {value}' for value in shot_indices)}）" if shot_indices else ""
            note = self._subject_retention_note(entry)
            lines.append(f"{entry['subject_tag']}{appearance}：{marker} - {note}。")
        return lines + self._media_retention_lines(bindings)

    @staticmethod
    def _subject_retention_note(entry):
        meta = entry['metadata']
        return str(meta.get('retention_note') or meta.get('retention_description') or
                   {'character':'人物外观','background':'场景布局与光线'}.get(entry['asset_kind'],'外观、结构与材质')).strip().rstrip('。；; ')

    def _media_retention_lines(self, bindings):
        lines = []
        for tag, kind, asset in bindings.prompt_references:
            metadata = asset.metadata if isinstance(asset.metadata, Mapping) else {}
            if kind == "image" and not self._picture_anchor_role(metadata):
                continue
            if kind == "video_audio":
                role = str(asset.metadata.get("audio_role") or asset.metadata.get("soundtrack_role") or "该视频的配套音轨参考").strip()
            else:
                role = asset.role or asset.label or {
                    "image": "画面参考",
                    "video": "动作、镜头或剪辑参考",
                    "audio": "音色、台词或节奏参考",
                }[kind]
            if kind == "image":
                shot_indices = [str(value) for value in metadata.get("shot_indices") or []]
                usage = self._picture_anchor_role(metadata)
                scope = f"（用于 {'、'.join(f'Shot {value}' for value in shot_indices)}）" if shot_indices else ""
                marker = self._retention_marker(metadata, default="fully_preserved")
                retention = str(metadata.get("retention_note") or f"作为{usage}使用，保持对应画面与构图要求").strip()
                lines.append(f"{tag}{scope}：{marker} - {retention}。")
                continue
            elif kind == "video":
                marker = self._retention_marker(metadata, default="weak_reference")
                retention = "参考动作、镜头运动、剪辑节奏或时间结构"
            elif kind in {"audio", "video_audio"}:
                marker = self._retention_marker(metadata, default="reference", audio=True)
                if metadata.get('voice_only'):
                    note = str(metadata.get('retention_note') or metadata.get('reference_summary') or '仅取音色').strip().rstrip('。；; ')
                    lines.append(f'{tag}：{marker} - {note}。')
                    continue
                retention = "参考音色、台词、声音质感或节奏"
            else:
                marker = self._retention_marker(metadata, default="weak_reference")
                retention = "作为画面参考"
            lines.append(f"{tag}：{marker} - {role}；{retention}。")
        return lines

    def _retention_lines_from_prompt(self, prompt: str, bindings: H3AssetBindings) -> list[str]:
        """Build retention declarations from the currently edited Shot text."""
        entries = self._subject_reference_entries(bindings)
        detail = re.search(r'(?ms)^【详细镜头描述】[ \t]*\n(.*?)(?=^【[^】\n]+】[ \t]*$|\Z)', str(prompt or ''))
        shot_text = detail[1] if detail else str(prompt or '')
        shot_matches = list(re.finditer(r"(?m)^\[Shot\s+(\d+)\]", shot_text))
        shot_bodies: list[tuple[int, str]] = []
        for index, match in enumerate(shot_matches):
            end = shot_matches[index + 1].start() if index + 1 < len(shot_matches) else len(shot_text)
            shot_bodies.append((int(match.group(1)), shot_text[match.start():end]))

        # Share authored visibility/scene resolution with editor and validation.
        # A direct camera subject must not disappear from reference declarations
        # merely because the author omitted the optional cast field.
        from .shot_editor import inspect_prompt
        manifest=H3GenerationConfig(asset_manifest=tuple(b.asset for b in bindings.all)).to_dict()['asset_manifest']
        inspected=inspect_prompt(prompt,'retention',manifest)
        parsed={number:shot for (number,_),shot in zip(shot_bodies,
                (inspected.get('document') or {}).get('shots',[]))} if inspected.get('verified') else {}

        lines: list[str] = []
        for entry in entries:
            metadata = entry["metadata"] if isinstance(entry["metadata"], Mapping) else {}
            marker = self._retention_marker(metadata, default="fully_preserved")
            indices = []
            for number, body in shot_bodies:
                cast = re.search(r'画面人物：([^；\n]+)', body)
                shot=parsed.get(number,{})
                if entry['asset_kind']=='background':
                    present=(bool(metadata.get('scene_id')) and shot.get('scene_id')==metadata['scene_id']) or entry['subject_tag'] in body
                elif 'visible_roles' in shot:
                    aliases={str(v) for v in entry.get('aliases',[])}|{entry['subject_tag']}
                    present=any(str(role) in aliases for role in shot['visible_roles'])
                else:
                    present=bool(cast and entry['subject_tag'] in cast[1])
                if present:
                    indices.append(number)
            if (entry['asset_kind'] == 'character' and parsed and not indices
                    and marker == 'fully_preserved' and not self._picture_anchor_role(metadata)):
                lines.append(f"{entry['subject_tag']}：weak_reference - 仅作身份与声音归属映射，本段不安排入画，不向其他人物转移外观。")
                continue
            appearance = f"（出现于 {'、'.join(f'Shot {value}' for value in indices)}）" if indices else ""
            note = self._subject_retention_note(entry)
            lines.append(f"{entry['subject_tag']}{appearance}：{marker} - {note}。")

        return lines + self._media_retention_lines(bindings)

    def _summary_text(
        self,
        core_idea: str,
        segment: Mapping[str, Any],
        bindings: H3AssetBindings,
    ) -> str:
        entries = self._subject_reference_entries(bindings)
        first_shot = self._raw_shots(segment)[0] if self._raw_shots(segment) else {}
        primary = self._entries_mentioned_in_text(str(first_shot.get("subject") or ""), entries)
        body = self._replace_subject_mentions(core_idea, entries, primary_entries=primary)
        task_types: list[str] = []
        if any(self._picture_anchor_role(binding.asset.metadata) for binding in bindings.images):
            task_types.append("keyframe completion")
        video_roles = [
            str((binding.asset.metadata or {}).get("reference_role") or "").strip().lower()
            for binding in bindings.videos
        ]
        if any(role in {"video_editing", "editing", "source_video"} for role in video_roles):
            task_types.append("video editing")
        if any(role in {"video_continuation", "continuation"} for role in video_roles):
            task_types.append("video continuation")
        audio_roles = [
            str((binding.asset.metadata or {}).get("reference_role") or "").strip().lower()
            for binding in bindings.audios
        ]
        if any(role in {"audio_reuse", "reuse", "copy"} for role in audio_roles):
            task_types.append("audio reuse")
        elif bindings.audios or any(binding.paired_audio_tag for binding in bindings.videos):
            task_types.append("audio reference")
        if bindings.all and not any(value in task_types for value in ("video editing", "video continuation")):
            task_types.insert(0, "reference generation")
        prefix = " + ".join(dict.fromkeys(task_types)) or "reference generation"
        return f"[{prefix}] {body}"

    @staticmethod
    def _picture_anchor_role(metadata: Mapping[str, Any]) -> str:
        raw = str(
            metadata.get("picture_role")
            or metadata.get("reference_role")
            or metadata.get("usage")
            or ""
        ).strip().lower()
        roles = {
            "first_frame": "首帧锚点",
            "last_frame": "尾帧锚点",
            "keyframe": "关键帧锚点",
            "edited_keyframe": "编辑关键帧锚点",
            "composition": "构图锚点",
            "composition_anchor": "构图锚点",
            "storyboard": "故事板参考",
            "shot_planning": "镜头规划参考",
        }
        if raw in roles:
            return roles[raw]
        for key, label in (
            ("is_first_frame", "首帧锚点"),
            ("is_last_frame", "尾帧锚点"),
            ("is_keyframe", "关键帧锚点"),
            ("is_composition_anchor", "构图锚点"),
            ("is_storyboard", "故事板参考"),
        ):
            if metadata.get(key):
                return label
        return ""

    @staticmethod
    def _retention_marker(metadata: Mapping[str, Any], *, default: str, audio: bool = False) -> str:
        allowed = (
            {"fully_copy", "partially_copy", "reference", "weak_reference"}
            if audio
            else {"fully_preserved", "partially_preserved", "attribute_transfer", "weak_reference"}
        )
        value = str(metadata.get("retention_marker") or metadata.get("retention_relationship") or default).strip()
        return value if value in allowed else default

    @staticmethod
    def _core_idea(segment: Mapping[str, Any], *, duration_sec: float, aspect_ratio: str | None) -> str:
        hints = H3PromptCompiler._model_hints(segment)
        idea = str(
            hints.get("core_idea")
            or segment.get("h3_core_idea")
            or segment.get("core_idea")
            or segment.get("story_event")
            or segment.get("plot")
            or ""
        ).strip()
        if not idea:
            idea = "根据参考素材生成一段连贯、可见动作明确的短视频。"
        duration_text = f"{int(duration_sec) if float(duration_sec).is_integer() else duration_sec} 秒"
        ratio_text = f"，{aspect_ratio}" if aspect_ratio else ""
        return f"{duration_text}{ratio_text}。{idea}"

    def _subject_shot_indices(
        self,
        segment: Mapping[str, Any],
        entry: Mapping[str, Any],
        bindings: H3AssetBindings,
    ) -> list[int]:
        shots = self._raw_shots(segment)
        background_count = sum(
            1 for value in self._subject_reference_entries(bindings) if value["asset_kind"] == "background"
        )
        return [
            index
            for index, shot in enumerate(shots, start=1)
            if isinstance(shot, Mapping) and self._subject_applies_to_shot(entry, shot, index, background_count)
        ]

    def _raw_shots(self, segment: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        hints = self._model_hints(segment)
        shots = (hints.get('shots') or []) if hints.get('planning_fingerprint') else (
            hints.get("shots") or segment.get("h3_shots") or segment.get("shots") or [])
        raw_shots = [dict(shot) for shot in shots if isinstance(shot, Mapping)] if isinstance(shots, list) else []
        if hints.get("planning_fingerprint"):
            # Fresh plans may split one line across Shots or keep multiple
            # speakers in one Shot. A positional overlay would undo that timing.
            from generation.pipelines.minimax_h3.prompt_planner import _assert_current_dialogue_preserved

            if not segment.get('h3_draft') and not hints.get('planning_issues'):
                _assert_current_dialogue_preserved(segment, {"shots": raw_shots})
            return raw_shots
        return self._shots_with_current_dialogue(segment, raw_shots)

    def _shots_with_current_dialogue(
        self,
        segment: Mapping[str, Any],
        shots: list[dict[str, Any]],
    ) -> list[Mapping[str, Any]]:
        """Overlay cached H3 Shot dialogue with the current Beat contract.

        ``model_hints...shots`` is generated when Beats are first split.  The
        user can subsequently edit ``dialogue_units`` without rebuilding that
        model hint.  Shot composition remains useful, but its dialogue is then
        stale and must not override the current Beat.

        An explicitly saved dialogue contract includes the empty (silent) case.
        """

        units = [
            item
            for item in segment.get("dialogue_units") or []
            if isinstance(item, Mapping) and str(item.get("line") or item.get("text") or "").strip()
        ]
        if not shots:
            return shots
        if segment.get('dialogue_authoritative'):
            by_shot = {}
            for unit in units:
                by_shot.setdefault(unit.get('shot_uid'), []).append(unit)
            shot_ids = {s.get('shot_uid') or s.get('uid') for s in shots}
            if any(not key or key not in shot_ids for key in by_shot):
                raise ValueError('已确认对白的 Shot 归属失效，请重新生成分镜')
            for shot in shots:
                shot.pop('dialogue', None)
                shot.pop('dialogues', None)
                assigned = by_shot.get(shot.get('shot_uid') or shot.get('uid'), [])
                if assigned:
                    shot['dialogues'] = [self._current_dialogue(u, None) for u in assigned]
            return shots
        if not units:
            return shots

        dialogue_slots = [index for index, shot in enumerate(shots) if self._shot_dialogue_values(shot)]
        if not dialogue_slots:
            dialogue_slots = list(range(min(len(shots), len(units))))

        for index, shot_index in enumerate(dialogue_slots):
            shot = shots[shot_index]
            if index >= len(units):
                shot.pop("dialogue", None)
                shot.pop("dialogues", None)
                continue
            previous_values = self._shot_dialogue_values(shot)
            previous = previous_values[0] if previous_values else None
            shot["dialogue"] = self._current_dialogue(units[index], previous)
            shot.pop("dialogues", None)

        if len(units) > len(dialogue_slots):
            target_index = dialogue_slots[-1] if dialogue_slots else len(shots) - 1
            target = shots[target_index]
            values = self._shot_dialogue_values(target)
            values.extend(self._current_dialogue(unit, None) for unit in units[len(dialogue_slots):])
            target.pop("dialogue", None)
            target["dialogues"] = values
        return shots

    def _current_dialogue(self, unit: Mapping[str, Any], previous: Any) -> dict[str, Any]:
        previous_speaker, previous_scope, _line, _continuation = self._dialogue_parts(previous)
        explicit_owner = bool(unit.get('dialogue_uid'))
        speaker = str((unit.get('speaker') if explicit_owner else unit.get('speaker_name')) or unit.get("speaker") or "").strip()
        # Normalized legacy Beats sometimes put a role id in speaker_name.
        # Preserve the Shot's human-readable speaker in that case.
        if not explicit_owner and (not speaker or re.fullmatch(r"character[_-]\d+", speaker, flags=re.IGNORECASE)) and previous_speaker:
            speaker = previous_speaker
        return preserve_delivery(unit, {
            "speaker": speaker,
            "scope": str(unit.get("scope") or unit.get("speech_type") or previous_scope or "onscreen").strip().lower(),
            "line": str(unit.get("line") or unit.get("text") or "").strip(),
            "continues_previous": bool(unit.get("continues_previous") or unit.get("continuation")),
        })

    @staticmethod
    def _shot_dialogue_values(shot: Mapping[str, Any]) -> list[Any]:
        values = shot.get("dialogues")
        if isinstance(values, list):
            return [value for value in values if value not in (None, "")]
        value = shot.get("dialogue")
        # Beat 编写阶段偶尔把 dialogue 写成数组 [{speaker, scope, line, ...}]，
        # 这里平铺成单条，避免把整个 dict 以 str() 原样泄漏进提示词。
        if isinstance(value, list):
            return [item for item in value if item not in (None, "")]
        return [value] if value not in (None, "") else []

    @staticmethod
    def _entries_mentioned_in_text(
        text: str,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        lowered = str(text or "").lower()
        return [
            entry
            for entry in entries
            if any(str(alias).lower() in lowered for alias in entry.get("aliases") or [] if len(str(alias)) >= 2)
        ]

    def _replace_subject_mentions(
        self,
        text: str,
        entries: list[dict[str, Any]],
        *,
        primary_entries: list[dict[str, Any]] | None = None,
    ) -> str:
        from generation.pipelines.minimax_h3.dialogue_text import transform_outside_dialogue

        return transform_outside_dialogue(
            str(text or "").strip(),
            lambda value: self._replace_visual_subject_mentions(value, entries, primary_entries=primary_entries),
        )

    def _replace_visual_subject_mentions(
        self,
        text: str,
        entries: list[dict[str, Any]],
        *,
        primary_entries: list[dict[str, Any]] | None = None,
    ) -> str:
        result = str(text or "")
        if not result:
            return ""
        replacements: list[tuple[str, str]] = []
        for entry in entries:
            for alias in entry.get("aliases") or []:
                alias_text = str(alias).strip()
                if len(alias_text) >= 2:
                    replacements.append((alias_text, str(entry["subject_tag"])))
        for alias, tag in sorted(replacements, key=lambda value: len(value[0]), reverse=True):
            result = re.sub(re.escape(alias), tag, result, flags=re.IGNORECASE)

        # Editors may keep a tag beside its human-readable name. Replacing
        # that name must not leave two adjacent references to the same asset.
        result = re.sub(r"(<Subject\s+\d+>)(?:\s*\1)+", r"\1", result, flags=re.IGNORECASE)
        character_tags = {
            str(entry["subject_tag"]) for entry in entries if entry.get("asset_kind") == "character"
        }

        # Resolve a collective reference from the explicitly named Subjects in
        # the same sentence/clause before falling back to the Shot-wide group.
        # Example: "<Subject 4>抓住<Subject 3>，两人转头" is deterministic,
        # while a bare "两人" in a four-character Shot is intentionally left
        # untouched for the Beat writer to disambiguate.
        for token, count in (("两人", 2), ("二人", 2), ("三人", 3)):
            search_from = 0
            while True:
                position = result.find(token, search_from)
                if position < 0:
                    break
                clause_start = max(result.rfind(mark, 0, position) for mark in "；。！？\n") + 1
                subject_tags = re.findall(r"<Subject\s+\d+>", result[clause_start:position])
                local_group = list(dict.fromkeys(tag for tag in subject_tags if tag in character_tags))
                if len(local_group) == count:
                    explicit_group = "、".join(local_group)
                    result = result[:position] + explicit_group + result[position + len(token) :]
                    search_from = position + len(explicit_group)
                else:
                    search_from = position + len(token)

        primary = list(primary_entries or [])
        character_entries = [entry for entry in entries if entry.get("asset_kind") == "character"]
        number_words = {2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
        for count in range(2, 10):
            group: list[dict[str, Any]] = []
            if len(primary) == count:
                group = primary
            elif len(primary) == 1:
                others = [entry for entry in character_entries if entry not in primary]
                if len(others) == count:
                    group = others
            if not group:
                continue
            explicit_group = "、".join(str(entry["subject_tag"]) for entry in group)
            patterns = [rf"{count}\s*人", rf"这\s*{count}\s*人"]
            if count in number_words:
                word = number_words[count]
                patterns.extend(
                    [
                        rf"{word}人",
                        rf"这{word}人",
                        rf"{word}个小家伙",
                        rf"这{word}个小家伙",
                    ]
                )
            for pattern in patterns:
                result = re.sub(pattern, explicit_group, result)
        return result

    @staticmethod
    def _subject_applies_to_shot(
        entry: Mapping[str, Any],
        shot: Mapping[str, Any],
        shot_index: int,
        background_count: int,
    ) -> bool:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
        raw_indices = metadata.get("shot_indices") or []
        indices = {int(value) for value in raw_indices if str(value).strip().isdigit()}
        shot_text = json.dumps(dict(shot), ensure_ascii=False).lower()
        aliases = [str(alias).lower() for alias in entry.get("aliases") or [] if len(str(alias)) >= 2]
        if str(entry.get("asset_kind") or "") == "background":
            if indices:
                return shot_index in indices
            if shot.get("scene_id") and metadata.get("scene_id"):
                return shot["scene_id"] == metadata["scene_id"]
            return any(alias in shot_text for alias in aliases) or background_count == 1
        if "visible_roles" in shot:
            exact_aliases = {str(a).lower() for a in entry.get('aliases', [])}
            return any(str(role).lower() in exact_aliases or str(role).lower() == str(entry["subject_tag"]).lower() for role in shot["visible_roles"])
        visual = {key: shot.get(key) for key in ("subject", "blocking", "action", "camera")}
        return any(alias in json.dumps(visual, ensure_ascii=False).lower() for alias in aliases)

    def _shot_lines(
        self,
        segment: Mapping[str, Any],
        *,
        duration_sec: float,
        fallback: str,
        bindings: H3AssetBindings,
    ) -> list[str]:
        hints = self._model_hints(segment)
        raw_shots = self._raw_shots(segment)
        lines: list[str] = []
        subject_entries = self._subject_reference_entries(bindings)
        speaker_ids: dict[str, str] = {}
        for shot in raw_shots:
            for dialogue in self._shot_dialogue_values(shot):
                speaker, _scope, line, _continuation = self._dialogue_parts(dialogue)
                if speaker and line:
                    key = self._speaker_key(speaker, subject_entries)
                    if key not in speaker_ids:
                        speaker_ids[key] = f"S{len(speaker_ids) + 1}"
        voice_refs = {}
        for audio in bindings.audios:
            meta = audio.asset.metadata or {}
            voice_refs[audio.asset.asset_id] = audio.prompt_tag
            for key in (meta.get('role_id'), meta.get('identity_id'), audio.asset.role, audio.asset.label):
                if key:
                    voice_refs[str(key)] = audio.prompt_tag
            for entry in subject_entries:
                if meta.get('identity_id') and meta.get('identity_id') == entry['metadata'].get('identity_id'):
                    for alias in [entry['subject_tag'], entry['name'], *entry.get('aliases', [])]:
                        voice_refs[alias] = audio.prompt_tag
        for index, shot in enumerate(raw_shots, start=1):
            primary_entries = self._entries_mentioned_in_text(str(shot.get("subject") or ""), subject_entries)
            timing = self._shot_timing(shot, index)
            description = self._shot_description(
                shot,
                speaker_ids=speaker_ids,
                subject_entries=subject_entries,
                primary_entries=primary_entries,
                voice_refs=voice_refs,
            )
            if description:
                reference_text = self._shot_reference_text(shot, index, bindings)
                if reference_text:
                    description = f"{reference_text}；{description}"
                marker = f"[Shot {index}]" if index == 1 else f"[Shot {index}] At {self._shot_start_timestamp(shot)}"
                lines.append(f"{marker}（{timing}）：{description}")
        if not lines:
            actions = self._text_items(segment.get("action_units"))
            dialogue = self._text_items(segment.get("dialogue_units"))
            body = "；".join(actions + dialogue).strip("；")
            if not body:
                body = fallback
            seconds = int(duration_sec) if float(duration_sec).is_integer() else duration_sec
            reference_text = self._shot_reference_text({"description": body}, 1, bindings)
            prefix = f"{reference_text}；" if reference_text else ""
            lines.append(f"[Shot 1]（0–{seconds} 秒）：{prefix}{body}")
        return lines

    def _shot_reference_text(
        self,
        shot: Mapping[str, Any],
        shot_index: int,
        bindings: H3AssetBindings,
    ) -> str:
        shot_text = json.dumps(dict(shot), ensure_ascii=False).lower()
        references: list[str] = []
        subject_entries = self._subject_reference_entries(bindings)
        background_count = sum(1 for entry in subject_entries if entry["asset_kind"] == "background")
        for entry in subject_entries:
            if entry["asset_kind"] == "background" and self._subject_applies_to_shot(entry, shot, shot_index, background_count):
                references.append(f"场景使用 {entry['subject_tag']}")
        if not references and shot.get("scene_id"):
            scene = str(shot["scene_id"])
            bible = load_project_bible()
            entry = next((item for item in bible.get("allowed_backgrounds") or []
                          if str(item.get("id")) == scene), {})
            name = str(entry.get("display_name") or scene)
            references.append(f"场景：{name}")
        for binding in bindings.images:
            metadata = binding.asset.metadata if isinstance(binding.asset.metadata, Mapping) else {}
            anchor_role = self._picture_anchor_role(metadata)
            if not anchor_role:
                continue
            raw_indices = metadata.get("shot_indices") or []
            indices = {
                int(value)
                for value in raw_indices
                if str(value).strip().isdigit()
            }
            label = str(binding.asset.label or binding.asset.role or anchor_role).strip()
            is_first_frame = anchor_role == "首帧锚点" and shot_index == 1
            if shot_index in indices or is_first_frame or (not indices and label and label.lower() in shot_text):
                references.append(f"镜头以 {binding.prompt_tag}（{anchor_role}：{label}）为画面锚点")
        return "；".join(dict.fromkeys(references))

    @staticmethod
    def _role_aliases(name: str, metadata: Mapping[str, Any]) -> list[str]:
        aliases = [str(value).strip() for value in metadata.get("aliases") or [] if str(value).strip()]
        aliases.append(str(name or "").strip())
        without_number = re.sub(r"^\s*\d+\s*号\s*", "", str(name or "")).strip()
        if without_number:
            aliases.append(without_number)
        for separator in ("·", "・", " "):
            if separator in str(name or ""):
                tail = str(name).rsplit(separator, 1)[-1].strip()
                if tail:
                    aliases.append(tail)
        return list(dict.fromkeys(alias for alias in aliases if len(alias) >= 2))

    @staticmethod
    def _shot_start_timestamp(shot: Mapping[str, Any]) -> str:
        try:
            seconds = max(0.0, float(shot.get("start_sec") or 0))
        except (TypeError, ValueError):
            seconds = 0.0
        minutes = int(seconds // 60)
        remaining = seconds - minutes * 60
        return f"{minutes:02d}:{remaining:06.3f}"

    @staticmethod
    def _shot_timing(shot: Mapping[str, Any], index: int) -> str:
        explicit = str(shot.get("time") or shot.get("range") or "").strip()
        if explicit:
            return explicit
        start = shot.get("start_sec")
        end = shot.get("end_sec")
        if start not in (None, "") and end not in (None, ""):
            return f"{start}–{end} 秒"
        return f"时间段 {index}"

    def _shot_description(
        self,
        shot: Mapping[str, Any],
        *,
        speaker_ids: Mapping[str, str] | None = None,
        subject_entries: list[dict[str, Any]],
        primary_entries: list[dict[str, Any]],
        voice_refs=None,
    ) -> str:
        parts: list[str] = []
        shot_size = str(shot.get("shot_size") or shot.get("framing") or "").strip()
        if shot_size:
            shot_sizes = {
                "wide": "全景",
                "long": "远景",
                "medium": "中景",
                "close-up": "近景",
                "closeup": "近景",
                "extreme close-up": "特写",
                "extreme_close_up": "特写",
            }
            parts.append(f"{shot_sizes.get(shot_size.lower(), shot_size)}镜头")
        cast_tags: set[str] = set()
        if 'visible_roles' in shot:
            cast = '、'.join(str(entry['subject_tag']) if (entry := self._subject_entry_for_name(str(role), subject_entries)) else str(role) for role in shot['visible_roles'])
            parts.append('画面人物：' + (cast or '无人物'))
            cast_tags = set(re.findall(r'<Subject\s+\d+>', cast))
        seen = set()
        for key in ("camera", "subject", "blocking", "description", "prompt", "action"):
            raw = shot.get(key)
            if isinstance(raw, (list, tuple)):
                value = "、".join(str(item or "").strip() for item in raw if str(item or "").strip()).strip()
            else:
                value = str(raw or "").strip()
            rendered = self._replace_subject_mentions(value, subject_entries, primary_entries=primary_entries)
            # The structured cast already names these people. A bare subject
            # or subject list adds no shot information; descriptive text does.
            if key == 'subject' and re.fullmatch(
                r'\s*<Subject\s+\d+>(?:\s*[、,，;；]\s*<Subject\s+\d+>)*\s*[。；;]?\s*', rendered
            ) and set(re.findall(r'<Subject\s+\d+>', rendered)) <= cast_tags:
                continue
            if not rendered or rendered in seen:
                continue
            seen.add(rendered)
            if rendered and key == "blocking":
                rendered = "人物调度：" + rendered
            if rendered and key == "camera":
                rendered = "镜头构图：" + rendered
            if rendered and rendered not in parts:
                parts.append(rendered)
        for dialogue in self._shot_dialogue_values(shot):
            speaker, scope, line, continuation = self._dialogue_parts(dialogue)
            if line:
                prefix = "接着上个 Shot 继续说，" if continuation else ""
                speaker_entry = self._subject_entry_for_name(speaker, subject_entries)
                speaker_ref = str(speaker_entry["subject_tag"]) if speaker_entry else speaker or "说话人"
                speaker_key = self._speaker_key(speaker, subject_entries)
                speaker_id = str((speaker_ids or {}).get(speaker_key) or "").strip()
                speaker_tag = f" ({speaker_id})" if speaker_id else ""
                scope_text = {"narration":"以画外旁白", "telephone":"在画外通过电话"}.get(
                    scope, "在画外" if scope.startswith("offscreen") else "")
                voice_id = dialogue.get('voice_asset_id') if isinstance(dialogue, Mapping) else None
                if voice_id and voice_refs is not None and voice_id not in voice_refs:
                    raise ValueError('对白音色参考绑定不存在：' + str(voice_id))
                audio_tag = (voice_refs or {}).get(voice_id) or (voice_refs or {}).get(speaker) or (voice_refs or {}).get(speaker_ref)
                if audio_tag:
                    parts.append(f'本句音色参考：{audio_tag}，仅用于{speaker_ref}的声音')
                if isinstance(dialogue, Mapping) and dialogue.get("delivery_spans"):
                    parts.append(self._replace_subject_mentions(render_delivery(dialogue, speaker_ref + speaker_tag), subject_entries, primary_entries=primary_entries))
                else:
                    parts.append(f"{prefix}{speaker_ref}{speaker_tag}{scope_text}说：<d>[Chinese]{line}</d>")
        audio = str(shot.get("audio") or shot.get("sound") or "").strip()
        if audio:
            parts.append(f"声音：{self._replace_subject_mentions(audio, subject_entries, primary_entries=primary_entries)}")
        continuity = str(shot.get("continuity") or "").strip()
        if continuity:
            parts.append(
                "连续性：" + self._replace_subject_mentions(continuity, subject_entries, primary_entries=primary_entries)
            )
        return "；".join(parts).strip("；")

    @staticmethod
    def _dialogue_parts(value: Any) -> tuple[str, str, str, bool]:
        if isinstance(value, Mapping):
            return (
                str(value.get("speaker") or value.get("speaker_name") or "").strip(),
                normalize_h3_scope(value.get("scope") or value.get("speech_type")),
                str(value.get("line") or value.get("text") or "").strip(),
                bool(value.get("continues_previous") or value.get("continuation")),
            )
        text = str(value or "").strip()
        match = re.match(r"^([^：:]{1,40})[：:]\s*(.+)$", text)
        if match:
            return match.group(1).strip(), "onscreen", match.group(2).strip(), False
        return "", "onscreen", text, False

    @staticmethod
    def _subject_entry_for_name(name: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        lowered = str(name or "").strip().lower()
        if not lowered:
            return None
        exact = [entry for entry in entries if lowered in {str(a).lower() for a in entry.get('aliases', [])}]
        if len(exact) == 1:
            return exact[0]
        return next(
            (
                entry
                for entry in entries
                if any(
                    lowered == str(alias).lower() or lowered in str(alias).lower() or str(alias).lower() in lowered
                    for alias in entry.get("aliases") or []
                    if len(str(alias)) >= 2
                )
            ),
            None,
        )

    def _speaker_key(self, speaker: str, entries: list[dict[str, Any]]) -> str:
        entry = self._subject_entry_for_name(speaker, entries)
        return str(entry["subject_tag"]) if entry else str(speaker or "说话人").strip()

    @staticmethod
    def _text_items(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, Iterable) or isinstance(value, (bytes, bytearray, Mapping)):
            return []
        items: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = str(item.get("text") or item.get("action") or item.get("description") or item.get("line") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                items.append(text)
        return items

    @staticmethod
    def _model_hints(segment: Mapping[str, Any]) -> Mapping[str, Any]:
        all_hints = segment.get("model_hints") if isinstance(segment, Mapping) else None
        hints = all_hints.get("minimax_h3_local_ref2va") if isinstance(all_hints, Mapping) else None
        return hints if isinstance(hints, Mapping) else {}

    @classmethod
    def _overall_soundscape(cls, segment: Mapping[str, Any]) -> str:
        hints = cls._model_hints(segment)
        # Shot sounds retain their timing in _render_shot; promoting them here
        # would turn a local cue into a whole-segment instruction.
        return str(hints.get("overall_soundscape") or segment.get("overall_soundscape") or "").strip()
