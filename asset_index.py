import json
import re
from pathlib import Path


BASE_DIR = Path('.')
ASSETS_DIR = Path('assets')
ASSET_INDEX_PATH = ASSETS_DIR / 'asset_index.json'
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}
META_FILE_NAME = 'asset_meta.json'


def _default_log(msg, level='INFO'):
    print(f'[{level}] {msg}')


log = _default_log


def configure_asset_index(base_dir, assets_dir, asset_index_path, log_fn=None):
    global BASE_DIR, ASSETS_DIR, ASSET_INDEX_PATH, log
    BASE_DIR = Path(base_dir)
    ASSETS_DIR = Path(assets_dir)
    ASSET_INDEX_PATH = Path(asset_index_path)
    log = log_fn or _default_log


def _rel(path):
    try:
        return str(Path(path).relative_to(BASE_DIR)).replace('\\', '/')
    except Exception:
        return str(path).replace('\\', '/')


def _empty_asset_index():
    return {
        'schema': 'asset_index.v2.identity_role',
        'asset_root': str(ASSETS_DIR),
        'semantic_design': {
            'identity_level': 'characters.identities 表示具体人物身份，例如 person_a / person_b；同一人物保持同一张脸和同一组参考图。',
            'role_level': 'characters.roles 表示职业或剧情角色类型，例如 protagonist / manager / assistant；role 用于剧情匹配，再映射到 default_identity。',
            'image_level': '图片层只保存姿态、视角、动作状态语义，默认继承所属 identity。',
            'background_level': '背景层保存场景语义、地点别名和适用剧情。',
        },
        'characters': {
            'identities': [],
            'roles': [],
        },
        'backgrounds': [],
        'selection_rules': [
            '先按具体人物 aliases / name_cn 匹配到 identity。',
            '如果没有具体人物匹配，再按 role / aliases / description_cn 匹配角色类型。',
            '角色类型命中后，跳转到 default_identity 或 identity_candidates 中的具体人物素材。',
            '具体人物确定后，再在该人物 images 中按 pose / tags_cn / use_for 选择姿态图。',
            '背景按 id / aliases / tags_cn / description_cn / use_for 匹配。',
            'LiconMSR Direct: 选择 1-4 张人物/物品参考图，并选择 1 张必需背景图。',
        ],
    }


def infer_asset_pose(name):
    low = str(name or '').lower()
    if 'in_car' in low:
        return 'inside vehicle'
    if 'out_car' in low:
        return 'outside vehicle'
    if 'fullbody' in low:
        return 'full body'
    if 'left90' in low:
        return 'left profile'
    if 'right90' in low:
        return 'right profile'
    if 'left45' in low:
        return 'left three-quarter'
    if 'right45' in low:
        return 'right three-quarter'
    if 'sit' in low:
        return 'sitting'
    if 'front' in low:
        return 'front'
    if 'back' in low:
        return 'back'
    return low.replace('_', ' ')


def infer_asset_tags(name):
    words = [x for x in re.split(r'[_\-\s]+', str(name or '').lower()) if x]
    tags = set(words)
    synonyms = {
        'sit': ['sitting', 'seated', 'chair'],
        'in': ['inside'],
        'car': ['vehicle'],
        'cabin': ['inside', 'vehicle'],
        'office': ['indoor', 'desk'],
        'warehouse': ['storage', 'cargo'],
        'road': ['driving', 'street'],
        'highway': ['driving', 'road'],
        'night': ['dark'],
        'day': ['daylight'],
    }
    for word in list(tags):
        tags.update(synonyms.get(word, []))
    return sorted(tags)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    return [str(value)]


