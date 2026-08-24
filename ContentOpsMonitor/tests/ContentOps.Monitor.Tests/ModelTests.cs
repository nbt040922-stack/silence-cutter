using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Tests;

public sealed class ModelTests
{
    [Fact]
    public void DefaultConfigurationUsesTheFiveContentOpsPorts()
    {
        var config = MonitorConfig.CreateDefault();

        Assert.Equal(8787, config.Endpoints["YT_NOTIFI"].Port);
        Assert.Equal(8790, config.Endpoints["YTDOWNLOAD"].Port);
        Assert.Equal(8791, config.Endpoints["Silence Scheduler"].Port);
        Assert.Equal(8792, config.Endpoints["Qwen"].Port);
        Assert.Equal(8780, config.Endpoints["Manual LAN API"].Port);
    }

    [Fact]
    public void DefaultConfigurationUsesFiveSecondPollingAndNotifications()
    {
        var config = MonitorConfig.CreateDefault();

        Assert.Equal(TimeSpan.FromSeconds(20), config.PollInterval);
        Assert.True(config.NotificationsEnabled);
    }

    [Fact]
    public void ServiceStatesContainTheRequiredOperationalValues()
    {
        Assert.Equal(new[] { "READY", "STARTING", "DEGRADED", "DOWN", "UNKNOWN" },
            Enum.GetNames<ServiceState>());
    }

    [Fact]
    public void ServiceControlButtonReflectsOperationalState()
    {
        var ready = new ServiceSnapshot("Qwen", 8792, ServiceState.READY);
        var down = ready with { State = ServiceState.DOWN };

        Assert.Equal("\uE71A", ready.ControlGlyph);
        Assert.Equal("Dừng service", ready.ControlTooltip);
        Assert.Equal("\uE768", down.ControlGlyph);
        Assert.Equal("Khởi động service", down.ControlTooltip);
    }
}
