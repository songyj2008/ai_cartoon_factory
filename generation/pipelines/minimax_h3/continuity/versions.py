"""Read continuity from the explicitly selected screenplay without changing history."""
from __future__ import annotations

import copy

from .revisions import revision


def _selected_render(segment):
    selected_id = segment.get('active_render_id')
    if not selected_id:
        return None
    for render in segment.get('renders') or []:
        if not isinstance(render, dict) or render.get('render_id') != selected_id:
            continue
        output = render.get('output') or {}
        prompt = render.get('prompt_snapshot')
        if (render.get('status') != 'success' or render.get('deleted')
                or render.get('video_deleted') or output.get('video_deleted')
                or not isinstance(prompt, str) or not prompt.strip()):
            return None
        return render
    return None


def _complete_record(record):
    if not isinstance(record, dict) or record.get('schema_version') != 3:
        return False
    from .ledger import validate_record
    try:
        validate_record(record)
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def _matches(record, view):
    return (isinstance(record, dict)
            and record.get('source_revision') == revision(view)
            and ('context_id' not in record or record['context_id'] == view.get('scene_context_id')))


def _source_media(manifest):
    """Normalize explicit asset identities without manufacturing source evidence."""
    from ..contracts import H3GenerationConfig
    if not isinstance(manifest, list):
        raise TypeError('source media must be a list')
    for asset in manifest:
        if not isinstance(asset, dict):
            raise TypeError('source media must contain objects')
        identity = asset.get('asset_id') or asset.get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('source media needs an explicit asset identity')
        if asset.get('asset_id') and asset.get('id') and asset['asset_id'] != asset['id']:
            raise ValueError('source media has conflicting asset identities')
        if not isinstance(asset.get('path'), str) or not asset['path'].strip():
            raise ValueError('source media needs an explicit path')
        if asset.get('metadata') is not None and not isinstance(asset['metadata'], dict):
            raise TypeError('source media metadata must be an object')
        if asset.get('order') is not None and type(asset['order']) is not int:
            raise TypeError('source media order must be an integer')
        for key in ('role', 'label'):
            if asset.get(key) is not None and not isinstance(asset[key], str):
                raise TypeError('source media labels must be text')
        for key in ('use_video_audio', 'include_video_audio'):
            if asset.get(key) is not None and type(asset[key]) is not bool:
                raise TypeError('source media audio selection must be boolean')
    return H3GenerationConfig.from_mapping({'asset_manifest': manifest}).to_dict()['asset_manifest']


def _equivalent_draft_record(record, segment, view):
    """Rebind only a proven draft record with identical normalized source media."""
    context_id = segment.get('scene_context_id')
    if (not context_id or record.get('context_id') != context_id
            or view.get('scene_context_id') != context_id):
        return None
    from ..editor_draft import snapshot
    try:
        configs = [item.get('generation_config') or {} for item in (segment, view)]
        if any(not isinstance(config, dict) for config in configs):
            return None
        draft_media, selected_media = [_source_media(config.get('asset_manifest', [])) for config in configs]
        if draft_media != selected_media:
            return None
        if record.get('source_revision') != revision(segment):
            return None
        draft, selected = snapshot(segment), snapshot(view)
        if (draft['prompt'] != selected['prompt']
                or ('source_prompt' in record and record['source_prompt'] != draft['prompt'])):
            return None
    except (ValueError, TypeError):
        return None
    derived = copy.deepcopy(record)
    derived['source_revision'] = revision(view)
    return derived


def _equivalent_render_snapshot(record, segment, view):
    """Prove the frozen record's id-only media serialization by its exact hash.

    This proof belongs to the selected render, independently of later draft
    edits. It never substitutes a new draft's facts for historical facts.
    """
    from ..editor_draft import snapshot
    # A still-available exact draft is stronger evidence. Its failed full-media
    # comparison must not be bypassed by the narrower historical fingerprint.
    if record.get('source_revision') == revision(segment):
        return None
    if (record.get('context_id') != view.get('scene_context_id')
            or record.get('source_prompt') != snapshot(view)['prompt']):
        return None
    legacy = copy.deepcopy(view)
    try:
        media = legacy['generation_config']['asset_manifest']
        _source_media(media)
        for asset in media:
            if asset.get('type') != 'image':
                continue
            # Submission normalizes the explicit id alias to asset_id. Restore
            # that representation only to verify the frozen source fingerprint.
            identity = asset.get('asset_id') or asset.get('id')
            asset['id'] = identity
            asset.pop('asset_id', None)
        if record.get('source_revision') != revision(legacy):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    derived = copy.deepcopy(record)
    derived['source_revision'] = revision(view)
    return derived