def _load_meta_file(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log(f'Failed to read asset metadata {path}: {e}', 'WARN')
        return {}


def _manual_fields(item):
    if not isinstance(item, dict):
        return {}
    protected = {'id', 'path', 'relative_path', 'images', 'image_count'}
    return {k: v for k, v in item.items() if k not in protected and v not in (None, '', [], {})}


def _overlay_manual_fields(target, source):
    if not isinstance(target, dict) or not isinstance(source, dict):
        return target
    for key, value in source.items():
        if key in {'id', 'path', 'relative_path', 'images', 'image_count'}:
            continue
        if value not in (None, '', [], {}):
            target[key] = value
    return target


def _pose_layer_meta(asset_id, pose):
    text = f'{asset_id or ""} {pose or ""}'.lower()
    if 'in_car' in text or 'inside vehicle' in text:
        return ['车内', '驾驶室', '驾驶状态'], '人物位于车内或驾驶室内。', ['开车', '接电话', '车内对话', '驾驶途中']
    if 'out_car' in text or 'outside vehicle' in text:
        return ['车外', '站在车旁', '户外'], '人物在车外或车辆旁边。', ['下车', '站在车旁', '户外对话', '车外争执']
    if 'sit' in text or 'sitting' in text:
        return ['坐着', '坐姿', '桌前', '办公'], '人物坐姿。', ['办公室谈话', '训话', '桌前交流', '稳定对话']
    if 'front' in text:
        return ['正面', '正脸', '面对镜头'], '人物正面视角。', ['正面说话', '身份参考', '人物介绍', '表情参考']
    if 'back' in text:
        return ['背面', '背影', '从后方看'], '人物背面视角。', ['背影镜头', '人物离开', '从后方跟拍', '走远']
    if 'left45' in text or 'left three' in text:
        return ['左45度', '左前侧', '半侧面'], '人物左前侧三分之二视角。', ['左侧对话', '斜侧构图', '半侧脸说话']
    if 'right45' in text or 'right three' in text:
        return ['右45度', '右前侧', '半侧面'], '人物右前侧三分之二视角。', ['右侧对话', '斜侧构图', '半侧脸说话']
    if 'left90' in text or 'left profile' in text:
        return ['左侧脸', '左侧面', '侧面'], '人物左侧90度侧脸。', ['左侧脸', '侧面说话', '横向构图']
    if 'right90' in text or 'right profile' in text:
        return ['右侧脸', '右侧面', '侧面'], '人物右侧90度侧脸。', ['右侧脸', '侧面说话', '横向构图']
    if 'fullbody' in text or 'full body' in text:
        return ['全身', '站立', '身体姿态'], '人物全身视角。', ['站立', '走路', '全身动作', '人物出场', '身体动作参考']
    return [], '人物参考图。', []


def _identity_meta(identity_id):
    identity_id = str(identity_id or '')
    display = identity_id.replace('_', ' ').strip() or 'character'
    return display, [identity_id], f'{display} character identity.', []


def _role_layer_meta(role_id):
    role_id = str(role_id or '')
    aliases = [role_id] if role_id else []
    return aliases, f'{role_id} role.'


def _background_layer_meta(bg_id, tags):
    bid = str(bg_id or '')
    aliases = [bid] if bid else []
    tags_cn = [str(x) for x in (tags or []) if str(x)]
    description = f'{bid} scene.'
    use_for = []
    return sorted(set(aliases)), sorted(set(tags_cn)), description, sorted(set(use_for))


def scan_assets_raw():
    identity_root = ASSETS_DIR / 'characters' / 'identities'
    role_root = ASSETS_DIR / 'characters' / 'roles'
    bg_dir = ASSETS_DIR / 'backgrounds'
    background_meta = _load_meta_file(bg_dir / META_FILE_NAME)
    identities = []
    roles = []
    backgrounds = []
    total_images = 0

    if identity_root.exists():
        for person_dir in sorted([p for p in identity_root.iterdir() if p.is_dir()]):
            person_meta = _load_meta_file(person_dir / META_FILE_NAME)
            identity_meta = person_meta.get('identity') if isinstance(person_meta.get('identity'), dict) else {}
            image_meta = person_meta.get('images') if isinstance(person_meta.get('images'), dict) else {}
            images = []
            for p in sorted([x for x in person_dir.iterdir() if x.is_file() and x.suffix.lower() in IMAGE_EXTS]):
                img_data = {
                    'id': p.stem,
                    'path': str(p),
                    'relative_path': _rel(p),
                    'pose': infer_asset_pose(p.stem),
                    'tags': infer_asset_tags(p.stem),
                }
                meta = image_meta.get(p.stem)
                if isinstance(meta, dict):
                    img_data['_manual_meta'] = _manual_fields(meta)
                images.append(img_data)
            name_cn, aliases, desc, use_for = _identity_meta(person_dir.name)
            identity_data = {
                'id': person_dir.name,
                'name_cn': name_cn,
                'name': person_dir.name,
                'aliases': aliases,
                'path': str(person_dir),
                'relative_path': _rel(person_dir),
                'description_cn': desc,
                'use_for': use_for,
                'images': images,
                'image_count': len(images),
            }
            if isinstance(identity_meta, dict):
                identity_data['_manual_meta'] = _manual_fields(identity_meta)
            identities.append(identity_data)
            total_images += len(images)

    if role_root.exists():
        for role_dir in sorted([p for p in role_root.iterdir() if p.is_dir()]):
            role_json = role_dir / 'role.json'
            role_data = {}
            if role_json.exists():
                try:
                    role_data = json.loads(role_json.read_text(encoding='utf-8'))
                except Exception as e:
                    log(f'Failed to read {role_json}: {e}', 'WARN')
            role_id = str(role_data.get('role_id') or role_data.get('id') or role_dir.name)
            default_identity = str(role_data.get('default_identity') or '')
            identity_candidates = _as_list(role_data.get('identity_candidates')) or ([default_identity] if default_identity else [])
            aliases, desc = _role_layer_meta(role_id)
            roles.append({
                'id': role_id,
                'name': role_id,
                'default_identity': default_identity,
                'identity_candidates': identity_candidates,
                'aliases': _as_list(role_data.get('aliases')) or aliases,
                'path': str(role_dir),
                'relative_path': _rel(role_dir),
                'description_cn': str(role_data.get('description_cn') or f'{desc} 默认映射到具体人物 {default_identity}。'),
                'selection_note_cn': '剧情先匹配 role/aliases，再跳转到 default_identity 或 identity_candidates 中的具体人物素材。',
            })

    if bg_dir.exists():
        for p in sorted([x for x in bg_dir.iterdir() if x.is_file() and x.suffix.lower() in IMAGE_EXTS]):
            bg_data = {
                'id': p.stem,
                'path': str(p),
                'relative_path': _rel(p),
                'tags': infer_asset_tags(p.stem),
            }
            meta = background_meta.get(p.stem)
            if isinstance(meta, dict):
                bg_data['_manual_meta'] = _manual_fields(meta)
            backgrounds.append(bg_data)
            total_images += 1

    data = {'characters': {'identities': identities, 'roles': roles}, 'backgrounds': backgrounds}
    log(f'Raw assets scanned: identities={len(identities)} roles={len(roles)} backgrounds={len(backgrounds)} images={total_images}')
    return data


def scan_assets():
    return scan_assets_raw()


def build_layered_asset_index_prompt(raw_assets, style_reference=None):
    system_prompt = '''
你是素材库语义索引生成助手。
只输出严格 JSON，不要解释，不要 Markdown，不要代码块。

你要把原始素材清单转换成两层角色语义 asset_index.json。

核心原则：
1. characters.identities 是具体人物身份层，只保存具体人物身份、别名、人物描述和该人物的图片。
2. characters.roles 是角色类型层，只保存职业/剧情身份语义，并通过 default_identity / identity_candidates 指向具体人物。
3. 图片层只保存姿态、视角、动作状态语义，不要重复大量人物身份关键词。
4. 不允许修改任何 path、relative_path。
5. 不允许删除素材。
6. 不允许创造不存在的 identity、role、图片或背景。
7. 输出必须是完整严格 JSON。

输出格式必须是：
{
  "schema": "asset_index.v2.identity_role",
  "asset_root": "",
  "semantic_design": {},
  "characters": {"identities": [], "roles": []},
  "backgrounds": [],
  "selection_rules": []
}
'''
    user_prompt = json.dumps({
        'asset_root': str(ASSETS_DIR),
        'raw_assets': raw_assets,
        'style_reference': style_reference or {},
        'style_rules': [
            '新增素材的 aliases、tags_cn、description_cn、use_for 要尽量模仿 style_reference 中现有素材的颗粒度和中文表达风格。',
            '背景 aliases 使用可出现在 guide.background 里的自然中文场景名，不要只保留英文 id。',
            '背景 tags_cn 给 3-6 个中文标签，覆盖地点、时间、室内外、用途等。',
            'description_cn 使用简短中文场景描述，格式接近“白天调度办公室场景。”。',
            'use_for 给 3-5 个中文用途短语，用于 guide 匹配，不要空数组，除非完全无法判断。',
            '不要修改 path、relative_path、id，不要创造不存在的素材。',
        ],
    }, ensure_ascii=False)
    return system_prompt, user_prompt


def build_layered_asset_index_locally(raw_assets):
    index = _empty_asset_index()
    index['asset_root'] = str(ASSETS_DIR)
    for identity in raw_assets.get('characters', {}).get('identities', []) or []:
        new_identity = dict(identity)
        manual_identity_meta = new_identity.pop('_manual_meta', {})
        new_images = []
        for img in identity.get('images', []) or []:
            manual_image_meta = img.get('_manual_meta', {}) if isinstance(img, dict) else {}
            tags_cn, img_desc, img_use_for = _pose_layer_meta(img.get('id', ''), img.get('pose', ''))
            new_image = {
                'id': img.get('id', ''),
                'path': img.get('path', ''),
                'relative_path': img.get('relative_path', ''),
                'pose': img.get('pose', ''),
                'tags': _as_list(img.get('tags')),
                'tags_cn': tags_cn,
                'description_cn': img_desc,
                'use_for': img_use_for,
            }
            _overlay_manual_fields(new_image, manual_image_meta)
            new_images.append(new_image)
        new_identity['images'] = new_images
        new_identity['image_count'] = len(new_images)
        _overlay_manual_fields(new_identity, manual_identity_meta)
        index['characters']['identities'].append(new_identity)
    index['characters']['roles'] = list(raw_assets.get('characters', {}).get('roles', []) or [])
    for bg in raw_assets.get('backgrounds', []) or []:
        manual_bg_meta = bg.get('_manual_meta', {}) if isinstance(bg, dict) else {}
        aliases, tags_cn, desc, use_for = _background_layer_meta(bg.get('id', ''), bg.get('tags', []))
        new_bg = {
            'id': bg.get('id', ''),
            'path': bg.get('path', ''),
            'relative_path': bg.get('relative_path', ''),
            'tags': _as_list(bg.get('tags')),
            'aliases': aliases,
            'tags_cn': tags_cn,
            'description_cn': desc,
            'use_for': use_for,
        }
        _overlay_manual_fields(new_bg, manual_bg_meta)
        index['backgrounds'].append(new_bg)
    return index


def _apply_manual_metadata(index, raw_assets):
    if not isinstance(index, dict):
        return index
    chars = index.get('characters', {}) if isinstance(index.get('characters'), dict) else {}
    index_identities = _by_id(chars.get('identities', []))
    for identity in raw_assets.get('characters', {}).get('identities', []) or []:
        target = index_identities.get(str(identity.get('id')))
        if not target:
            continue
        _overlay_manual_fields(target, identity.get('_manual_meta', {}))
        target_images = _by_id(target.get('images', []))
        for image in identity.get('images', []) or []:
            target_image = target_images.get(str(image.get('id')))
            if target_image:
                _overlay_manual_fields(target_image, image.get('_manual_meta', {}))
    index_bgs = _by_id(index.get('backgrounds', []))
    for bg in raw_assets.get('backgrounds', []) or []:
        target = index_bgs.get(str(bg.get('id')))
        if target:
            _overlay_manual_fields(target, bg.get('_manual_meta', {}))
    return index


def _validate_and_repair_layered_index(index, raw_assets):
    repaired = build_layered_asset_index_locally(raw_assets)
    if not isinstance(index, dict):
        return repaired
    chars = index.get('characters') if isinstance(index.get('characters'), dict) else {}
    llm_identities = {x.get('id'): x for x in chars.get('identities', []) or [] if isinstance(x, dict)}
    llm_roles = {x.get('id'): x for x in chars.get('roles', []) or [] if isinstance(x, dict)}
    llm_bgs = {x.get('id'): x for x in index.get('backgrounds', []) or [] if isinstance(x, dict)}

    for identity in repaired['characters']['identities']:
        llm = llm_identities.get(identity.get('id'), {})
        for key in ('name_cn', 'aliases', 'description_cn', 'use_for'):
            if llm.get(key):
                identity[key] = llm[key]
        llm_images = {x.get('id'): x for x in llm.get('images', []) or [] if isinstance(x, dict)}
        for img in identity.get('images', []) or []:
            li = llm_images.get(img.get('id'), {})
            for key in ('tags_cn', 'description_cn', 'use_for'):
                if li.get(key):
                    img[key] = li[key]

    for role in repaired['characters']['roles']:
        llm = llm_roles.get(role.get('id'), {})
        for key in ('aliases', 'description_cn', 'selection_note_cn'):
            if llm.get(key):
                role[key] = llm[key]
        # paths and identity mapping stay authoritative from roles/role.json

    for bg in repaired['backgrounds']:
        llm = llm_bgs.get(bg.get('id'), {})
        for key in ('aliases', 'tags_cn', 'description_cn', 'use_for'):
            if llm.get(key):
                bg[key] = llm[key]

    repaired['schema'] = 'asset_index.v2.identity_role'
    repaired['semantic_design'] = index.get('semantic_design') or repaired['semantic_design']
    repaired['selection_rules'] = index.get('selection_rules') or repaired['selection_rules']
    return repaired


def _load_existing_asset_index():
    if not ASSET_INDEX_PATH.exists():
        return None
    try:
        with open(ASSET_INDEX_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data.get('characters'), dict):
            return data
    except Exception as e:
        log(f'Failed to load existing asset_index.json for incremental refresh: {e}', 'WARN')
    return None


def _by_id(items):
    return {str(x.get('id')): x for x in (items or []) if isinstance(x, dict) and x.get('id')}


def _compact_style_item(item, keys):
    return {key: item.get(key) for key in keys if item.get(key) not in (None, '', [], {})}


def _build_style_reference(existing, limit=5):
    if not existing:
        return {}
    chars = existing.get('characters', {}) if isinstance(existing.get('characters'), dict) else {}
    identities = []
    for identity in chars.get('identities', []) or []:
        item = _compact_style_item(identity, ('id', 'name_cn', 'aliases', 'description_cn', 'use_for'))
        images = []
        for image in identity.get('images', []) or []:
            images.append(_compact_style_item(image, ('id', 'pose', 'tags_cn', 'description_cn', 'use_for')))
            if len(images) >= 2:
                break
        if images:
            item['images'] = images
        if item:
            identities.append(item)
        if len(identities) >= limit:
            break

    roles = []
    for role in chars.get('roles', []) or []:
        item = _compact_style_item(role, ('id', 'aliases', 'description_cn', 'selection_note_cn', 'use_for'))
        if item:
            roles.append(item)
        if len(roles) >= limit:
            break

    backgrounds = []
    for bg in existing.get('backgrounds', []) or []:
        item = _compact_style_item(bg, ('id', 'aliases', 'tags_cn', 'description_cn', 'use_for'))
        if item:
            backgrounds.append(item)
        if len(backgrounds) >= limit:
            break

    return {
        'characters': {'identities': identities, 'roles': roles},
        'backgrounds': backgrounds,
    }


def _copy_metadata(target, source, protected_keys, only_truthy=False):
    if not isinstance(target, dict) or not isinstance(source, dict):
        return target
    for key, value in source.items():
        if key in protected_keys:
            continue
        if only_truthy and value in (None, '', [], {}):
            continue
        target[key] = value
    return target


def _merge_existing_metadata(index, existing):
    """Keep scanned paths authoritative, but preserve manually tuned semantic fields."""
    if not existing:
        return index
    index = index or _empty_asset_index()
    existing_chars = existing.get('characters', {}) if isinstance(existing.get('characters'), dict) else {}
    index_chars = index.get('characters', {}) if isinstance(index.get('characters'), dict) else {}

    old_identities = _by_id(existing_chars.get('identities', []))
    for identity in index_chars.get('identities', []) or []:
        old_identity = old_identities.get(str(identity.get('id')))
        if not old_identity:
            continue
        _copy_metadata(identity, old_identity, {'id', 'path', 'relative_path', 'images', 'image_count'})
        old_images = _by_id(old_identity.get('images', []))
        for image in identity.get('images', []) or []:
            old_image = old_images.get(str(image.get('id')))
            if old_image:
                _copy_metadata(image, old_image, {'id', 'path', 'relative_path'})
        identity['image_count'] = len(identity.get('images', []) or [])

    old_roles = _by_id(existing_chars.get('roles', []))
    for role in index_chars.get('roles', []) or []:
        old_role = old_roles.get(str(role.get('id')))
        if old_role:
            _copy_metadata(role, old_role, {
                'id', 'path', 'relative_path',
                'default_identity', 'identity_candidates',
            })

    old_backgrounds = _by_id(existing.get('backgrounds', []))
    for bg in index.get('backgrounds', []) or []:
        old_bg = old_backgrounds.get(str(bg.get('id')))
        if old_bg:
            _copy_metadata(bg, old_bg, {'id', 'path', 'relative_path'})

    for key in ('schema', 'asset_root'):
        index[key] = index.get(key) or existing.get(key)
    for key in ('semantic_design', 'selection_rules'):
        if existing.get(key):
            index[key] = existing[key]
    return index


def _build_new_raw_assets(raw_assets, existing):
    existing_chars = existing.get('characters', {}) if isinstance(existing.get('characters'), dict) else {}
    old_identities = _by_id(existing_chars.get('identities', []))
    old_roles = _by_id(existing_chars.get('roles', []))
    old_backgrounds = _by_id(existing.get('backgrounds', []))

    new_identities = []
    for identity in raw_assets.get('characters', {}).get('identities', []) or []:
        old_identity = old_identities.get(str(identity.get('id')))
        if not old_identity:
            new_identities.append(identity)
            continue
        old_images = _by_id(old_identity.get('images', []))
        new_images = [img for img in identity.get('images', []) or [] if str(img.get('id')) not in old_images]
        if new_images:
            item = dict(identity)
            item['images'] = new_images
            item['image_count'] = len(new_images)
            new_identities.append(item)

    new_roles = [
        role for role in raw_assets.get('characters', {}).get('roles', []) or []
        if str(role.get('id')) not in old_roles
    ]
    new_backgrounds = [
        bg for bg in raw_assets.get('backgrounds', []) or []
        if str(bg.get('id')) not in old_backgrounds
    ]
    return {'characters': {'identities': new_identities, 'roles': new_roles}, 'backgrounds': new_backgrounds}


def _raw_asset_count(raw_assets):
    identities = raw_assets.get('characters', {}).get('identities', []) or []
    roles = raw_assets.get('characters', {}).get('roles', []) or []
    backgrounds = raw_assets.get('backgrounds', []) or []
    images = sum(len(identity.get('images', []) or []) for identity in identities)
    return len(identities), len(roles), len(backgrounds), images


def _format_log_value(value, max_len=180):
    if value in (None, '', [], {}):
        return ''
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    if len(text) > max_len:
        return text[:max_len - 3] + '...'
    return text


def _log_new_assets_for_deepseek(raw_assets):
    identity_count, role_count, bg_count, image_count = _raw_asset_count(raw_assets)
    if not (identity_count or role_count or bg_count or image_count):
        return
    log('新增素材将发送给 DeepSeek:')
    for identity in raw_assets.get('characters', {}).get('identities', []) or []:
        images = identity.get('images', []) or []
        if len(images) == int(identity.get('image_count') or len(images)):
            log(f"  identity: {identity.get('id', '')} images={len(images)}")
        else:
            log(f"  identity新增图片: {identity.get('id', '')} images={len(images)}")
        for image in images:
            log(f"    image: {identity.get('id', '')}/{image.get('id', '')} path={image.get('relative_path', image.get('path', ''))}")
    for role in raw_assets.get('characters', {}).get('roles', []) or []:
        log(
            f"  role: {role.get('id', '')} "
            f"default_identity={role.get('default_identity', '')} "
            f"identity_candidates={_format_log_value(role.get('identity_candidates', []))}"
        )
    for bg in raw_assets.get('backgrounds', []) or []:
        log(f"  background: {bg.get('id', '')} path={bg.get('relative_path', bg.get('path', ''))}")


def _log_metadata_fields(prefix, item, keys):
    parts = []
    for key in keys:
        value = _format_log_value((item or {}).get(key))
        if value:
            parts.append(f'{key}={value}')
    if parts:
        log(f"  {prefix}: " + ' | '.join(parts))
        return True
    return False


def _log_deepseek_generated_metadata(index):
    log('DeepSeek 为新增素材补充的内容:')
    wrote_any = False
    chars = index.get('characters', {}) if isinstance(index.get('characters'), dict) else {}
    for identity in chars.get('identities', []) or []:
        wrote_any = _log_metadata_fields(
            f"identity {identity.get('id', '')}",
            identity,
            ('name_cn', 'aliases', 'description_cn', 'use_for', 'avoid_for', 'priority'),
        ) or wrote_any
        for image in identity.get('images', []) or []:
            wrote_any = _log_metadata_fields(
                f"image {identity.get('id', '')}/{image.get('id', '')}",
                image,
                ('pose', 'tags_cn', 'description_cn', 'use_for', 'avoid_for', 'priority'),
            ) or wrote_any
    for role in chars.get('roles', []) or []:
        wrote_any = _log_metadata_fields(
            f"role {role.get('id', '')}",
            role,
            ('aliases', 'description_cn', 'selection_note_cn', 'use_for', 'avoid_for', 'priority'),
        ) or wrote_any
    for bg in index.get('backgrounds', []) or []:
        wrote_any = _log_metadata_fields(
            f"background {bg.get('id', '')}",
            bg,
            ('aliases', 'tags_cn', 'description_cn', 'use_for', 'avoid_for', 'priority'),
        ) or wrote_any
    if not wrote_any:
        log('  DeepSeek 没有返回可记录的新增语义字段。', 'WARN')


def _overlay_generated_metadata(index, generated):
    generated_chars = generated.get('characters', {}) if isinstance(generated.get('characters'), dict) else {}
    index_chars = index.get('characters', {}) if isinstance(index.get('characters'), dict) else {}

    new_identities = _by_id(generated_chars.get('identities', []))
    for identity in index_chars.get('identities', []) or []:
        source_identity = new_identities.get(str(identity.get('id')))
        if not source_identity:
            continue
        _copy_metadata(identity, source_identity, {'id', 'path', 'relative_path', 'images', 'image_count'}, only_truthy=True)
        source_images = _by_id(source_identity.get('images', []))
        for image in identity.get('images', []) or []:
            source_image = source_images.get(str(image.get('id')))
            if source_image:
                _copy_metadata(image, source_image, {'id', 'path', 'relative_path'}, only_truthy=True)

    new_roles = _by_id(generated_chars.get('roles', []))
    for role in index_chars.get('roles', []) or []:
        source_role = new_roles.get(str(role.get('id')))
        if source_role:
            _copy_metadata(role, source_role, {
                'id', 'path', 'relative_path',
                'default_identity', 'identity_candidates',
            }, only_truthy=True)

    new_backgrounds = _by_id(generated.get('backgrounds', []))
    for bg in index.get('backgrounds', []) or []:
        source_bg = new_backgrounds.get(str(bg.get('id')))
        if source_bg:
            _copy_metadata(bg, source_bg, {'id', 'path', 'relative_path'}, only_truthy=True)
    return index


def save_asset_index(index):
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ASSET_INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(index or _empty_asset_index(), f, ensure_ascii=False, indent=2)
    log(f'Saved two-layer asset_index.json: {ASSET_INDEX_PATH}')
    return ASSET_INDEX_PATH


def build_asset_index_with_llm(call_llm_fn):
    log('Scanning raw assets...')
    raw_assets = scan_assets_raw()
    identities = raw_assets.get('characters', {}).get('identities', []) or []
    roles = raw_assets.get('characters', {}).get('roles', []) or []
    bgs = raw_assets.get('backgrounds', []) or []
    image_count = sum(len(c.get('images', []) or []) for c in identities) + len(bgs)
    log(f'Raw assets: identities={len(identities)} roles={len(roles)} backgrounds={len(bgs)} images={image_count}')

    existing = _load_existing_asset_index()
    if not existing:
        system_prompt, user_prompt = build_layered_asset_index_prompt(raw_assets)
        try:
            log('asset_index.json 不存在或不是新结构，本次会把全部素材发送给 DeepSeek。')
            _log_new_assets_for_deepseek(raw_assets)
            log('Calling DeepSeek to build two-layer asset index...')
            index, _ = call_llm_fn(system_prompt, user_prompt, max_tokens=12000, label='????asset_index')
            index = _validate_and_repair_layered_index(index, raw_assets)
            _log_deepseek_generated_metadata(index)
        except Exception as e:
            log(f'DeepSeek asset_index generation failed, using local two-layer fallback: {e}', 'WARN')
            index = build_layered_asset_index_locally(raw_assets)
        index = _apply_manual_metadata(index, raw_assets)
        save_asset_index(index)
        return index

    index = build_layered_asset_index_locally(raw_assets)
    new_raw_assets = _build_new_raw_assets(raw_assets, existing)
    new_identity_count, new_role_count, new_bg_count, new_image_count = _raw_asset_count(new_raw_assets)
    log(
        'Incremental asset refresh: '
        f'new_identities={new_identity_count} new_roles={new_role_count} '
        f'new_backgrounds={new_bg_count} new_images={new_image_count}'
    )

    if new_identity_count or new_role_count or new_bg_count or new_image_count:
        _log_new_assets_for_deepseek(new_raw_assets)
        style_reference = _build_style_reference(existing)
        log(
            'DeepSeek style reference: '
            f"backgrounds={len(style_reference.get('backgrounds', []))}, "
            f"roles={len(style_reference.get('characters', {}).get('roles', []))}, "
            f"identities={len(style_reference.get('characters', {}).get('identities', []))}"
        )
        system_prompt, user_prompt = build_layered_asset_index_prompt(new_raw_assets, style_reference=style_reference)
        try:
            log('Calling DeepSeek only for new assets...')
            generated, _ = call_llm_fn(system_prompt, user_prompt, max_tokens=6000, label='????asset_index')
            generated = _validate_and_repair_layered_index(generated, new_raw_assets)
            _log_deepseek_generated_metadata(generated)
            index = _overlay_generated_metadata(index, generated)
        except Exception as e:
            log(f'DeepSeek incremental asset_index generation failed; using local metadata for new assets: {e}', 'WARN')
    else:
        log('No new assets found; preserving existing semantic metadata.')

    index = _merge_existing_metadata(index, existing)
    index = _apply_manual_metadata(index, raw_assets)
    save_asset_index(index)
    return index


def build_asset_index(*args, **kwargs):
    if ASSET_INDEX_PATH.exists():
        log('build_asset_index is deprecated for normal flow; loading existing asset_index.json instead.', 'WARN')
        return load_asset_index()
    log('asset_index.json not found; creating local two-layer fallback without DeepSeek.', 'WARN')
    index = build_layered_asset_index_locally(scan_assets_raw())
    save_asset_index(index)
    return index


def load_asset_index():
    if not ASSET_INDEX_PATH.exists():
        log(f'asset_index.json not found: {ASSET_INDEX_PATH}. Click refresh assets to generate it.', 'WARN')
        return _empty_asset_index()
    with open(ASSET_INDEX_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data.get('characters'), dict):
        raise ValueError('asset_index.json 必须是新两层结构：characters.identities / characters.roles')
    return data


def sync_asset_index_with_assets():
    raw_assets = scan_assets_raw()
    existing = _load_existing_asset_index()
    if existing:
        index = _validate_and_repair_layered_index(existing, raw_assets)
        index = _merge_existing_metadata(index, existing)
    else:
        index = build_layered_asset_index_locally(raw_assets)
    index = _apply_manual_metadata(index, raw_assets)
    save_asset_index(index)
    return index


def get_asset_ids():
    data = load_asset_index()
    chars = data.get('characters', {})
    return {
        'characters': [x.get('id', '') for x in chars.get('roles', []) if x.get('id')],
        'identities': [x.get('id', '') for x in chars.get('identities', []) if x.get('id')],
        'backgrounds': [x.get('id', '') for x in data.get('backgrounds', []) if x.get('id')],
    }


def compact_asset_index(index):
    chars = index.get('characters', {}) if isinstance(index.get('characters'), dict) else {}
    return {
        'characters': {
            'identities': [
                {
                    'id': c.get('id', ''),
                    'name_cn': c.get('name_cn', ''),
                    'name': c.get('name', ''),
                    'aliases': c.get('aliases', []),
                    'description_cn': c.get('description_cn', ''),
                    'use_for': c.get('use_for', []),
                    'images': [
                        {
                            'id': img.get('id', ''),
                            'path': img.get('path', ''),
                            'pose': img.get('pose', ''),
                            'tags': img.get('tags', []),
                            'tags_cn': img.get('tags_cn', []),
                            'description_cn': img.get('description_cn', ''),
                            'use_for': img.get('use_for', []),
                        }
                        for img in c.get('images', []) or []
                    ],
                }
                for c in chars.get('identities', []) or []
            ],
            'roles': [
                {
                    'id': r.get('id', ''),
                    'name': r.get('name', ''),
                    'default_identity': r.get('default_identity', ''),
                    'identity_candidates': r.get('identity_candidates', []),
                    'aliases': r.get('aliases', []),
                    'description_cn': r.get('description_cn', ''),
                }
                for r in chars.get('roles', []) or []
            ],
        },
        'backgrounds': [
            {
                'id': bg.get('id', ''),
                'path': bg.get('path', ''),
                'tags': bg.get('tags', []),
                'tags_cn': bg.get('tags_cn', []),
                'aliases': bg.get('aliases', []),
                'description_cn': bg.get('description_cn', ''),
                'use_for': bg.get('use_for', []),
            }
            for bg in index.get('backgrounds', []) or []
        ],
    }
