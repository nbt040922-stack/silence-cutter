using System.Windows.Forms;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Services.Notifications;

public sealed class WindowsNotificationService(NotifyIcon icon)
{
    private bool _enabled = true;
    public void SetEnabled(bool enabled) => _enabled = enabled;

    public void NotifyTransition(MonitorAlert alert, int port)
    {
        if (!_enabled) return;
        icon.BalloonTipTitle = alert.Resolved ? "ContentOps Recovered" : "ContentOps Alert";
        icon.BalloonTipText = $"{alert.Component} :{port} {(alert.Resolved ? "READY" : "DOWN")}";
        icon.ShowBalloonTip(4000);
    }
}
