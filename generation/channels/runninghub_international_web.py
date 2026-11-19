"""RunningHub international H3 web video channel."""
from generation.channels.runninghub_web import RunningHubWebChannel


class RunningHubInternationalWebChannel(RunningHubWebChannel):
    id = "runninghub_international_web"
    label = "RunningHub 国际网页版（H3）"
    history_label = "RunningHub 国际网页视频"


channel = RunningHubInternationalWebChannel()
