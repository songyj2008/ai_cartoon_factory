"""RunningHub Task API client."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


class RunningHubQueueFullError(RuntimeError):
    """The remote account has no free task slot; safe to retry later."""


class RunningHubClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        workflow_id: str = "",
        poll_seconds: float = 5.0,
        timeout_seconds: int = 7200,
        workflow_payload_mode: str = "string",
        instance_type: str = "",
    ) -> None:
        self.base_url = str(base_url or "https://www.runninghub.cn").rstrip("/") + "/"
        self.api_key = str(api_key or "").strip()
        self.workflow_id = str(workflow_id or "").strip()
        self.poll_seconds = float(poll_seconds or 5.0)
        self.timeout_seconds = int(timeout_seconds or 7200)
        self.workflow_payload_mode = str(workflow_payload_mode or "string").strip().lower()
        self.instance_type = str(instance_type or "").strip()
        if not self.api_key:
            raise ValueError("RunningHub api_key is required")

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if extra:
            headers.update(extra)
        return headers

    def upload_media(self, local_path: str | Path) -> str:
        path = Path(local_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"RunningHub upload file does not exist: {path}")
        with open(path, "rb") as file:
            response = requests.post(
                self._url("/openapi/v2/media/upload/binary"),
                headers=self._auth_headers(),
                data={"apiKey": self.api_key},
                files={"file": (path.name, file)},
                timeout=120,
            )
        response.raise_for_status()
        data = response.json()
        file_name = _find_first(data, ("fileName", "file_name", "name"))
        if not file_name:
            raise RuntimeError(f"RunningHub upload response missing fileName: {data}")
        return file_name

    def create_task_with_workflow(self, workflow: dict[str, Any]) -> str:
        if not self.workflow_id:
            raise ValueError("RunningHub workflow_id is required when submitting workflow payload")
        workflow_payload: str | dict[str, Any]
        if self.workflow_payload_mode == "object":
            workflow_payload = workflow
        else:
            workflow_payload = json.dumps(workflow, ensure_ascii=False)
        payload: dict[str, Any] = {
            "apiKey": self.api_key,
            "workflowId": self.workflow_id,
            "workflow": workflow_payload,
        }
        if self.instance_type:
            payload["instanceType"] = self.instance_type
        response = requests.post(
            self._url("/task/openapi/create"),
            json=payload,
            headers=self._auth_headers({"Content-Type": "application/json"}),
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        task_id = _find_first(data, ("taskId", "task_id", "id"))
        if not task_id:
            code = str(data.get("code") or "") if isinstance(data, dict) else ""
            message = str(data.get("msg") or "") if isinstance(data, dict) else ""
            if code == "421" or "TASK_QUEUE_MAXED" in message.upper():
                raise RunningHubQueueFullError(f"RunningHub queue is full: {data}")
            raise RuntimeError(f"RunningHub create task response missing taskId: {data}")
        return task_id

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        payload = {"apiKey": self.api_key, "taskId": str(task_id or "").strip()}
        response = requests.post(
            self._url("/task/openapi/status"),
            json=payload,
            headers=self._auth_headers({"Content-Type": "application/json"}),
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}

    def wait_for_task(self, task_id: str) -> dict[str, Any]:
        started = time.time()
        while True:
            if time.time() - started > self.timeout_seconds:
                raise TimeoutError(f"RunningHub task timed out: {task_id}")
            status = self.get_task_status(task_id)
            status_text = str(_find_first(status, ("status", "taskStatus", "state", "data")) or "").lower()
            if status_text in {"success", "succeeded", "finished", "completed", "complete", "done"}:
                return status
            if status_text in {"failed", "failure", "error", "canceled", "cancelled"}:
                raise RuntimeError(f"RunningHub task failed: {status}")
            outputs = self.get_task_outputs(task_id)
            if outputs:
                return {"status": status, "outputs": outputs}
            time.sleep(self.poll_seconds)

    def get_task_outputs(self, task_id: str) -> list[dict[str, Any]]:
        payload = {"apiKey": self.api_key, "taskId": str(task_id or "").strip()}
        response = requests.post(
            self._url("/task/openapi/outputs"),
            json=payload,
            headers=self._auth_headers({"Content-Type": "application/json"}),
            timeout=60,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        outputs = _find_outputs(data)
        return outputs

    def download_file(self, file_url: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(str(file_url), stream=True, timeout=300) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
        return dest_path.resolve()


def _find_first(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for key in keys:
            if value.get(key):
                return str(value[key]).strip()
        for child in value.values():
            found = _find_first(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first(item, keys)
            if found:
                return found
    return ""


def _find_outputs(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            url = item.get("fileUrl") or item.get("file_url") or item.get("url")
            # Failed/expired output records can still carry billing metadata
            # without a downloadable file URL.  Keep those records so the
            # segment billing history remains complete.
            has_usage = any(
                key in item
                for key in (
                    "taskCostTime",
                    "task_cost_time",
                    "consumeCoins",
                    "consume_coins",
                    "consumeMoney",
                    "thirdPartyConsumeMoney",
                )
            )
            if url or has_usage:
                found.append(item)
            for key in ("outputs", "data", "result", "files"):
                child = item.get(key)
                if child is not None:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found
