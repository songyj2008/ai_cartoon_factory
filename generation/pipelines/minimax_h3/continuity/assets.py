"""Reference mapping; no directing or scene-state inference."""
import copy
from ..contracts import H3GenerationConfig
from ..prompt_reconciler import H3PromptReconciler

def _source_slot(asset,current):
    """Inherit media while preserving the target screenplay's stable slot identity."""
    item=copy.deepcopy(asset)
    item['asset_id']=current.get('asset_id') or current.get('id')
    for key in ('id','order','role','label'):
        if key in current: item[key]=copy.deepcopy(current[key])
    item.setdefault('metadata',{}).pop('shot_indices',None)
    meta=current.get('metadata') or {}
    for key in ('shot_indices','role_id','voice_disabled'):
        if key in meta: item['metadata'][key]=copy.deepcopy(meta[key])
    return item


def _same_person(a,b):
    left=a.get('metadata') or {};right=b.get('metadata') or {}
    # Explicit story roles take precedence over reusable library identities.
    if left.get('role_id') and right.get('role_id'):
        return left['role_id']==right['role_id']
    return bool(left.get('identity_id') and left['identity_id']==right.get('identity_id'))


def _prepare_scene_references(base,source):
    """Make source backgrounds available without importing its cast or audio."""
    backgrounds=[a for a in H3GenerationConfig.from_mapping(source.get('snapshot')).to_dict()['asset_manifest']
                 if a.get('type')=='image' and (a.get('metadata') or {}).get('asset_kind')=='background']
    prepared=copy.deepcopy(base)
    manifest=prepared['asset_manifest']
    changed=[]
    for background in backgrounds:
        key=H3PromptReconciler._asset_path_key(background.get('path'))
        index=next((i for i,a in enumerate(manifest) if a.get('type')=='image' and
                    ((a.get('asset_id') or a.get('id'))==background['asset_id'] or
                     (key and H3PromptReconciler._asset_path_key(a.get('path'))==key))),None)
        current_backgrounds=[i for i,a in enumerate(manifest) if a.get('type')=='image' and
                             (a.get('metadata') or {}).get('asset_kind')=='background']
        if index is None and current_backgrounds:
            if len(backgrounds)!=1 or len(current_backgrounds)!=1:
                raise ValueError('来源或当前稿有多个场景图，无法唯一确定承接场景，请先明确场景参考')
            index=current_backgrounds[0]
        item=copy.deepcopy(background)
        item.setdefault('metadata',{}).pop('shot_indices',None)
        if index is not None:
            current=manifest[index]
            current_meta=current.get('metadata') or {}
            if current_meta.get('asset_kind')=='background' and 'shot_indices' in current_meta:
                item['metadata']['shot_indices']=copy.deepcopy(current_meta['shot_indices'])
            # A slot can already point to the right file but still carry a
            # former character's name and identity after image replacement.
            item['asset_id']=current.get('asset_id') or current.get('id') or background['asset_id']
            if 'id' in current:
                item['id']=current['id']
            if 'order' in current:
                item['order']=current['order']
            else:
                item.pop('order',None)
            if any(current.get(k)!=item.get(k) for k in ('path','role','label','metadata')):
                manifest[index]=item
                changed.append(item)
            continue
        if any((a.get('asset_id') or a.get('id'))==background['asset_id'] for a in manifest):
            raise ValueError('场景参考素材身份冲突，请重新选择素材')
        item['order']=max((a.order or 0 for a in H3GenerationConfig.from_mapping(prepared).asset_manifest),default=0)+1
        manifest.append(item)
        changed.append(item)
    if changed:
        prepared['prompt']=H3PromptReconciler().reconcile(base['prompt'],
            H3GenerationConfig.from_mapping(base),H3GenerationConfig.from_mapping(prepared))
    return prepared,backgrounds,changed


