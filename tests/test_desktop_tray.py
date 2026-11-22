from types import SimpleNamespace

from services.desktop_tray import attach_tray


class Event:
    handler = None

    def __iadd__(self, handler):
        self.handler = handler
        return self

    def fire(self, *args):
        self.handler(*args)


class Tray:
    def __init__(self):
        self.MouseClick = Event()
        self.Visible = False
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class Menu:
    def __init__(self):
        self.items = []
        self.Items = SimpleNamespace(Add=self.items.append)
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class Form:
    def __init__(self):
        self.FormClosing = Event()
        self.FormClosed = Event()
        self.Icon = object()
        self.Text = "AI Cartoon Factory"
        self.WindowState = "Maximized"
        self.visible = True
        self.activated = False
        self.closed = False

    def Hide(self):
        self.visible = False

    def Show(self):
        self.visible = True

    def Activate(self):
        self.activated = True

    def Close(self, reason="user"):
        event = SimpleNamespace(Cancel=False, CloseReason=reason)
        self.FormClosing.fire(self, event)
        if not event.Cancel:
            self.closed = True
            self.visible = False
            self.FormClosed.fire(self, event)
        return event


def attach_test_tray(form):
    winforms = SimpleNamespace(
        NotifyIcon=Tray,
        ContextMenuStrip=Menu,
        ToolStripMenuItem=lambda label: SimpleNamespace(Text=label, Click=Event()),
        ToolStripSeparator=object,
        MouseButtons=SimpleNamespace(Left="left"),
        CloseReason=SimpleNamespace(UserClosing="user"),
        FormWindowState=SimpleNamespace(Minimized="Minimized", Normal="Normal"),
    )
    return attach_tray(form, winforms)


def test_close_hides_window_until_tray_is_clicked():
    form = Form()
    tray = attach_test_tray(form)
    assert tray.Icon is form.Icon
    assert not tray.Visible
    assert form.Close().Cancel
    assert not form.visible and not form.closed and tray.Visible

    tray.MouseClick.fire(tray, SimpleNamespace(Button="right"))
    assert not form.visible
    tray.MouseClick.fire(tray, SimpleNamespace(Button="left"))
    assert form.visible and form.activated and not tray.Visible
    assert form.WindowState == "Maximized"


def test_tray_exit_and_windows_shutdown_close_and_dispose():
    form = Form()
    tray = attach_test_tray(form)
    form.Close()
    tray.ContextMenuStrip.items[-1].Click.fire()
    assert form.closed and tray.disposed and tray.ContextMenuStrip.disposed
    assert not tray.Visible

    shutdown_form = Form()
    shutdown_tray = attach_test_tray(shutdown_form)
    assert not shutdown_form.Close("shutdown").Cancel
    assert shutdown_form.closed and shutdown_tray.disposed
