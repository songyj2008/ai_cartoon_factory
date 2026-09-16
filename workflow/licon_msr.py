"""Direct LiconMSR video workflow patching, submission, and result capture."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.context import BASE_DIR, CONFIG, LICON_MSR_API_TEMPLATE, get_project_dir
from workflow.submit_factory import get_submit_provider
from workflow.submit_provider import SubmitResult


MODEL_ID = "ltx23_licon_msr_v2"
DEFAULT_FPS = int(CONFIG.get("fps") or 50)
PROMPT_NODE = str(CONFIG.get("licon_prompt_node") or "5")
TOTALFRAME_NODE = str(CONFIG.get("licon_totalframe_node") or "50")
REFERENCE_NODES = [str(x) for x in CONFIG.get("licon_reference_nodes") or ["80", "81", "82", "83"]]
BACKGROUND_NODE = str(CONFIG.get("licon_background_node") or "84")
LICON_MSR_NODE = str(CONFIG.get("licon_msr_node") or "28")
SAVE_VIDEO_NODE = "20"
MAX_REFERENCE_IMAGES = int(CONFIG.get("reference_image_max") or 4)
MIN_REFERENCE_IMAGES = int(CONFIG.get("reference_image_min", 0) or 0)


@dataclass
class VideoJobContract:
    prompt: str
    duration_sec: float
    reference_images: list[str]
    background_image: str
    fps: int = DEFAULT_FPS
    output_prefix: str = ""
    part_id: str = ""
    model_id: str = MODEL_ID
    asset_slots: list[dict[str, str]] = field(default_factory=list)
    total_frames: int = field(init=False)

    def __post_init__(self) -> None:
        self.prompt = str(self.prompt or "").strip()
        self.duration_sec = float(self.duration_sec)
        self.fps = int(self.fps or DEFAULT_FPS)
        self.reference_images = [str(x or "").strip() for x in (self.reference_images or []) if str(x or "").strip()]
        self.background_image = str(self.background_image or "").strip()
        self.output_prefix = str(self.output_prefix or "").strip()
        self.part_id = str(self.part_id or "").strip()
        self.model_id = str(self.model_id or MODEL_ID).strip() or MODEL_ID
        self.total_frames = int(round(self.duration_sec * self.fps))


def _resolve_path(path_value: str) -> str:
    path = Path(str(path_value or "").strip())
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path.resolve())


def validate_video_job(job: VideoJobContract) -> VideoJobContract:
    if not job.prompt:
        raise ValueError("video prompt must not be empty")
    if job.duration_sec <= 0:
        raise ValueError("duration_sec must be positive")
    if job.fps <= 0:
        raise ValueError("fps must be positive")
    if job.total_frames <= 0:
        raise ValueError("total_frames must be positive")
    if len(job.reference_images) < MIN_REFERENCE_IMAGES:
        raise ValueError(f"at least {MIN_REFERENCE_IMAGES} reference image is required")
    if len(job.reference_images) > MAX_REFERENCE_IMAGES:
        raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images are allowed")
    if not job.background_image:
        raise ValueError("background image is required")

    missing: list[str] = []
    for image in job.reference_images + [job.background_image]:
        resolved = Path(_resolve_path(image))
        if not resolved.exists():
            missing.append(str(resolved))
    if missing:
        raise FileNotFoundError("referenced image files do not exist: " + "; ".join(missing))
    return job


def load_template(template_path: str | Path | None = None) -> dict[str, Any]:
    models = CONFIG.get("models") if isinstance(CONFIG.get("models"), dict) else {}
    ltx_config = models.get(MODEL_ID) if isinstance(models, dict) else {}
    configured_path = (ltx_config or {}).get("template_api_path") or CONFIG.get("template_api_path", str(LICON_MSR_API_TEMPLATE))
    configured = Path(template_path) if template_path else BASE_DIR / configured_path
    candidates = [configured]
    if not configured.is_absolute():
        candidates[0] = BASE_DIR / configured
    candidates.extend(
        [
            BASE_DIR / "templates" / "Ltx2.3_Licon-MSRV2_api.json",
            BASE_DIR / "templates" / "Ltx2.3_Licon-MSRV2.json",
            BASE_DIR / "templates" / "Ltx2.3_Licon-MSRV1_api.json",
            BASE_DIR / "templates" / "Ltx2.3_Licon-MSRV1.json",
        ]
    )

    checked: list[str] = []
    for path in candidates:
        path = path if path.is_absolute() else BASE_DIR / path
        path = path.resolve()
        if str(path) in checked:
            continue
        checked.append(str(path))
        if path.exists():
            with open(path, "r", encoding="utf-8-sig") as file:
                return json.load(file)

    raise FileNotFoundError(
        "LiconMSR API template not found. Checked: "
        + "; ".join(checked)
        + ". Make sure templates/Ltx2.3_Licon-MSRV1_api.json is copied into the runtime project directory."
    )


def _require_node(api: dict[str, Any], node_id: str) -> dict[str, Any]:
    if node_id not in api:
        raise KeyError(f"LiconMSR workflow missing node {node_id}")
    node = api[node_id]
    if not isinstance(node, dict):
        raise TypeError(f"LiconMSR workflow node {node_id} is not an object")
    node.setdefault("inputs", {})
    return node


def patch_licon_msr_workflow(template: dict[str, Any], job: VideoJobContract) -> dict[str, Any]:
    job = validate_video_job(job)
    api = copy.deepcopy(template)

    _require_node(api, PROMPT_NODE)["inputs"]["text"] = job.prompt
    _require_node(api, TOTALFRAME_NODE)["inputs"]["value"] = job.total_frames

    licon_inputs = _require_node(api, LICON_MSR_NODE)["inputs"]
    asset_slots: list[dict[str, str]] = []
    for slot_index, ref_node_id in enumerate(REFERENCE_NODES, start=1):
        slot_key = str(slot_index)
        if slot_index <= len(job.reference_images):
            image_path = _resolve_path(job.reference_images[slot_index - 1])
            _require_node(api, ref_node_id)["inputs"]["file_name"] = image_path
            licon_inputs[slot_key] = [ref_node_id, 0]
            asset_slots.append(
                {
                    "name": f"reference_{slot_index}",
                    "node_id": ref_node_id,
                    "input_name": "file_name",
                    "path": image_path,
                }
            )
        else:
            licon_inputs.pop(slot_key, None)
            api.pop(ref_node_id, None)

    background_path = _resolve_path(job.background_image)
    _require_node(api, BACKGROUND_NODE)["inputs"]["file_name"] = background_path
    licon_inputs["background"] = [BACKGROUND_NODE, 0]
    asset_slots.append(
        {"name": "background", "node_id": BACKGROUND_NODE, "input_name": "file_name", "path": background_path}
    )
    job.asset_slots = asset_slots

    if SAVE_VIDEO_NODE in api:
        prefix = job.output_prefix or str(CONFIG.get("licon_output_prefix") or "LTX-2/MSR_direct")
        api[SAVE_VIDEO_NODE].setdefault("inputs", {})["filename_prefix"] = prefix

    return api


def save_workflow(api: dict[str, Any], output_prefix: str = "licon_msr_direct") -> Path:
    workflow_dir = get_project_dir() / "workflows" / "licon_msr"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    safe_prefix = str(output_prefix or "licon_msr_direct").replace("/", "_").replace("\\", "_")
    path = workflow_dir / f"{safe_prefix}_api.json"
    with open(path, "w", encoding="utf-8") as file:
        json.dump(api, file, ensure_ascii=False, indent=2)
    return path


def build_and_optionally_submit(
    prompt: str,
    duration_sec: float,
    reference_images: list[str],
    background_image: str,
    output_prefix: str = "",
    submit: bool | None = None,
    part_id: str = "",
    wait: bool = True,
    model_id: str = MODEL_ID,
) -> dict[str, Any]:
    job = VideoJobContract(
        prompt=prompt,
        duration_sec=duration_sec,
        fps=DEFAULT_FPS,
        reference_images=reference_images,
        background_image=background_image,
        output_prefix=output_prefix,
        part_id=part_id,
        model_id=model_id,
    )
    template = load_template()
    api = patch_licon_msr_workflow(template, job)
    workflow_path = save_workflow(api, output_prefix=output_prefix or "licon_msr_direct")
    should_submit = bool(CONFIG.get("submit_to_comfyui", True)) if submit is None else bool(submit)
    submit_result = SubmitResult(backend="", submitted=False)

    if should_submit:
        provider = get_submit_provider(model_id=job.model_id)
        submit_result = provider.submit(api=api, job=job, workflow_path=str(workflow_path), wait=wait)

    actual_media_spec: dict[str, Any] = {}
    if submit_result.video_path:
        from generation.media import inspect_media

        actual_media_spec = inspect_media(submit_result.video_path).to_dict()

    return {
        "job": {
            "prompt": job.prompt,
            "duration_sec": job.duration_sec,
            "fps": job.fps,
            "total_frames": job.total_frames,
            "reference_images": [_resolve_path(x) for x in job.reference_images],
            "background_image": _resolve_path(job.background_image),
            "output_prefix": job.output_prefix,
            "part_id": job.part_id,
            "model_id": job.model_id,
            "asset_slots": job.asset_slots,
        },
        "backend": submit_result.backend,
        "workflow_path": str(workflow_path),
        "submitted": should_submit,
        "prompt_id": submit_result.prompt_id,
        "task_id": submit_result.task_id,
        "video_path": submit_result.video_path,
        "output_urls": submit_result.output_urls,
        "actual_media_spec": actual_media_spec,
        "submit_result": submit_result.submit_result,
        "raw_submit": submit_result.raw,
    }
