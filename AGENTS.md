# ai_cartoon_factory Project Notes

`ai_cartoon_factory` is now a single-workflow AI video generation project.

## Primary Architecture

The only primary generation path is:

```text
Video prompt + 1-4 reference images + 1 background image
-> templates/Ltx2.3_Licon-MSRV1_api.json
-> ComfyUI /prompt
-> final video
```

The project must not preserve legacy Qwen key-image, GuideContract, PromptRelay timeline, or dynamic i2v compatibility unless the user explicitly asks to restore them.

## Current Workflow Contract

Use `templates/Ltx2.3_Licon-MSRV1_api.json` as the source workflow.

Patch these nodes only:

- Node `5`: positive video prompt text.
- Node `50`: `totalframe`, calculated as `duration_sec * fps`.
- Nodes `80`, `81`, `82`, `83`: optional reference images, 1-4 total.
- Node `84`: required background image.
- Node `28`: `LiconMSR` reference inputs. Connect only the reference slots that exist; remove unused `1`-`4` inputs instead of using placeholders or repeated images.

The default fps for this workflow is `50`.

## Deletion / Compatibility Policy

Prefer removing old compatibility code over maintaining parallel paths.

Legacy concepts should be removed from active runtime code:

- Qwen key image generation
- `qwen_slot_contract`
- `GuideContract`
- `TimelineSegmentContract`
- `timeline_data`
- `local_prompts`
- `segment_lengths`
- `guide_frames`
- `PromptRelayTimelineInjector`
- `master_controller_v4.py`
- `build_workflow_submit_v4.py`
- old LTX dynamic i2v templates

Do not add adapters, fallbacks, or mixed-mode compatibility for the old pipeline.

## General Policy

This remains a general AI video generation tool. Do not hard-code one story, one scene, one character set, or one genre.

Local code should validate deterministic workflow facts only:

- prompt is non-empty
- duration is positive
- fps is positive, default `50`
- reference image count is 1-4
- background image is required
- referenced files exist
- total frames equals `duration_sec * fps`
- LiconMSR node wiring matches the actual reference image count

Creative quality belongs in prompts, examples, project bible, and user input, not runtime repair patches.

## Responsibility Boundaries

This remains a general AI video generation tool.
Do not optimize runtime rules around one story, one scene, one set of characters, or one observed failure case.

### Architecture Principles

1. The model is responsible for creation, but must create inside explicit structural boundaries.
2. Local code is responsible for validating structure, not rewriting plots or repairing creativity.
3. Only deterministic constraints belong in runtime code:
   - JSON structure
   - role ids and visibility
   - reference image assignment
   - duration
   - beat segmentation
   - speaking ownership
   - action complexity limits
   - workflow contracts
4. Style, realism, pacing, dramatic quality, directing taste, cinematic language, and subjective quality belong in prompts, examples, project bible, and generation configuration.
5. A single story failure must not automatically become a global runtime rule.

### Where To Fix Problems

If the issue is story structure, character visibility, scene transitions, dialogue ownership, beat decomposition, duration planning, or role allocation:
→ fix story / beats generation prompts and structural contracts.

If the issue is keyframe identity, pose, facial consistency, composition, or reference binding:
→ fix image prompt compilation and reference assignment rules.

If the issue is final video lip sync, motion continuity, action execution, temporal coherence, or video behavior:
→ fix video prompt compilation or workflow prompt generation.

If the issue is segment duration, grouping, frame allocation, or video splitting:
→ fix duration policy, beat grouping policy, or workflow segmentation rules.

If the issue only appears in one specific story or wording:
→ do not add runtime logic; fix the input data, examples, prompt instructions, or project bible.

## Environment

This is a Windows project. Use the `ai_cartoon_factory` conda environment for local checks.