def _prepare_character_references(base,source):
    """Align existing roles and offer missing identities for continued presence."""
    prepared=copy.deepcopy(base)
    added=[]
    changed=False
    for asset in H3GenerationConfig.from_mapping(source.get('snapshot')).to_dict()['asset_manifest']:
        meta=asset.get('metadata') or {}
        if asset.get('type')!='image' or meta.get('asset_kind')!='character':
            continue
        def same_identity(current):
            if current.get('type')!='image':
                return False
            other=current.get('metadata') or {}
            if meta.get('role_id') and other.get('role_id') and meta['role_id']!=other['role_id']:
                return False
            return (current.get('asset_id',current.get('id'))==asset['asset_id'] or
                    H3PromptReconciler._asset_path_key(current.get('path'))==H3PromptReconciler._asset_path_key(asset.get('path')) or
                    bool(meta.get('identity_id') and meta['identity_id']==other.get('identity_id')) or
                    bool(meta.get('role_id') and meta['role_id']==other.get('role_id')))
        matches=[i for i,current in enumerate(prepared['asset_manifest']) if
                 current.get('type')=='image' and
                 (current.get('metadata') or {}).get('asset_kind')=='character' and
                 _same_person(asset,current)]
        if len(matches)>1:
            raise ValueError('当前稿同一人物有多个参考，无法唯一承接')
        if matches:
            # Keep the current reference; history only supplies missing roles.
            continue
        if any(same_identity(current) for current in prepared['asset_manifest']):
            continue
        if any(a.get('asset_id',a.get('id'))==asset['asset_id'] for a in prepared['asset_manifest']):
            raise ValueError('人物参考素材身份冲突，请重新选择素材')
        item=copy.deepcopy(asset)
        item['metadata'].pop('shot_indices',None)
        # A silent listener needs their image, not an automatically selected voice.
        item['metadata'].pop('library_voice',None)
        item['order']=max((a.order or 0 for a in H3GenerationConfig.from_mapping(prepared).asset_manifest),default=0)+1
        prepared['asset_manifest'].append(item)
        added.append(item)
    if added or changed:
        prepared['prompt']=H3PromptReconciler().reconcile(base['prompt'],
            H3GenerationConfig.from_mapping(base),H3GenerationConfig.from_mapping(prepared))
    return prepared,added


def composition_references(base, text, shots):
    """Project actual visual use; preserve speech, authored dependencies and anchors.

    The scene roster is independent of the video's image inputs. Only ordinary
    character images without any remaining use can be omitted. No creative
    decision or inference about camera geometry happens here.
    """
    import re
    from ..asset_binder import H3AssetBinder
    from ..prompt_compiler import H3PromptCompiler
    from ..shot_editor import parse_prompt, dialogue_units
    from ..dialogue_text import transform_outside_dialogue

    bindings = H3AssetBinder().bind(base)
    compiler = H3PromptCompiler()
    doc = parse_prompt(text, base['segment_uid'], base['asset_manifest'])
    used_roles = {role for shot in shots for role in shot['visible_roles']}
    used_roles.update(unit['speaker'] for shot in doc['shots'] for unit in dialogue_units(shot))
    # Definitions mention every image. They are generated output, not evidence
    # of an authored dependency. Dialogue is opaque even if it contains tags.
    authored = compiler.strip_voice_sections(text)
    authored = H3PromptReconciler()._without_sections(authored, {'主体定义', '参考保留关系', '场景人物占位'})
    tags = set()
    fragments = []
    def dependencies(value):
        fragments.append(value)
        tags.update(tag.casefold() for tag in re.findall(r'<(?:Subject|Picture)\s+\d+>', value, re.I))
        return value
    transform_outside_dialogue(authored, dependencies)
    authored = '\n'.join(fragments)
    removable = set()
    for entry in compiler._subject_reference_entries(bindings):
        if entry['asset_kind'] != 'character':
            continue
        asset = bindings.binding_for(entry['asset_id']).asset
        meta = asset.metadata
        if (compiler._picture_anchor_role(meta)
                or any(meta.get(key) for key in ('picture_role', 'reference_role', 'usage', 'retention_note'))
                or str(meta.get('retention_marker') or meta.get('retention_relationship') or 'fully_preserved') != 'fully_preserved'):
            continue
        aliases = {str(value) for value in entry.get('aliases', [])}
        if aliases & used_roles or entry['subject_tag'].casefold() in tags or entry['picture_tag'].casefold() in tags:
            continue
        if any(re.search(r'(?<![A-Za-z0-9_])' + re.escape(alias) + r'(?![A-Za-z0-9_])', authored)
               for alias in aliases if alias):
            continue
        # Explicitly bound voices keep their identity owner, including voices
        # that are currently used only offscreen or outside a parsed Shot.
        if any(a.asset.metadata.get('voice_only') and _same_person(
                   {'metadata': asset.metadata}, {'metadata': a.asset.metadata})
               for a in bindings.audios):
            continue
        removable.add(entry['asset_id'])
    return [copy.deepcopy(a) for a in base['asset_manifest']
            if (a.get('asset_id') or a.get('id')) not in removable]


