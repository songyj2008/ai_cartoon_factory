"""Use the existing compiler's numbering, not manifest row numbers."""
from ..asset_binder import H3AssetBinder
from ..prompt_compiler import H3PromptCompiler

def reference_map(manifest):
    result={}
    for entry in H3PromptCompiler()._subject_reference_entries(H3AssetBinder().bind({'asset_manifest':manifest})):
        meta=entry['metadata']
        key=str(meta.get('role_id') or meta.get('scene_id') or meta.get('identity_id') or entry['asset_id'])
        if key in result: raise ValueError('承接身份存在多个参考，无法唯一编号：'+key)
        result[key]={'role_id':key,'subject_tag':entry['subject_tag'],'picture_tag':entry['picture_tag'],
                     'asset_id':entry['asset_id'],'name':entry['name']}
    return result

def project_facts(extraction,manifest):
    mapping=reference_map(manifest); result={}
    for person in extraction['state']['characters']:
        for name,fact in person['facts'].items():
            if fact['value'] is None:continue
            key=person['role_id']+':'+name
            result[key]={'role_id':person['role_id'],'field':name,'value':fact['value'],
                         'reference':mapping.get(person['role_id']),
                         'relative_to':mapping.get(fact.get('relative_to_role_id'))}
    return result


def normalize_script_entities(prompt, manifest):
    import re
    from ..dialogue_text import transform_outside_dialogue
    entries=reference_map(manifest)
    aliases={ref['subject_tag']:role for role,ref in entries.items()}
    def replace(text):
        def sub(match):
            tag='<Subject '+match[1]+'>'
            if tag not in aliases: raise ValueError('来源人物编号未绑定：'+tag)
            return aliases[tag]
        return re.sub(r'<\s*Subject\s+(\d+)\s*>',sub,text,flags=re.I)
    return transform_outside_dialogue(prompt,replace),aliases


def model_references(manifest):
    """Reference semantics only; files and library metadata stay local."""
    bindings=H3AssetBinder().bind({'asset_manifest':manifest})
    subjects=reference_map(manifest)
    by_asset={ref['asset_id']:ref for ref in subjects.values()}
    rows=[]
    usage_fields=('asset_kind','voice_only','voice_usage','picture_role','reference_role',
                  'usage','shot_indices','retention_marker','retention_note')
    for tag,kind,asset in bindings.prompt_references:
        meta=asset.metadata
        ref=by_asset.get(asset.asset_id)
        row={'tag':tag,'type':kind}
        if ref: row.update(role_id=ref['role_id'],name=ref['name'],subject_tag=ref['subject_tag'])
        else:
            if meta.get('role_id'): row['role_id']=meta['role_id']
            row['name']=asset.label or asset.role
        if asset.role and asset.role!=row.get('name'): row['role']=asset.role
        anchor=H3PromptCompiler._picture_anchor_role(meta)
        if anchor:row['anchor']=anchor
        for key in usage_fields:
            if key in meta: row[key]=meta[key]
        if kind=='video':row['use_video_audio']=asset.use_video_audio
        rows.append(row)
    return rows
