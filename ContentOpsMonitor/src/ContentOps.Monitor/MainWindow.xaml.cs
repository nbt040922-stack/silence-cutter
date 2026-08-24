using System.ComponentModel;
using System.Diagnostics;
using System.Windows;
using ContentOps.Monitor.Services.Notifications;
using ContentOps.Monitor.Services.Tray;
using ContentOps.Monitor.ViewModels;

namespace ContentOps.Monitor;

public partial class MainWindow : Window
{
    private readonly MainViewModel _viewModel = new();
    private readonly TrayService _tray = new();
    private readonly WindowsNotificationService _notifications;
    private bool _allowClose;

    public MainWindow()
    {
        InitializeComponent();
        DataContext = _viewModel;
        _notifications = _tray.Notifications;
        _viewModel.AlertRaised += OnAlertRaised;
        _tray.OpenRequested += () => { Show(); WindowState = WindowState.Normal; Activate(); };
        _tray.ExitRequested += () => { _allowClose = true; Close(); };
        _tray.PauseNotificationsRequested += () => _notifications.SetEnabled(false);
        StateChanged += (_, _) => { if (WindowState == WindowState.Minimized) Hide(); };
        Loaded += async (_, _) => await _viewModel.InitializeAsync();
        Closing += OnClosing;
    }

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (!_allowClose && _viewModel.Config.MinimizeToTray)
        {
            e.Cancel = true;
            Hide();
            return;
        }
        Closing -= OnClosing;
        await _viewModel.DisposeAsync();
        _tray.Dispose();
    }

    private void OnAlertRaised(ContentOps.Monitor.Models.MonitorAlert alert)
    {
        var port = _viewModel.Services.FirstOrDefault(service => service.Name == alert.Component)?.Port ?? 0;
        _notifications.NotifyTransition(alert, port);
    }

    private static void OpenUrl(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
}
