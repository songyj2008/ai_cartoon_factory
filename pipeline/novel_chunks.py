"""Lossless input batching; all Beat and Shot decisions remain model-owned."""
import copy
import hashlib
import json


def split_source(text, limit):
    """Prefer paragraph/sentence boundaries outside quoted dialogue."""
    if len(text) <= limit:
        return [text]
    boundaries = []
    stack = []
    pairs = {'“': '”', '「': '」', '『': '』', '‘': '’'}
    for index, char in enumerate(text, 1):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == '"':
            if stack and stack[-1] == '"':
                stack.pop()
            else:
                stack.append('"')
        if not stack and (char in '\n。！？!?；;' or char in pairs.values()):
            boundaries.append(index)
    boundaries.append(len(text))
    chunks = []
    start = 0
    while len(text) - start > limit:
        choices = [end for end in boundaries if start < end <= start + limit]
        if not choices:
            end = next(end for end in boundaries if end > start)
            if end - start > limit * 2:
                raise ValueError('小说存在过长且无可用边界的段落或对白，请补充分段或检查引号配对后重试')
        else:
            end = choices[-1]
        chunks.append(text[start:end])
        start = end
    if start < len(text):
        chunks.append(text[start:])
    assert ''.join(chunks) == text
    return chunks


def _compact(text):
    return ''.join(text.split())


def generate_chunked_beats(payload, *, config, model_id, temp_dir, generate):
    from generation.pipelines.minimax_h3.planning_checkpoints import load_novel_beats, save_novel_beats
    from services.text_jobs import phase, check_cancelled
    limit = max(1000, int(config.get('novel_chunk_chars') or 6000))
    text = payload['novel_text']
    chunks = split_source(text, limit)
    if len(chunks) == 1:
        return generate(payload, model_id=model_id, config=config, temp_dir=temp_dir)
    results = []
    offset = 0
    for index, chunk in enumerate(chunks, 1):
        check_cancelled()
        phase(f'小说分批拆解 {index}/{len(chunks)}')
        request = copy.deepcopy(payload)
        request.update(novel_text=chunk,
            target_duration_sec=max(1, round(payload['target_duration_sec'] * len(chunk) / len(text))),
            target_beat_count=max(1, round(payload['target_beat_count'] * len(chunk) / len(text))),
            source_batch={'index': index, 'total': len(chunks), 'start': offset, 'end': offset + len(chunk)},
            reading_context={'previous': text[max(0, offset-500):offset],
                             'following': text[offset+len(chunk):offset+len(chunk)+500]},
            batching_instruction='只改编 novel_text，reading_context 仅供理解称谓和句间关系，不得重复纳入摘录或对白。'
                'source_excerpt 按顺序逐字覆盖本批全部原文，不遗漏、不重复。保持项目人物身份；'
                f'新场景ID使用 batch_{index}_ 前缀，已有场景沿用项目设定ID。不要推断连续场景分组。')
        key = hashlib.sha256(json.dumps([2, request, config], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        data = load_novel_beats(key)
        if data is None:
            batch_dir = temp_dir / f'novel_batch_{index:04d}'
            batch_dir.mkdir(parents=True, exist_ok=True)
            data = generate(request, model_id=model_id, config=config, temp_dir=batch_dir)
            covered = ''.join(str(beat.get('source_excerpt') or '') for beat in data.get('beats') or [])
            if _compact(covered) != _compact(chunk):
                raise ValueError(f'小说第 {index}/{len(chunks)} 批摘录未完整按序覆盖原文，结果未发布；已完成批次保留')
            save_novel_beats(key, data)
        results.append(data)
        offset += len(chunk)
    combined = copy.deepcopy(results[0])
    combined['beats'] = []
    combined['scene_contexts'] = []
    contexts = {}
    issues = []
    for result in results:
        beat_offset = len(combined['beats'])
        for beat in result['beats']:
            beat = copy.deepcopy(beat)
            beat['id'] = len(combined['beats']) + 1
            combined['beats'].append(beat)
        for scene in result.get('scene_contexts') or []:
            key = scene.get('scene_id') or scene.get('id')
            if not key or (key in contexts and contexts[key] != scene):
                raise ValueError('小说分批场景身份缺失或定义冲突，请核对项目场景设定后重试')
            contexts[key] = scene
        for issue in result.get('planning_issues') or []:
            issue = copy.deepcopy(issue)
            if isinstance(issue.get('beat_index'), int):
                issue['beat_index'] += beat_offset
            issues.append(issue)
    combined.update(scene_contexts=list(contexts.values()), locked_outcome=results[-1].get('locked_outcome'),
                    total_duration_sec=sum(float(b['estimated_duration_sec']) for b in combined['beats']),
                    target_duration_sec=payload['target_duration_sec'], target_beat_count=payload['target_beat_count'],
                    source_batches=[{'index': i, 'characters': len(chunk)} for i, chunk in enumerate(chunks, 1)])
    combined.pop('grouping_review', None)
    combined['grouping_reviews'] = [copy.deepcopy(r.get('grouping_review')) for r in results]
    if issues:
        combined.update(planning_state='warning', planning_issues=issues)
    return combined