def source_segment(segment, *, prefer_render=True):
    """Return an isolated source view; never infer or change the selected render."""
    prefer_render = (prefer_render and not segment.get('_continuity_use_draft')
                     and segment.get('h3_continuity_source') != 'draft')
    view = copy.deepcopy(segment)
    view.pop('_continuity_use_draft', None)
    view.pop('_continuity_source', None)
    render = _selected_render(segment) if prefer_render else None
    if render is None:
        view['_continuity_source'] = {'kind': 'draft', 'render_id': None, 'revision': revision(view)}
        return view

    from ..shot_editor import parse_prompt

    prompt = render['prompt_snapshot']
    manifest = copy.deepcopy(render.get('asset_manifest') or [])
    parsed = parse_prompt(prompt, view['segment_uid'], manifest)
    requested = render.get('requested_media_spec') or {}
    duration = requested.get('duration_sec')
    if duration is None:
        duration = render.get('requested_duration_sec', render.get('requested_duration'))
    if duration is None:
        duration = sum(shot['duration_cs'] for shot in parsed['shots']) / 100
    config = view['generation_config'] = copy.deepcopy(view.get('generation_config') or {})
    config.update(prompt_override=prompt, asset_manifest=manifest, duration_sec=duration)
    view['compiled_prompt_snapshot'] = prompt
    # A selected historical screenplay can only carry its own frozen coverage
    # proof. A later editing draft's proof must never attest to an older render.
    view['h3_scene_framing'] = copy.deepcopy(
        (render.get('metadata') or {}).get('h3_scene_framing_snapshot') or {})
    view['_continuity_source'] = {'kind': 'render', 'render_id': render['render_id'], 'revision': revision(view)}

    record = source_record(segment, view)
    ids = [shot.get('shot_uid') for shot in record.get('shots') or [] if isinstance(shot, dict)]
    if (len(ids) != len(parsed['shots']) or any(not isinstance(uid, str) or not uid for uid in ids)
            or len(ids) != len(set(ids))):
        ids = [shot['uid'] for shot in parsed['shots']]
    editor = view['h3_shot_editor'] = copy.deepcopy(view.get('h3_shot_editor') or {})
    editor['shot_uids'] = ids
    return view


def source_record(segment, view=None):
    """Return this source's record; old render snapshots remain extraction evidence."""
    if view is None:
        view = source_segment(segment)
    source = view.get('_continuity_source') or {}
    if source.get('kind') != 'render':
        return copy.deepcopy(segment.get('h3_continuity') or {})
    render_id = source['render_id']
    versions = segment.get('h3_continuity_versions') or {}
    cached = versions.get(render_id)
    record = segment.get('h3_continuity') or {}
    render = next((item for item in segment.get('renders') or []
                   if isinstance(item, dict) and item.get('render_id') == render_id), None)
    original = (render.get('metadata') or {}).get('h3_continuity_snapshot') if render else None
    candidates = (cached, record, original)
    # Exact versions are authoritative, including invalid records that callers
    # must reject rather than silently replacing with older render facts.
    for candidate in candidates:
        if _matches(candidate, view):
            return copy.deepcopy(candidate)
    for candidate in candidates:
        if not _complete_record(candidate):
            continue
        derived = _equivalent_draft_record(candidate, segment, view)
        if derived is not None:
            return derived
    if _complete_record(original):
        derived = _equivalent_render_snapshot(original, segment, view)
        if derived is not None:
            return derived
    return copy.deepcopy(original) if _complete_record(original) else {}


def store_source_record(segment, view, record):
    """Store derived records separately from immutable render submission snapshots."""
    source = view.get('_continuity_source') or {}
    if source.get('kind') == 'render':
        versions = segment.setdefault('h3_continuity_versions', {})
        versions[source['render_id']] = copy.deepcopy(record)
        if revision(segment) == revision(view):
            segment['h3_continuity'] = copy.deepcopy(record)
    else:
        segment['h3_continuity'] = copy.deepcopy(record)
