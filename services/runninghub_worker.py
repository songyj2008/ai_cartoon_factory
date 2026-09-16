"""Server-owned RunningHub polling; never depends on browser timer events."""
from __future__ import annotations

import atexit
import threading

from services.context import get_current_episode_name, get_project_dir, pin_runtime
from services.logger import log


class RunningHubWorker:
    def __init__(self, interval=5.0):
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="runninghub-queue-worker", daemon=True
            )
            self._thread.start()
            log("[runninghub][worker] 后台队列已启动，轮询间隔 5 秒", "STEP")

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def tick(self):
        from workflow.runninghub_sync import sync_runninghub_video_jobs
        from workflow.video_merge import finish_requested_video_merge

        with pin_runtime():
            if get_current_episode_name() or not (get_project_dir() / "video_jobs.json").is_file():
                return
            sync_runninghub_video_jobs()
            finish_requested_video_merge()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                log(f"[runninghub][worker][warn] 本轮同步失败，下轮重试: {exc}", "WARN")
            self._stop.wait(self.interval)


runninghub_worker = RunningHubWorker()
atexit.register(runninghub_worker.stop)
