"""RunningHub Task API submit provider."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.context import get_project_dir
from services.logger import log
from services.secrets import RUNNINGHUB_API_KEY_ENV, secret_value
from workflow.runninghub_asset_cache import get_cached_file_name, put_cached_file_name
from workflow.runninghub_client import RunningHubClient
from workflow.submit_factory import submit_backend_config
from workflow.submit_provider import SubmitResult


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _filename_token(value: str, fallback: str) -> str:
    """Keep provider IDs usable as a single, deterministic filename token."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return token or fallback


class RunningHubSubmitProvider:
    name = "runninghub"

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = str(model_id or "").strip()
        self.config = submit_backend_config(self.name, model_id=self.model_id)
        api_key = secret_value(RUNNINGHUB_API_KEY_ENV)
        if not api_key:
            raise ValueError(f"RunningHub API key is not configured. Set {RUNNINGHUB_API_KEY_ENV} in UI settings.")
        instance_type = str(self.config.get("instanceType") or self.config.get("instance_type") or "").strip()
        if not instance_type and bool(self.config.get("use_plus_instance")):
            instance_type = "plus"
        self.client = RunningHubClient(
            base_url=str(self.config.get("base_url") or "https://www.runninghub.cn"),
            api_key=api_key,
            workflow_id=str(self.config.get("workflow_id") or ""),
            poll_seconds=float(self.config.get("poll_seconds") or 5),
            timeout_seconds=int(self.config.get("timeout_seconds") or 7200),
            workflow_payload_mode=str(self.config.get("workflow_payload_mode") or "string"),
            instance_type=instance_type,
        )
        self.reuse_uploaded_assets = bool(self.config.get("reuse_uploaded_assets", True))

    def _upload_or_reuse(self, local_path: str | Path) -> str:
        path = Path(local_path).resolve()
        if self.reuse_uploaded_assets:
            cached = get_cached_file_name(path)
            if cached:
                log(f"[LICON_MSR][runninghub] upload cache hit: {path.name}", "STEP")
                return cached
        log(f"[LICON_MSR][runninghub] upload media: {path.name}", "STEP")
        file_name = self.client.upload_media(path)
        put_cached_file_name(path, file_name)
        return file_name

    def _patch_uploaded_media(self, api: dict[str, Any], job: Any) -> dict[str, Any]:
        patched = copy.deepcopy(api)
        asset_slots = list(getattr(job, "asset_slots", []) or [])
        for slot in asset_slots:
            if not isinstance(slot, dict):
                continue
            node_id = str(slot.get("node_id") or "").strip()
            input_name = str(slot.get("input_name") or "file_name").strip() or "file_name"
            local_path = str(slot.get("source_path") or slot.get("path") or "").strip()
            if not node_id:
                continue
            if node_id not in patched:
                continue
            node = patched.get(node_id)
            if not isinstance(node, dict):
                continue
            inputs = node.setdefault("inputs", {})
            local_path = local_path or str(inputs.get(input_name) or "").strip()
            if not local_path:
                continue
            file_name = self._upload_or_reuse(local_path)
            # Existing Licon workflow exports used ``file_name`` locally but
            # RunningHub's upload patch expects a LoadImage ``image`` field.
            # Keep that contract untouched; new model adapters opt into the
            # generic shape by declaring an explicit class_type/input_name.
            if not str(slot.get("class_type") or "").strip():
                node["class_type"] = "LoadImage"
                inputs.clear()
                inputs["image"] = file_name
                inputs["upload"] = "image"
                continue
            class_type = str(slot.get("class_type") or node.get("class_type") or "LoadImage").strip()
            if class_type:
                node["class_type"] = class_type
            inputs[input_name] = file_name
            upload_input = str(slot.get("upload_input") or "").strip()
            upload_value = slot.get("upload_value")
            if upload_input and upload_value not in (None, ""):
                inputs[upload_input] = upload_value
        return patched

    def _output_url(self, output: dict[str, Any]) -> str:
        return str(output.get("fileUrl") or output.get("file_url") or output.get("url") or "").strip()

    def _download_first_video(self, outputs: list[dict[str, Any]], part_id: str, task_id: str) -> tuple[str, list[str]]:
        urls = [self._output_url(item) for item in outputs if self._output_url(item)]
        video_url = ""
        for url in urls:
            suffix = Path(urlparse(url).path).suffix.lower()
            if not suffix or suffix in VIDEO_SUFFIXES:
                video_url = url
                break
        if not video_url:
            return "", urls

        suffix = Path(urlparse(video_url).path).suffix
        if not suffix or suffix.lower() not in VIDEO_SUFFIXES:
            suffix = ".mp4"
        videos_dir = get_project_dir() / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        safe_part_id = _filename_token(part_id, "part")
        safe_task_id = _filename_token(task_id, "task")
        dest = videos_dir / f"{safe_part_id}_{self.name}_{safe_task_id}{suffix}"
        downloaded = self.client.download_file(video_url, dest)
        return str(downloaded), urls

    def submit(
        self,
        api: dict[str, Any],
        job: Any,
        workflow_path: str | None = None,
        wait: bool = True,
    ) -> SubmitResult:
        if not self.client.workflow_id:
            raise ValueError(f"RunningHub workflow_id is required for model deployment: {self.model_id or 'default'}")
        submit_api = self._patch_uploaded_media(api, job)
        log("[LICON_MSR][runninghub] create task", "STEP")
        log("[LICON_MSR][runninghub] use workflowId + workflow payload", "STEP")
        task_id = self.client.create_task_with_workflow(submit_api)
        log(f"[runninghub] accepted {getattr(job, 'part_id', '')}: task_id={task_id}", "STEP")
        status: dict[str, Any] = {}
        outputs: list[dict[str, Any]] = []
        video_path = ""
        output_urls: list[str] = []

        if wait:
            try:
                status = self.client.wait_for_task(task_id)
                outputs = self.client.get_task_outputs(task_id)
                if not outputs and isinstance(status.get("outputs"), list):
                    outputs = [item for item in status["outputs"] if isinstance(item, dict)]
                video_path, output_urls = self._download_first_video(
                    outputs,
                    getattr(job, "part_id", "") or "part",
                    task_id,
                )
                if video_path:
                    log(f"[LICON_MSR][runninghub] downloaded video: {video_path}", "STEP")
                else:
                    log("[LICON_MSR][runninghub][warn] task completed but no downloadable video URL was found", "WARN")
            except Exception as exc:
                status = {"sync_pending": True, "error": str(exc)}
                log(f"[LICON_MSR][runninghub][warn] task created but wait failed; will sync later: {task_id} / {exc}", "WARN")

        return SubmitResult(
            backend=self.name,
            submitted=True,
            task_id=task_id,
            video_path=video_path,
            output_urls=output_urls,
            submit_result={"task_id": task_id, "status": status, "outputs": outputs},
            raw={"workflow_path": workflow_path or "", "status": status, "outputs": outputs},
        )
