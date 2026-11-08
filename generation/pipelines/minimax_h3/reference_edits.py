"""Conservative reference cleanup. Dialogue is opaque; visual dependencies block removal."""
import re
from .dialogue_text import transform_outside_dialogue


def remove_character_voice(prompt, manifest, selected):
    """Remove only known voice annotations; keep speech and unknown dependencies."""
    import copy
    from .asset_binder import H3AssetBinder
    meta=selected.get('metadata') or {}
    if selected.get('type')!='audio' or not meta.get('voice_only'):
        return prompt,manifest,False
    def owns(image):
        other=image.get('metadata') or {}
        if image.get('type')!='image' or other.get('asset_kind')!='character': return False
        if meta.get('role_id') and other.get('role_id'):
            return meta['role_id']==other['role_id']
        return bool(meta.get('identity_id') and meta['identity_id']==other.get('identity_id'))
    if not any(owns(a) for a in manifest):
        return prompt,manifest,False
    bindings=H3AssetBinder().bind({'asset_manifest':manifest})
    binding=next(a for a in bindings.audios if a.asset.asset_id==selected.get('asset_id',selected.get('id')))
    tag=re.escape(binding.prompt_tag)
    owner=r'<Subject\s+\d+>'
    def clean(text):
        text=re.sub(r'本句音色参考[：:]\s*'+tag+r'\s*[，,]\s*仅用于\s*'+owner+r'的声音\s*[；;]?', '',text)
        return re.sub(r'使用\s*'+tag+r'\s*绑定\s*'+owner+r'\s*音色\s*[；;]?', '',text)
    prompt=transform_outside_dialogue(prompt,clean)
    result=copy.deepcopy(manifest)
    for image in result:
        if owns(image): image.setdefault('metadata',{})['voice_disabled']=True
    return prompt,result,True


def restore_removed_mentions(text, old, new, compiler):
    remaining = {a.asset.asset_id for a in new.images}
    identities = {a.asset.metadata.get('identity_id') for a in new.images if a.asset.metadata.get('identity_id')}
    paths = {str(a.asset.path).replace('\\', '/').casefold() for a in new.images}
    for entry in compiler._subject_reference_entries(old):
        asset = next(a.asset for a in old.images if a.asset.asset_id == entry['asset_id'])
        if asset.asset_id in remaining or (asset.metadata.get('identity_id') and asset.metadata['identity_id'] in identities) or str(asset.path).replace('\\', '/').casefold() in paths:
            continue
        tag = entry['subject_tag']
        # Only narrative sections are known mentions. A leftover tag in a Shot,
        # first frame or composition remains a dependency for the reconciler.
        sections = re.split(r'(?m)(^【[^】]+】[ \t]*$)', text)
        title = ''
        for i, part in enumerate(sections):
            if part.startswith('【'):
                title = part.strip()
            elif title in {'【任务概述】', '【剧情上下文】'}:
                sections[i] = transform_outside_dialogue(part, lambda v: re.sub(re.escape(tag), lambda m: entry['name'], v, flags=re.I))
        text = ''.join(sections)
    return text
