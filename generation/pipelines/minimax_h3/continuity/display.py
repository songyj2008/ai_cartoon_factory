"""Project-scoped display labels; continuity and asset IDs remain unchanged."""
import copy


def project_role_names():
    from services.project_bible import load_project_bible, character_entries
    names = {}
    for role in character_entries(load_project_bible()):
        name = role.get('display_name') or role.get('name_cn') or role.get('name')
        if not name:
            continue
        for key in (role.get('id'), role.get('asset_identity_id')):
            if key:
                names[str(key)] = name
    return names


def scene_display_contexts(data):
    """Resolve labels from registered locations without changing continuity keys."""
    from services.project_bible import load_project_bible
    backgrounds = {b['id']: b.get('display_name') or b.get('name_cn')
                   for b in load_project_bible().get('allowed_backgrounds', []) if b.get('id')}
    scenes = copy.deepcopy((data.get('h3_scene_continuity') or {}).get('contexts', {}))
    for index, (key, scene) in enumerate(scenes.items(), 1):
        members = [r for r in data.get('segments', []) if r.get('scene_context_id') == key]
        scene_ids = {r['scene_id'] for r in members if r.get('scene_id')}
        assets = [a for r in members for a in (r.get('generation_config') or {}).get('asset_manifest', [])
                  if (a.get('metadata') or {}).get('asset_kind') == 'background']
        scene_ids.update((a.get('metadata') or {}).get('scene_id') for a in assets)
        name = str(scene.get('name') or '').strip()
        # Keep authored group names (e.g. the same office at a different time).
        if name and name != key and name not in scene_ids and name not in backgrounds:
            scene['display_name'] = name
            continue
        resolved = backgrounds.get(name)
        for row in members:
            resolved = resolved or backgrounds.get(row.get('scene_id'))
        for asset in assets:
            metadata = asset.get('metadata') or {}
            resolved = resolved or backgrounds.get(metadata.get('scene_id')) or asset.get('label') or asset.get('role')
        scene['display_name'] = resolved or f'未命名场景 {index}'
    return scenes


def framing_display_snapshot(jobs, target):
    """Report saved framing separately from saved world facts and video pixels."""
    record = copy.deepcopy(target.get('h3_scene_framing'))
    if not target.get('scene_context_id'):
        return {'scene_framing': record, 'framing_status': {'ready': False, 'message': '未加入连续场景'}}
    if not record:
        return {'scene_framing': None, 'framing_status': {'ready': False, 'message': '当前保存稿尚无全员取景核对记录'}}
    from .readiness import read_ready_context
    from .framing import validate_framing_record
    try:
        validate_framing_record(target, read_ready_context(jobs, target['segment_uid'], target_source='draft'))
    except (ValueError, KeyError, TypeError) as exc:
        return {'scene_framing': record, 'framing_status': {'ready': False, 'message': str(exc)}}
    return {'scene_framing': record, 'framing_status': {'ready': True, 'message': '当前保存稿的全员取景安排已核对；成片尚需另行检查'}}

