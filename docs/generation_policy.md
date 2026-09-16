# LiconMSR Direct Video Generation Policy

This project now uses one primary workflow: `templates/Ltx2.3_Licon-MSRV1_api.json`.

The goal is to keep the runtime small, deterministic, and centered on the latest working ComfyUI graph. Do not maintain compatibility with the old Qwen key-image pipeline, GuideContract pipeline, PromptRelay timeline pipeline, or dynamic i2v workflow unless the user explicitly requests a separate restoration.

## Core Principle

```text
LLM = write the complete video prompt
Local code = validate and patch the LiconMSR workflow
ComfyUI = generate the video
```

The runtime must not become a creative repair system or a legacy compatibility layer.

## Primary Data Contract

All video generation should reduce to a single `VideoJobContract`:

```json
{
  "prompt": "complete video prompt",
  "duration_sec": 15,
  "fps": 50,
  "total_frames": 750,
  "reference_images": ["ref1.png"],
  "background_image": "background.png",
  "output_prefix": "video"
}
```

Required rules:

- `prompt` must be non-empty.
- `duration_sec` must be positive.
- `fps` defaults to `50`.
- `total_frames = duration_sec * fps`.
- `reference_images` must contain 1 to 4 images.
- `background_image` is required.
- All referenced files must exist before submit.

## Workflow Node Ownership

The LiconMSR API workflow is patched through fixed nodes:

| Node | Meaning |
|---|---|
| `5` | Positive video prompt |
| `50` | Total frame count |
| `80` | Reference image 1 |
| `81` | Reference image 2 |
| `82` | Reference image 3 |
| `83` | Reference image 4 |
| `84` | Background image |
| `28` | LiconMSR image wiring |

Unused reference slots must be disconnected from node `28`. Do not fill unused slots with placeholders and do not repeat the last reference image.

## Prompt Policy

The prompt should be a complete video description, not a chain of intermediate prompts.

Recommended structure:

```text
Style and camera rule.

Scene and background.

Reference descriptions:
Reference image 1: ...
Reference image 2: ...

Timeline:
0-5 seconds — ...
5-10 seconds — ...
10-15 seconds — ...

Constraints:
Keep reference identity, clothing, object appearance, and background layout consistent. No role swapping. No extra clear characters unless described. No cuts when the prompt asks for one continuous shot.
```

## What Belongs in Local Code

Only deterministic workflow validation belongs in local code:

- prompt is present
- duration/fps/total frame calculation
- image count is 1-4
- background exists
- node IDs exist in the workflow template
- node `28` has only the connected slots that match the actual references
- node `84` always receives the background
- ComfyUI submit payload is valid JSON

## What Does Not Belong in Local Code

Do not add runtime patches for vague creative preferences:

- make the story funnier
- make acting more natural
- make pacing better
- make camera more cinematic
- make emotions stronger
- repair one story-specific plot mistake

These belong in user input, examples, prompt templates, or project bible text.

## Deprecated Concepts

The following are no longer part of the active architecture:

- Qwen key-image generation
- Qwen slot contracts
- GuideContract generation
- TimelineSegmentContract generation
- PromptRelay timeline data
- local prompts
- segment lengths
- guide frames
- part splitting for old LTX workflows
- `master_controller_v4.py`
- `build_workflow_submit_v4.py`

New code should not depend on these concepts.

## Final Rule

Prefer deletion and simplification over compatibility. The latest LiconMSR workflow is the source of truth.
