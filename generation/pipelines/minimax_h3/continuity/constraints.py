"""Shared authored scene instructions, separate from measured character state."""
import uuid

DEFAULT = '已明确的人物与固定空间位置的关系持续有效。摄影机可以改变位置、方向和景别，不能仅因构图变化重新安排人物位置。没有明确移动动作时，保持此前位置和姿势；未入画不代表离场。发生剧情移动后，继承动作完成后的状态，不重新恢复为开场初态。'


def scene_constraints(scene):
    return scene.get('spatial_constraints', DEFAULT)


def save_constraints(scene, payload):
    if 'spatial_constraints' not in payload:
        return
    from ..editor_draft import EditorConflictError
    if payload.get('constraints_revision') != scene.get('constraints_revision'):
        raise EditorConflictError('场景共用约束已更新，请读取最新初态后再保存', 'SCENE_INITIAL_CONFLICT')
    value = payload['spatial_constraints']
    if not isinstance(value, str):
        raise ValueError('场景共用约束必须为文字')
    value = value.strip()
    if value != scene_constraints(scene):
        scene['spatial_constraints'] = value
        scene['constraints_revision'] = uuid.uuid4().hex


def background_signature(manifest):
    """Bind authored layout to background identities, not ordering or labels."""
    from .checkpoints import digest
    backgrounds = []
    for asset in manifest or []:
        metadata = asset.get('metadata') or {}
        if asset.get('type') == 'image' and metadata.get('asset_kind') == 'background':
            backgrounds.append({key: value for key, value in {
                'path': asset.get('path'), 'background_id': metadata.get('background_id'),
                'scene_id': metadata.get('scene_id'),
            }.items() if value is not None})
    return digest(sorted(backgrounds, key=lambda row: repr(sorted(row.items()))))


def scene_layout(scene, manifest):
    """Expose layout provenance without treating a replacement image as the old one."""
    saved = scene.get('scene_layout') or {}
    current = background_signature(manifest)
    description = saved.get('description', '')
    return {'description': description, 'background_signature': saved.get('background_signature'),
            'current_background_signature': current,
            'current': not description or saved.get('background_signature') == current}


def save_scene_layout(scene, payload, manifest):
    """An explicit scene save binds the provided layout to its current background."""
    if 'scene_layout' not in payload:
        return False
    from ..editor_draft import EditorConflictError
    if payload.get('layout_revision') != scene.get('layout_revision'):
        raise EditorConflictError('场景布局已更新，请读取最新初态后再保存', 'SCENE_INITIAL_CONFLICT')
    value = payload['scene_layout']
    if not isinstance(value, str):
        raise ValueError('场景布局必须为文字')
    updated = {'description': value.strip(), 'background_signature': background_signature(manifest)}
    if updated == scene.get('scene_layout') or (not updated['description'] and not scene.get('scene_layout')):
        return False
    scene['scene_layout'] = updated
    scene['layout_revision'] = uuid.uuid4().hex
    return True


def prompt_with_constraints(text, constraints):
    import re
    text = re.sub(r'(?ms)^【场景空间约束】[^\n]*\n.*?(?=^【[^】\n]+】|\Z)', '', text).rstrip()
    return text + ('\n\n【场景空间约束】\n' + constraints if constraints else '')
