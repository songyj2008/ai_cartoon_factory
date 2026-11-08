"""Lossless semantic projections for LLM inputs; persisted objects stay local."""
import copy
import json


def select_fields(value, fields):
    return {key: copy.deepcopy(value[key]) for key in fields if key in value}


STORY_FIELDS = ('title', 'summary', 'story', 'main_conflict', 'ending', 'source_topic')
BEAT_FIELDS = ('id', 'title', 'plot', 'scene_id', 'important_roles', 'visible_roles',
               'reference_roles', 'offscreen_speakers', 'mentioned_roles', 'dialogue_units',
               'dialogue_authoritative', 'action_units', 'event_units', 'identity_constraints',
               'background_extras', 'estimated_duration_sec', 'duration_sec', 'source_excerpt',
               'model_hints', 'scene_contexts', 'binding_schema_version')


def project_bible_context(bible):
    # Custom creative fields are supported by the project bible editor. Only
    # known local binding flags are removed; never prune false/null/empty facts.
    result = copy.deepcopy(bible or {})
    for group in ('core_characters', 'supporting_characters', 'allowed_backgrounds'):
        for entry in result.get(group, []):
            for key in ('asset_identity_id', 'asset_background_id', 'locked', 'need_reference_image'):
                entry.pop(key, None)
    return result


def reference_context(manifest):
    fields = ('asset_id', 'id', 'type', 'role', 'label', 'order', 'use_video_audio',
              'duration_sec', 'start_sec', 'end_sec', 'metadata')
    local_metadata = ('path', 'relative_path', 'image_path', 'audio_path', 'video_path',
                      'binding_source', 'file_hash', 'sha256', 'created_at', 'updated_at')
    result = []
    for asset in manifest or []:
        item = select_fields(asset, fields)
        if isinstance(item.get('metadata'), dict):
            for key in local_metadata:
                item['metadata'].pop(key, None)
        result.append(item)
    return result


def request_metrics(messages):
    """Measure exactly the message array/text being handed to the transport."""
    serialized = messages if isinstance(messages, str) else json.dumps(messages, ensure_ascii=False)
    return {'schema_version': 1, 'chars': len(serialized),
            'utf8_bytes': len(serialized.encode('utf-8'))}


INPUT_REFERENCE_RULE = ('输入中的{"input_ref":"#/..."}是本次输入内部的JSON Pointer引用，'
                        '请读取该路径的完整值；不是省略、空值或新的事实。输出仍须返回原合同要求的完整结果。')


def referenced_feedback(feedback, sources):
    """Replace exact diagnostic copies only; authoritative input stays intact."""
    index = {}
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    def visit(value, path):
        if isinstance(value, (dict, list, str)):
            text = encoded(value)
            # Short references would cost more than the original facts.
            if len(text) >= 80:
                index.setdefault(text, path)
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for key, item in enumerate(value):
                visit(item, path + '/' + str(key))
    for key, value in sources.items():
        visit(value, '#/' + str(key).replace('~', '~0').replace('/', '~1'))
    def compact(value):
        target = index.get(encoded(value)) if isinstance(value, (dict, list, str)) else None
        if target and len(encoded({'input_ref': target})) < len(encoded(value)):
            return {'input_ref': target}
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return copy.deepcopy(value)
    return compact(feedback)


def prompt_ir_context(prompt_ir):
    """Reference only exact, same-speaker dialogue copies in event records."""
    result = copy.deepcopy(prompt_ir)
    events = result.get('events') or {}
    lines = events.get('dialogue_units') or []
    for group in ('event_units', 'event_flow'):
        for event in events.get(group) or []:
            line = event.get('dialogue')
            if not isinstance(line, str):
                continue
            for index, unit in enumerate(lines):
                if line != unit.get('line'):
                    continue
                if event.get('actor') and unit.get('speaker'):
                    same_speaker = event['actor'] == unit['speaker']
                else:
                    same_speaker = bool(event.get('actor_name') and event['actor_name'] == unit.get('speaker_name'))
                if not same_speaker:
                    continue
                ref = {'input_ref': f'#/events/dialogue_units/{index}/line'}
                if len(json.dumps(ref, ensure_ascii=False)) < len(json.dumps(line, ensure_ascii=False)):
                    event['dialogue'] = ref
                break
    return result


def log_request_metrics(label, messages):
    from services.logger import log
    metrics = request_metrics(messages)
    log('[llm_input] ' + str(label) + ' ' + json.dumps(metrics, ensure_ascii=False), 'STEP')
    return metrics
