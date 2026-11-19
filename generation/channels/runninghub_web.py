"""Runow H3 web channel, independent of RunningHub workflow submission."""
from services import runow_web


class RunningHubWebChannel:
    id = "runninghub_web"
    label = "RunningHub 网页版（H3）"
    uses_workflow = False
    daily_limit = None
    history_label = "RunningHub 网页视频"
    history_cost_label = "网站结算"

    def unavailable_reason(self, segment, payload):
        from generation.job_schema import resolved_model_id

        if resolved_model_id(segment, payload) != runow_web.MODEL_ID:
            return "RunningHub 网页渠道仅支持 H3 多模态参考分段"
        try:
            runow_web.build_request(segment)
        except (ValueError, TypeError, OSError) as exc:
            return str(exc)
        return ""

    def submit(self, service, segment_index, *, wait=False):
        from services.web_video_bridge import enqueue_segment

        return enqueue_segment(segment_index, self)

    build_request = staticmethod(runow_web.build_request)
    public_input = staticmethod(runow_web.public_input)
    resolve_asset = staticmethod(runow_web.resolve_asset)


channel = RunningHubWebChannel()
