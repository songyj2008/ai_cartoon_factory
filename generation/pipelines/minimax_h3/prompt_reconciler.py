"""Keep an edited H3 prompt aligned with its ordered reference manifest."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler


_SECTION_RE = re.compile(r"(?m)^【([^】]+)】\s*$")


class H3PromptReconciler:
    """Rebuild compiler-owned sections while preserving the edited screenplay.

    Asset ids are the stable identity.  Picture/Subject numbers are only a
    projection of the current ordered manifest and are therefore remapped
    whenever an image is inserted, removed, or reordered.
    """

    def __init__(self, compiler: H3PromptCompiler | None = None) -> None:
        self.compiler = compiler or H3PromptCompiler()
        self.binder = H3AssetBinder()

    def reconcile(
        self,
        prompt: str,
        old_config: H3GenerationConfig,
        new_config: H3GenerationConfig,
    ) -> str:
        text = str(prompt or "").strip()
        old_bindings = self.binder.bind(old_config)
        new_bindings = self.binder.bind(new_config)
        # Compiler-owned sections always contain every old Subject/Picture tag,
        # so remove them before deciding whether a deleted asset is still used
        # by the editable screenplay.  Never inject diagnostic text into the
        # user's prompt: an unresolved reference is a configuration error.
        without_managed = self._without_sections(text, {"主体定义", "参考保留关系"})
        without_managed = self._unwrap_legacy_removed_markers(without_managed)
        without_managed = self._remap_reference_tags(without_managed, old_bindings, new_bindings)

        subject_lines = self.compiler._subject_lines(new_bindings)
        entries = self.compiler._subject_reference_entries(new_bindings)
        without_managed = self.compiler._replace_subject_mentions(without_managed, entries)
        retention_lines = self.compiler._retention_lines_from_prompt(without_managed, new_bindings)
        return self._compose(without_managed, subject_lines, retention_lines)

    def _remap_reference_tags(self, text: str, old_bindings: Any, new_bindings: Any) -> str:
        old_picture = {binding.asset.asset_id: binding.prompt_tag for binding in old_bindings.images}
        new_picture = {binding.asset.asset_id: binding.prompt_tag for binding in new_bindings.images}
        old_subject = {
            str(entry["asset_id"]): str(entry["subject_tag"])
            for entry in self.compiler._subject_reference_entries(old_bindings)
        }
        new_subject = {
            str(entry["asset_id"]): str(entry["subject_tag"])
            for entry in self.compiler._subject_reference_entries(new_bindings)
        }
        asset_names = {
            binding.asset.asset_id: str(binding.asset.role or binding.asset.label or binding.asset.asset_id)
            for binding in old_bindings.images
        }

        # Asset ids are intended to be stable, but older generated manifests
        # and the project picker used different id namespaces (``asset_2``,
        # ``project:character:...``, ``char_...``) for the same file.  A plain
        # fullscreen save can therefore round-trip an unchanged picture under
        # another id.  Match the unique normalized path as a migration-safe
        # identity fallback so an unchanged reference is never reported as
        # removed.  New manifests still keep their own ordering/tags.
        new_by_path: dict[str, list[str]] = {}
        new_by_identity: dict[str, list[str]] = {}
        for binding in new_bindings.images:
            key = self._asset_path_key(binding.asset.path)
            if key:
                new_by_path.setdefault(key, []).append(binding.asset.asset_id)
            ident = str((binding.asset.metadata or {}).get("identity_id") or "").strip()
            if ident:
                new_by_identity.setdefault(ident, []).append(binding.asset.asset_id)

        matched_new_ids: dict[str, str] = {}
        for binding in old_bindings.images:
            old_id = binding.asset.asset_id
            if old_id in new_picture:
                matched_new_ids[old_id] = old_id
                continue
            ident = str((binding.asset.metadata or {}).get("identity_id") or "").strip()
            if ident:
                candidates = new_by_identity.get(ident, [])
                if len(candidates) == 1:
                    matched_new_ids[old_id] = candidates[0]
                    continue
            candidates = new_by_path.get(self._asset_path_key(binding.asset.path), [])
            if len(candidates) == 1:
                matched_new_ids[old_id] = candidates[0]

        replacements: list[tuple[str, str]] = []
        unresolved_names: set[str] = set()
        for asset_id, old_tag in old_picture.items():
            new_id = matched_new_ids.get(asset_id, asset_id)
            target = new_picture.get(new_id)
            if target:
                replacements.append((old_tag, target))
            elif re.search(re.escape(old_tag), text, flags=re.IGNORECASE):
                unresolved_names.add(asset_names.get(asset_id, asset_id))
        for asset_id, old_tag in old_subject.items():
            new_id = matched_new_ids.get(asset_id, asset_id)
            target = new_subject.get(new_id)
            if target:
                replacements.append((old_tag, target))
            elif re.search(re.escape(old_tag), text, flags=re.IGNORECASE):
                unresolved_names.add(asset_names.get(asset_id, asset_id))
        old_entries = self.compiler._subject_reference_entries(old_bindings)
        for entry in old_entries:
            asset_id = str(entry["asset_id"])
            new_id = matched_new_ids.get(asset_id, asset_id)
            target = new_subject.get(new_id)
            for alias in entry.get("aliases") or []:
                alias_text = str(alias).strip()
                if len(alias_text) >= 2:
                    if target:
                        replacements.append((alias_text, target))
                    elif alias_text in text:
                        unresolved_names.add(str(entry["name"]))

        if unresolved_names:
            names = "、".join(sorted(unresolved_names))
            raise ValueError(f"参考素材绑定失效：{names}；请重新选择素材，或先从提示词中删除相关引用")

        placeholders: dict[str, str] = {}
        result = text
        for index, (old_tag, new_tag) in enumerate(sorted(replacements, key=lambda value: len(value[0]), reverse=True)):
            placeholder = f"__H3_REFERENCE_REMAP_{index}__"
            updated, count = re.subn(re.escape(old_tag), placeholder, result, flags=re.IGNORECASE)
            if count:
                result = updated
                placeholders[placeholder] = new_tag
        for placeholder, new_tag in placeholders.items():
            result = result.replace(placeholder, new_tag)
        return result

    @staticmethod
    def _unwrap_legacy_removed_markers(text: str) -> str:
        """Migrate prompts written by the old diagnostic-marker behavior."""

        return re.sub(r"\[已移除(?:主体|图片)：([^\]]+)\]", r"\1", str(text or ""))

    @staticmethod
    def _asset_path_key(value: Any) -> str:
        """Return a platform-neutral key for an already selected local file."""

        text = str(value or "").strip().replace("\\", "/")
        if not text:
            return ""
        # ``Path`` collapses harmless ``.`` segments; casefold matches the
        # Windows runtime without requiring the file to exist during tests.
        return str(Path(text)).replace("\\", "/").casefold()

    @staticmethod
    def _sections(text: str) -> list[tuple[str, str]]:
        matches = list(_SECTION_RE.finditer(str(text or "")))
        values: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            values.append((match.group(1).strip(), text[match.end():end].strip()))
        return values

    def _without_sections(self, text: str, names: set[str]) -> str:
        return "\n\n".join(
            f"【{name}】\n{body}" for name, body in self._sections(text) if name not in names
        ).strip()

    def _compose(self, original: str, subjects: list[str], retention: list[str]) -> str:
        sections = self._sections(original)
        unmanaged = [(name, body) for name, body in sections if name not in {"主体定义", "参考保留关系"}]
        result: list[tuple[str, str]] = []
        inserted_subjects = False
        inserted_retention = False
        for name, body in unmanaged:
            if not inserted_subjects and name == "任务概述":
                if subjects:
                    result.append(("主体定义", "\n".join(subjects)))
                inserted_subjects = True
            result.append((name, body))
            if name == "任务概述" and not inserted_retention:
                if retention:
                    result.append(("参考保留关系", "\n".join(retention)))
                inserted_retention = True
        if not inserted_subjects and subjects:
            result.insert(0, ("主体定义", "\n".join(subjects)))
        if not inserted_retention and retention:
            position = 1 if result and result[0][0] == "主体定义" else 0
            result.insert(position, ("参考保留关系", "\n".join(retention)))
        if not result:
            return original.strip()
        return "\n\n".join(f"【{name}】\n{body}" for name, body in result if body.strip()).strip()
