"""Local ComfyUI submit provider."""
from __future__ import annotations

import shutil
import re
from pathlib import Path
from typing import Any

import requests

from services.context import get_project_dir
from services.logger import log
from workflow.comfyui import comfy_url, extract_errors, locate_output_video, wait_for_prompt
from workflow.submit_provider import SubmitResult


class ComfyUISubmitProvider:
    name = "comfyui"

    @staticmethod
    def _filename_token(value: str, fallback: str) -> str:
        token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
        return token or fallback

    def _submit_to_comfyui(self, api: dict[str, Any], url: str | None = None) -> dict[str, Any] | None:
        endpoint = (url or comfy_url()).rstrip("/") + "/prompt"
        log(f"[LICON_MSR][comfyui] submit {endpoint}", "STEP")
        response = requests.post(endpoint, json={"prompt": api}, timeout=60)
        log(f"[LICON_MSR][comfyui] HTTP {response.status_code}", "STEP")
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            log(response.text, "ERROR")
            return None

    def _copy_video_to_project(self, source: Path, part_id: str, prompt_id: str) -> Path:
        videos_dir = get_project_dir() / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix or ".mp4"
        safe_part_id = self._filename_token(part_id, "part")
        safe_prompt_id = self._filename_token(prompt_id, "prompt")
        name = f"{safe_part_id}_{self.name}_{safe_prompt_id}{suffix}"
        dest = videos_dir / name
        shutil.copy2(source, dest)
        return dest.resolve()

    def submit(
        self,
        api: dict[str, Any],
        job: Any,
        workflow_path: str | None = None,
        wait: bool = True,
    ) -> SubmitResult:
        result = self._submit_to_comfyui(api)
        prompt_id = str((result or {}).get("prompt_id") or "").strip()
        video_path = ""
        history = None

        if wait and prompt_id:
            history = wait_for_prompt(prompt_id)
            source_video = locate_output_video(history, filename_prefix=getattr(job, "output_prefix", ""))
            if source_video:
                captured = self._copy_video_to_project(
                    source_video,
                    getattr(job, "part_id", "") or Path(getattr(job, "output_prefix", "")).name,
                    prompt_id,
                )
                video_path = str(captured)
                log(f"[LICON_MSR][comfyui] captured video: {captured}", "STEP")
            else:
                errors = extract_errors(history)
                if errors:
                    raise RuntimeError("; ".join(errors))
                log("[LICON_MSR][comfyui][warn] finished but no video file was found", "WARN")

        return SubmitResult(
            backend=self.name,
            submitted=True,
            prompt_id=prompt_id,
            video_path=video_path,
            submit_result=result or {},
            raw={"workflow_path": workflow_path or "", "history": history or {}},
        )
