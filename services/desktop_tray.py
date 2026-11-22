"""Keep the Windows desktop server alive while its window is in the tray."""

import logging


def register_windows_tray(window):
    def install():
        from webview.platforms.winforms import WinForms

        attach_tray(window.native, WinForms)

    window.events.before_show += install


def attach_tray(form, winforms):
    tray = winforms.NotifyIcon()
    tray.Icon = form.Icon
    tray.Text = form.Text
    menu = winforms.ContextMenuStrip()
    open_item = winforms.ToolStripMenuItem("打开主窗口")
    exit_item = winforms.ToolStripMenuItem("退出程序")
    menu.Items.Add(open_item)
    menu.Items.Add(winforms.ToolStripSeparator())
    menu.Items.Add(exit_item)
    tray.ContextMenuStrip = menu
    exiting = False

    def restore(*_):
        if form.WindowState == winforms.FormWindowState.Minimized:
            form.WindowState = winforms.FormWindowState.Normal
        form.Show()
        form.Activate()
        tray.Visible = False

    def tray_click(_sender, event):
        if event.Button == winforms.MouseButtons.Left:
            restore()

    def exit_application(*_):
        nonlocal exiting
        exiting = True
        form.Close()

    def move_to_tray(_sender, event):
        if exiting or event.Cancel or event.CloseReason != winforms.CloseReason.UserClosing:
            return
        try:
            tray.Visible = True
            form.Hide()
        except Exception as error:
            tray.Visible = False
            logging.getLogger(__name__).warning("Could not move desktop window to the tray: %s", error)
            return
        event.Cancel = True

    def dispose(*_):
        tray.Visible = False
        tray.Dispose()
        menu.Dispose()

    open_item.Click += restore
    exit_item.Click += exit_application
    tray.MouseClick += tray_click
    form.FormClosing += move_to_tray
    form.FormClosed += dispose
    return tray
