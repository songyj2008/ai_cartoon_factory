"""Explicit spatial envelopes for existing patch-focused test fixtures."""
import json
from generation.pipelines.minimax_h3.continuity.spatial_plan import sources


def envelope(patch, roles, source_texts):
    plan = {'schema_version': 1, 'basis': [], 'relations': [], 'shots': []}
    for shot in patch.get('shots', []):
        plan['shots'].append({'shot_uid': shot['shot_uid'], 'basis_ids': [], 'views': [{
            'phase': '固定', 'camera_position': '桌端', 'view_direction': '朝主体', 'axis': '未知',
            'characters': [{'role_id': role, 'visibility': 'visible' if role in shot.get('visible_roles', []) else 'out_of_frame',
                            'layer': '主体' if role in shot.get('visible_roles', []) else '画外', 'body_view': '未知'} for role in roles]}]})
    return {'patch': patch, 'spatial_plan': plan, 'spatial_review': {'issues': []}, 'spatial_sources': source_texts}


def planner_reply(fn):
    def run(base, context):
        return envelope(fn(base, context), context['tracked_role_ids'], sources(base, context))
    return run


def model_reply(fn):
    def run(system, user, **kwargs):
        label = kwargs.get('label', '')
        if label.startswith('h3_composition_review'):
            return {'issues': []}, 0
        reply, elapsed = fn(system, user, **kwargs)
        if label.startswith('h3_composition_patch'):
            payload = json.loads(user)
            wrapper = envelope(reply, payload['tracked_role_ids'], payload['spatial_sources'])
            return {k: wrapper[k] for k in ('patch', 'spatial_plan')}, elapsed
        return reply, elapsed
    return run


def plan_patch(*args, **kwargs):
    from generation.pipelines.minimax_h3.continuity.composition import plan_composition_patch
    return plan_composition_patch(*args, **kwargs)['patch']
