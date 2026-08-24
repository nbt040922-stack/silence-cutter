using System.Drawing;
using System.Windows.Forms;
using ContentOps.Monitor.Models;
using ContentOps.Monitor.Services.Notifications;

namespace ContentOps.Monitor.Services.Tray;

public sealed class TrayService : IDisposable
{
    private readonly NotifyIcon _icon;
    public event Action? OpenRequested;
    public event Action? ExitRequested;
    public event Action? PauseNotificationsRequested;

    public TrayService()
    {
        _icon = new NotifyIcon { Icon = SystemIcons.Application, Visible = true, Text = "ContentOps Monitor" };
        var menu = new ContextMenuStrip();
        menu.Items.Add("Open ContentOps Monitor", null, (_, _) => OpenRequested?.Invoke());
        menu.Items.Add("Services status", null, (_, _) => OpenRequested?.Invoke());
        menu.Items.Add("Pause notifications", null, (_, _) => PauseNotificationsRequested?.Invoke());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Exit Monitor", null, (_, _) => ExitRequested?.Invoke());
        _icon.ContextMenuStrip = menu;
        _icon.DoubleClick += (_, _) => OpenRequested?.Invoke();
    }

    public WindowsNotificationService Notifications => new(_icon);

    public void SetStatus(IReadOnlyList<ServiceSnapshot> services)
    {
        var down = services.Any(service => service.State == ServiceState.DOWN);
        var degraded = services.Any(service => service.State == ServiceState.DEGRADED);
        _icon.Text = down ? "ContentOps Monitor · DOWN" : degraded ? "ContentOps Monitor · Degraded" : "ContentOps Monitor · Ready";
    }

    public void Dispose() { _icon.Visible = false; _icon.Dispose(); }
}
