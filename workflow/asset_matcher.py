"""Automatic per-beat asset matching from project-selected characters/backgrounds."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from asset_index import load_asset_index
from services.project_bible import load_project_bible
from workflow.asset_resolver import identity_image_options


_LATIN_BACKGROUND_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "de",
    "del",
    "der",
    "des",
    "di",
    "el",
    "en",
    "for",
    "la",
    "las",
    "le",
    "les",
    "of",
    "on",
    "the",
    "to",
    "y",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _terms(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        parts = [part.strip() for part in re.split(r"[,，、。:：/|;；\s]+", item) if part.strip()]
        result.extend(parts or [item])
    return list(dict.fromkeys(result))


def _norm(value: Any) -> str:
    return _text(value).lower()


def _contains(haystack: str, needle: str) -> bool:
    needle = _norm(needle)
    if not needle or len(needle) < 2:
        return False
    # Latin identifiers/words must match as complete lexical units.  A raw
    # substring check made the Spanish alias token "de" match the Shot size
    # "wide", which incorrectly selected Madrid's Royal Palace.
    if re.fullmatch(r"[a-z0-9_+.-]+", needle):
        return re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack) is not None
    return needle in haystack


def _informative_background_term(value: Any) -> bool:
    term = _norm(value)
    return not (re.fullmatch(r"[a-z]+", term) and term in _LATIN_BACKGROUND_STOPWORDS)


def _name_variants(value: Any) -> list[str]:
    """Derive stable human-name aliases without story-specific dictionaries."""
    text = _text(value)
    if not text:
        return []
    values = [text]
    without_number = re.sub(r"^\s*\d+\s*号\s*", "", text).strip()
    if without_number and without_number != text:
        values.append(without_number)
    for separator in ("·", "・", " "):
        if separator in text:
            tail = text.rsplit(separator, 1)[-1].strip()
            if len(tail) >= 2:
                values.append(tail)
    return list(dict.fromkeys(value for value in values if len(value) >= 2))


def _beat_text(beat: dict[str, Any]) -> str:
    parts = [
        beat.get("title"),
        beat.get("plot"),
        beat.get("source_excerpt"),
    ]
    for key in ("action_units", "event_units", "dialogue_units"):
        for item in beat.get(key) or []:
            if isinstance(item, dict):
                for field in ("action", "dialogue", "line", "text", "object", "description"):
                    parts.append(item.get(field))
            else:
                parts.append(item)
    for item in beat.get("background_extras") or []:
        if isinstance(item, dict):
            parts.append(item.get("description") or item.get("appearance"))
        else:
            parts.append(item)
    return _norm(" ".join(_text(x) for x in parts if _text(x)))


def _identity_by_id(asset_index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    return {str(item.get("id") or ""): item for item in identities if isinstance(item, dict)}


def _background_by_id(asset_index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in asset_index.get("backgrounds") or [] if isinstance(item, dict)}


def _character_tokens(entry: dict[str, Any], identity: dict[str, Any] | None) -> list[tuple[str, int, str]]:
    tokens: list[tuple[str, int, str]] = []
    for key, weight in (("id", 100), ("asset_identity_id", 90), ("display_name", 80), ("name_cn", 80), ("role", 25)):
        value = entry.get(key)
        if value:
            tokens.append((str(value), weight, key))
    for alias in _terms(entry.get("aliases")):
        if _informative_background_term(alias):
            tokens.append((alias, 70, "alias"))
    for key in ("display_name", "name_cn", "name"):
        for variant in _name_variants(entry.get(key)):
            tokens.append((variant, 70, "name_variant"))
    if identity:
        for key, weight in (("id", 90), ("display_name", 70), ("name_cn", 70), ("name", 50), ("description_cn", 25)):
            value = identity.get(key)
            if value:
                tokens.append((str(value), weight, key))
        for alias in _terms(identity.get("aliases")):
            tokens.append((alias, 65, "identity_alias"))
        for key in ("display_name", "name_cn", "name"):
            for variant in _name_variants(identity.get(key)):
                tokens.append((variant, 65, "identity_name_variant"))
        for value in _terms(identity.get("use_for")):
            tokens.append((value, 25, "use_for"))
        for image in identity.get("images") or []:
            if isinstance(image, dict):
                for value in _terms(image.get("tags_cn")) + _terms(image.get("use_for")):
                    tokens.append((value, 20, "image_meta"))
    return tokens


def _background_tokens(entry: dict[str, Any], background: dict[str, Any] | None) -> list[tuple[str, int, str]]:
    tokens: list[tuple[str, int, str]] = []
    for key, weight in (("id", 100), ("asset_background_id", 95), ("display_name", 85), ("name_cn", 80)):
        value = entry.get(key)
        if value:
            tokens.append((str(value), weight, key))
    for alias in _terms(entry.get("aliases")):
        tokens.append((alias, 70, "alias"))
    if background:
        for key, weight in (("id", 95), ("display_name", 80), ("name_cn", 80), ("name", 60), ("description_cn", 35)):
            value = background.get(key)
            if value:
                tokens.append((str(value), weight, key))
        for alias in _terms(background.get("aliases")):
            if _informative_background_term(alias):
                tokens.append((alias, 70, "asset_alias"))
        for value in _terms(background.get("tags_cn")):
            tokens.append((value, 45, "tag"))
        for value in _terms(background.get("use_for")):
            tokens.append((value, 45, "use_for"))
    return tokens


def _image_variant_tokens(image: dict[str, Any]) -> list[tuple[str, int, str]]:
    tokens: list[tuple[str, int, str]] = []
    image_id = _text(image.get("id"))
    if image_id:
        tokens.append((image_id, 110, "image_id"))
    for value in _terms(image.get("tags_cn")):
        tokens.append((value, 75, "image_tag"))
    for value in _terms(image.get("use_for")):
        tokens.append((value, 85, "image_use"))
    for value in _terms(image.get("pose")):
        tokens.append((value, 40, "image_pose"))
    for value in _terms(image.get("description_cn")):
        tokens.append((value, 30, "image_description"))
    return tokens


def _score(tokens: list[tuple[str, int, str]], text: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    seen: set[str] = set()
    for token, weight, source in tokens:
        token_text = _text(token)
        key = token_text.lower()
        if key in seen:
            continue
        seen.add(key)
        if _contains(text, token_text):
            score += int(weight)
            reasons.append(f"{source}:{token_text}")
    return score, reasons[:8]


def _project_characters(bible: dict[str, Any]) -> list[dict[str, Any]]:
    return list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])


def _character_by_role_id(bible: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in _project_characters(bible)
        if isinstance(item, dict) and item.get("id")
    }


def _match_reference_images(
    roles: list[str],
    text: str,
    bible: dict[str, Any],
    identity_by_id: dict[str, dict[str, Any]],
    preferred: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    preferred = preferred if isinstance(preferred, dict) else {}
    character_by_role = _character_by_role_id(bible)
    selected: dict[str, str] = {}
    details: list[dict[str, Any]] = []

    for role_id in roles:
        character = character_by_role.get(str(role_id))
        if not character:
            continue
        identity_id = str(character.get("asset_identity_id") or "")
        identity = identity_by_id.get(identity_id)
        if not identity:
            continue
        options = identity_image_options(identity)
        if not options:
            continue

        requested_id = _text(preferred.get(str(role_id)))
        requested = next(
            (option for option in options if _text(option.get("id")) == requested_id),
            None,
        )
        if requested:
            chosen = requested
            score = 0
            reasons = ["manual:image_variant"]
        else:
            scored: list[tuple[int, bool, dict[str, Any], list[str]]] = []
            for option in options:
                score, reasons = _score(_image_variant_tokens(option), text)
                scored.append((score, bool(option.get("is_primary")), option, reasons))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            score, _is_primary, chosen, reasons = scored[0]
            if score <= 0:
                chosen = next((option for option in options if option.get("is_primary")), options[0])
                reasons = ["fallback:primary_reference"]

        image_id = _text(chosen.get("id"))
        if not image_id:
            continue
        selected[str(role_id)] = image_id
        details.append(
            {
                "role_id": str(role_id),
                "asset_identity_id": identity_id,
                "image_id": image_id,
                "score": int(score or 0),
                "reason": reasons,
                "is_primary": bool(chosen.get("is_primary")),
            }
        )
    return selected, details


def match_beat_assets(
    beat: dict[str, Any],
    bible: dict[str, Any] | None = None,
    asset_index: dict[str, Any] | None = None,
    fallback_background: dict[str, Any] | None = None,
    reference_role_limit: int = 4,
    model_id: str = "",
) -> dict[str, Any]:
    bible = bible or load_project_bible()
    asset_index = asset_index or load_asset_index()
    identity_by_id = _identity_by_id(asset_index)
    background_by_id = _background_by_id(asset_index)
    text = _beat_text(beat)
    item = copy.deepcopy(beat if isinstance(beat, dict) else {})
    auto_match = item.get("auto_asset_match") if isinstance(item.get("auto_asset_match"), dict) else {}
    if auto_match.get("manual_override") is True:
        valid_roles = {
            str(entry.get("id") or "")
            for entry in _project_characters(bible)
            if isinstance(entry, dict) and entry.get("id")
        }
        valid_scene_list = [
            str(entry.get("id") or "")
            for entry in bible.get("allowed_backgrounds") or []
            if isinstance(entry, dict) and entry.get("id")
        ]
        valid_scenes = set(valid_scene_list)
        manual_roles = [role for role in _as_list(item.get("reference_roles")) if role in valid_roles][:reference_role_limit]
        manual_image_ids, image_match_details = _match_reference_images(
            manual_roles,
            text,
            bible,
            identity_by_id,
            item.get("reference_image_ids") if isinstance(item.get("reference_image_ids"), dict) else {},
        )
        manual_scene = _text(item.get("scene_id"))
        if manual_scene not in valid_scenes:
            manual_scene = valid_scene_list[0] if valid_scene_list else ""
        item["reference_roles"] = manual_roles
        item["reference_image_ids"] = manual_image_ids
        item["scene_id"] = manual_scene
        constraints = item.get("identity_constraints") if isinstance(item.get("identity_constraints"), dict) else {}
        item["identity_constraints"] = {
            **constraints,
            "registered_identity_only": True,
            "locked_roles": list(manual_roles),
        }
        item["auto_asset_match"] = {
            **auto_match,
            "manual_override": True,
            "manual_selection": {
                "reference_roles": list(manual_roles),
                "reference_image_ids": dict(manual_image_ids),
                "scene_id": manual_scene,
            },
            "character_images": image_match_details,
        }
        return item

    character_matches = []
    for character in _project_characters(bible):
        if not isinstance(character, dict):
            continue
        identity = identity_by_id.get(str(character.get("asset_identity_id") or ""))
        score, reasons = _score(_character_tokens(character, identity), text)
        if score > 0:
            character_matches.append((score, character, reasons))
    character_matches.sort(key=lambda x: x[0], reverse=True)
    matched_roles = [str(entry.get("id")) for score, entry, _reasons in character_matches[:reference_role_limit] if entry.get("id") and score >= 50]
    reference_image_ids, image_match_details = _match_reference_images(
        matched_roles,
        text,
        bible,
        identity_by_id,
    )

    is_h3 = str(model_id or "").strip() == "minimax_h3_local_ref2va"
    h3_backgrounds = _match_h3_shot_backgrounds(item, bible, background_by_id) if is_h3 else []
    if h3_backgrounds:
        first = h3_backgrounds[0]
        bg_entry = next(
            (
                entry
                for entry in bible.get("allowed_backgrounds") or []
                if isinstance(entry, dict) and str(entry.get("id") or "") == str(first.get("scene_id") or "")
            ),
            {},
        )
        bg_score = int(first.get("score") or 0)
        bg_reasons = list(first.get("reason") or [])
        item["scene_id"] = str(first.get("scene_id") or "")
    else:
        background_matches = []
        for background_entry in bible.get("allowed_backgrounds") or []:
            if not isinstance(background_entry, dict):
                continue
            bg = background_by_id.get(str(background_entry.get("asset_background_id") or ""))
            score, reasons = _score(_background_tokens(background_entry, bg), text)
            background_matches.append((score, background_entry, reasons))
        background_matches.sort(key=lambda x: x[0], reverse=True)
        best_background = background_matches[0] if background_matches else (0, {}, [])
        bg_score, bg_entry, bg_reasons = best_background
        if is_h3 and bg_score < 60:
            bg_entry, bg_score, bg_reasons = {}, 0, []
            item["scene_id"] = ""
        elif fallback_background and bg_score < 60:
            bg_entry = fallback_background
            bg_score = 0
            bg_reasons = ["fallback:previous_background"]
        if bg_entry.get("id"):
            item["scene_id"] = str(bg_entry.get("id"))

    existing_roles = _as_list(item.get("important_roles"))
    if matched_roles:
        item["visible_roles"] = matched_roles
        item["reference_roles"] = matched_roles
        item["reference_image_ids"] = reference_image_ids
        item["important_roles"] = list(dict.fromkeys(matched_roles + existing_roles))
        item["mentioned_roles"] = [role for role in _as_list(item.get("mentioned_roles")) if role not in matched_roles]
    else:
        item["visible_roles"] = []
        item["reference_roles"] = []
        item["reference_image_ids"] = {}
        item["important_roles"] = existing_roles
        item["mentioned_roles"] = list(dict.fromkeys(existing_roles + _as_list(item.get("mentioned_roles"))))

    item["offscreen_speakers"] = [role for role in _as_list(item.get("offscreen_speakers")) if role in item["important_roles"] and role not in item["visible_roles"]]
    identity_constraints = item.get("identity_constraints") if isinstance(item.get("identity_constraints"), dict) else {}
    item["identity_constraints"] = {
        **identity_constraints,
        "registered_identity_only": True,
        "locked_roles": list(item.get("reference_roles") or []),
    }
    item["auto_asset_match"] = {
        "background": {
            "scene_id": str(bg_entry.get("id") or ""),
            "asset_background_id": str(bg_entry.get("asset_background_id") or ""),
            "display_name": str(bg_entry.get("display_name") or ""),
            "score": int(bg_score or 0),
            "reason": bg_reasons,
        },
        "characters": [
            {
                "role_id": str(entry.get("id") or ""),
                "asset_identity_id": str(entry.get("asset_identity_id") or ""),
                "display_name": str(entry.get("display_name") or ""),
                "score": int(score or 0),
                "reason": reasons,
            }
            for score, entry, reasons in character_matches[:reference_role_limit]
        ],
        "character_images": image_match_details,
    }
    if is_h3:
        item["auto_asset_match"]["backgrounds"] = h3_backgrounds
    return item


def _match_h3_shot_backgrounds(
    beat: dict[str, Any],
    bible: dict[str, Any],
    background_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match zero or more backgrounds to H3 shots, never a default scene."""
    hints = beat.get("model_hints") if isinstance(beat.get("model_hints"), dict) else {}
    h3 = hints.get("minimax_h3_local_ref2va") if isinstance(hints, dict) else {}
    shots = h3.get("shots") if isinstance(h3, dict) and isinstance(h3.get("shots"), list) else []
    if not shots:
        return []
    allowed = [entry for entry in bible.get("allowed_backgrounds") or [] if isinstance(entry, dict)]
    allowed_by_scene = {str(entry.get("id") or ""): entry for entry in allowed if entry.get("id")}
    matches: list[dict[str, Any]] = []
    by_asset_id: dict[str, dict[str, Any]] = {}
    for shot_index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            continue
        explicit_scene = str(shot.get("scene_id") or "").strip()
        semantic_shot = {key: value for key, value in shot.items() if key != "scene_id"}
        shot_text = _norm(json.dumps(semantic_shot, ensure_ascii=False))
        beat_context = _norm(f"{beat.get('title') or ''} {beat.get('plot') or ''}")

        def score_backgrounds(text: str) -> list[tuple[int, dict[str, Any], list[str]]]:
            values: list[tuple[int, dict[str, Any], list[str]]] = []
            for candidate in allowed:
                bg = background_by_id.get(str(candidate.get("asset_background_id") or ""))
                candidate_score, candidate_reasons = _score(_background_tokens(candidate, bg), text)
                values.append((candidate_score, candidate, candidate_reasons))
            values.sort(key=lambda value: value[0], reverse=True)
            return values

        scored = score_backgrounds(shot_text)
        if not scored or scored[0][0] < 60:
            scored = score_backgrounds(f"{beat_context} {shot_text}")
        score, entry, reasons = scored[0] if scored else (0, {}, [])
        if explicit_scene and explicit_scene in allowed_by_scene:
            explicit_entry = allowed_by_scene[explicit_scene]
            explicit_bg = background_by_id.get(str(explicit_entry.get("asset_background_id") or ""))
            explicit_score, explicit_reasons = _score(_background_tokens(explicit_entry, explicit_bg), shot_text)
            if explicit_score < 60:
                explicit_score, explicit_reasons = _score(
                    _background_tokens(explicit_entry, explicit_bg),
                    f"{beat_context} {shot_text}",
                )
            if explicit_score >= 60:
                score, entry = explicit_score, explicit_entry
                reasons = ["explicit:shot_scene_id", *explicit_reasons]
        if score < 60:
            continue
        asset_background_id = str(entry.get("asset_background_id") or "").strip()
        if not asset_background_id:
            continue
        existing = by_asset_id.get(asset_background_id)
        if existing:
            existing["shot_indices"].append(shot_index)
            existing["score"] = max(int(existing.get("score") or 0), int(score or 0))
            existing["reason"] = list(dict.fromkeys(list(existing.get("reason") or []) + list(reasons or [])))
            continue
        result = {
            "scene_id": str(entry.get("id") or ""),
            "asset_background_id": asset_background_id,
            "display_name": str(entry.get("display_name") or ""),
            "score": int(score or 0),
            "reason": list(reasons or []),
            "shot_indices": [shot_index],
        }
        by_asset_id[asset_background_id] = result
        matches.append(result)
    return matches


def match_beats_assets(beats_data: dict[str, Any] | None) -> dict[str, Any]:
    data = copy.deepcopy(beats_data if isinstance(beats_data, dict) else {})
    from generation.model_rules import rules_for_beats_data

    model_rules = rules_for_beats_data(data)
    reference_role_limit = model_rules.max_reference_roles or 4
    bible = load_project_bible()
    asset_index = load_asset_index()
    beats = data.get("beats") if isinstance(data.get("beats"), list) else []
    matched_beats = []
    last_background: dict[str, Any] | None = None
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        matched = match_beat_assets(
            beat,
            bible=bible,
            asset_index=asset_index,
            fallback_background=None if model_rules.model_id == "minimax_h3_local_ref2va" else last_background,
            reference_role_limit=reference_role_limit,
            model_id=model_rules.model_id,
        )
        matched_beats.append(matched)
        scene_id = str(matched.get("scene_id") or "")
        for entry in bible.get("allowed_backgrounds") or []:
            if isinstance(entry, dict) and str(entry.get("id") or "") == scene_id:
                last_background = entry
                break
    data["beats"] = matched_beats
    return data
