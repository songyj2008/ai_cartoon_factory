"""Match approved prompt evidence, without judging or rewriting creative content."""
from __future__ import annotations

import re


_SHOT = re.compile(r'\[Shot\s+\d+\]', re.I)
_DIALOGUE = re.compile(r'<d\s*>.*?</d\s*>', re.I | re.S)


def normalized_tags(text):
    return re.sub(r'<\s*(Subject|Picture|Audio)\s+(\d+)\s*>',
                  lambda m: f'<{m[1].title()} {m[2]}>', str(text or ''), flags=re.I)


def shot_blocks(prompt):
    text = str(prompt or '')
    section = re.search(r'【详细镜头描述】(.*?)(?=\n【|\Z)', text, re.S)
    if section:
        text = section[1]
    else:
        text = re.split(r'【(?:整体声音环境|非叙事性音乐)】', text, maxsplit=1)[0]
    headers = list(_SHOT.finditer(text))
    return [text[header.start():headers[i + 1].start() if i + 1 < len(headers) else len(text)].strip()
            for i, header in enumerate(headers)]


def speech_layout(prompt='', shots=None):
    """These are text declarations, never measured silence or speech intervals."""
    if prompt:
        blocks = shot_blocks(prompt)
        states = ['dialogue' if _DIALOGUE.search(block) else
                  'continuing_dialogue' if re.search(r'(?:接着|接上|延续|继续|跨镜)[^。；\n]{0,15}(?:说|对白|发言)|continues_previous', block)
                  else 'no_dialogue_text' for block in blocks]
    else:
        states = []
        for shot in shots or []:
            if not isinstance(shot, dict):
                continue
            values = shot.get('dialogues') or shot.get('dialogue')
            states.append('dialogue' if values else 'continuing_dialogue'
                          if shot.get('continues_previous') else 'no_dialogue_text')
    talking = {'dialogue', 'continuing_dialogue'}
    return {'basis': 'prompt_declarations_not_media_measurement', 'shot_states': states,
            'starts_without_dialogue_text': bool(len(states) > 1 and states[0] == 'no_dialogue_text'
                                                 and any(state in talking for state in states[1:])),
            'has_no_dialogue_to_dialogue': any(a == 'no_dialogue_text' and b in talking
                                              for a, b in zip(states, states[1:]))}


def speaker_ids(manifest, prompt='', dialogue=None, *, people_reader, owner_reader, canonical):
    """Only explicit, uniquely resolved speaking owners participate in matching."""
    normalized = normalized_tags(prompt)
    people = people_reader(manifest, normalized)
    found = set()
    if normalized:
        for block in shot_blocks(normalized):
            previous = 0
            for match in _DIALOGUE.finditer(block):
                owner = owner_reader(block[previous:match.start()], people)
                if owner:
                    found.add(canonical(owner['identity_id']))
                previous = match.end()
    else:
        for unit in dialogue or []:
            if not isinstance(unit, dict):
                continue
            speaker = normalized_tags(unit.get('speaker') or unit.get('speaker_name') or '').strip()
            matches = {canonical(person['identity_id']) for person in people
                       if speaker and speaker in {normalized_tags(name) for name in
                                                  person['names'] + person['subject_tags'] + [person['identity_id']]}}
            if len(matches) == 1:
                found.update(matches)
    return found


def prompt_evidence(prompt, *, prefer_transition=False):
    """Return bounded original text with explicit truncation, not an LLM rewrite."""
    prompt = str(prompt or '')
    blocks = shot_blocks(prompt)
    start = 0
    if prefer_transition:
        states = speech_layout(prompt)['shot_states']
        for index, (before, after) in enumerate(zip(states, states[1:])):
            if before == 'no_dialogue_text' and after in {'dialogue', 'continuing_dialogue'}:
                start = index
                break
    excerpt = '\n'.join(blocks[start:start + 2])
    definitions = re.search(r'【主体定义】(.*?)(?=\n【|\Z)', prompt, re.S)
    bindings = re.search(r'【人物音色绑定】(.*?)(?=\n【|\Z)', prompt, re.S)
    return {'subject_definitions': definitions[1].strip()[:850] if definitions else '',
            'voice_binding': bindings[1].strip()[:550] if bindings else '',
            'shot_descriptions': excerpt[:3100],
            'excerpt_truncated': len(excerpt) > 3100 or bool(definitions and len(definitions[1].strip()) > 850)
                                 or bool(bindings and len(bindings[1].strip()) > 550),
            'historical_numbering_only': True}