def _prepare_voice_references(base,source):
    """Use source voices only for identities speaking in the target screenplay."""
    from ..shot_editor import inspect_prompt,dialogue_units
    inspected=inspect_prompt(base['prompt'],'continuity',base['asset_manifest'])
    if not inspected['verified']:
        raise ValueError('无法可靠识别当前对白和说话人：'+'；'.join(inspected['issues']))
    speakers={u['speaker'] for shot in inspected['document']['shots'] for u in dialogue_units(shot)}
    prepared=copy.deepcopy(base)
    source_assets=H3GenerationConfig.from_mapping(source.get('snapshot')).to_dict()['asset_manifest']
    changes=[]
    for image in prepared['asset_manifest'][:]:
        meta=image.get('metadata') or {}
        if image.get('type')!='image' or meta.get('asset_kind')!='character': continue
        if meta.get('voice_disabled'): continue
        if not speakers.intersection({meta.get('role_id'),image.get('role'),image.get('label')}): continue
        voices=[a for a in source_assets if a.get('type')=='audio' and
                (a.get('metadata') or {}).get('voice_only') and _same_person(a,image)]
        if len(voices)>1: raise ValueError('上一段同一人物有多个音色参考，无法唯一承接')
        if voices:
            voice=voices[0]
        else:
            source_images=[a for a in source_assets if a.get('type')=='image' and
                           (a.get('metadata') or {}).get('asset_kind')=='character' and _same_person(a,image)]
            if len(source_images)>1: raise ValueError('上一段同一人物有多个参考，无法唯一承接音色')
            library=((source_images[0].get('metadata') or {}).get('library_voice') or {}) if source_images else {}
            if not library.get('path'): continue
            voice={'asset_id':'voice_'+str(meta.get('identity_id') or meta.get('role_id')),
                   'type':'audio','path':library['path'],'role':image.get('role','')+'音色参考',
                   'metadata':{'voice_only':True,'binding_source':'character_voice',
                               'identity_id':meta.get('identity_id'),'role_id':meta.get('role_id'),
                               'duration_sec':library.get('duration_sec')}}
        matches=[i for i,a in enumerate(prepared['asset_manifest']) if a.get('type')=='audio' and
                 (a.get('metadata') or {}).get('voice_only') and _same_person(a,image)]
        if len(matches)>1: raise ValueError('当前稿同一人物有多个音色参考，无法唯一承接')
        if matches:
            i=matches[0];prepared['asset_manifest'][i]=_source_slot(voice,prepared['asset_manifest'][i])
        else:
            item=copy.deepcopy(voice)
            item['metadata']['role_id']=meta.get('role_id')
            if any(a.get('asset_id',a.get('id'))==item['asset_id'] for a in prepared['asset_manifest']):
                raise ValueError('承接音色素材身份冲突')
            item['order']=max((a.order or 0 for a in H3GenerationConfig.from_mapping(prepared).asset_manifest),default=0)+1
            prepared['asset_manifest'].append(item)
        changes.append(image.get('role') or image.get('label') or image['asset_id'])
    prepared['prompt']=H3PromptReconciler().reconcile(base['prompt'],H3GenerationConfig.from_mapping(base),H3GenerationConfig.from_mapping(prepared))
    return prepared,changes


