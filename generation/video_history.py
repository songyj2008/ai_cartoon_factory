"""Display task videos from provider attempts and browser render records."""


def video_attempts(segment):
    attempts = [item for item in segment.get('runninghub_attempts') or [] if isinstance(item, dict)]
    seen = {str(item.get('task_id') or '') for item in attempts}
    for render in segment.get('renders') or []:
        if not isinstance(render, dict):
            continue
        submission = render.get('submission') or {}
        task_id = str(submission.get('task_id') or '')
        backend = submission.get('backend')
        if not task_id or task_id in seen:
            continue
        if backend != 'doubao_web':
            from generation.channels import get_channel
            try:
                if get_channel(backend).uses_workflow:
                    continue
            except ValueError:
                continue
        output = render.get('output') or {}
        attempts.append({
            'task_id': task_id, 'backend': backend,
            'model_id': (render.get('model') or {}).get('id'),
            'status': render.get('status'), 'submitted_prompt': render.get('prompt_snapshot'),
            'video_path': output.get('video_path'), 'video_deleted': output.get('video_deleted'),
        })
        seen.add(task_id)
    return attempts
