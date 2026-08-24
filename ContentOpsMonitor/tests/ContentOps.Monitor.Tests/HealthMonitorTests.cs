using ContentOps.Monitor.Models;
using ContentOps.Monitor.Services.Health;

namespace ContentOps.Monitor.Tests;

public sealed class HealthMonitorTests
{
    [Fact]
    public async Task FirstPollPublishesServiceSnapshotsWithoutAStartupAlert()
    {
        var service = new ServiceDefinition("Qwen", 8792, "http://127.0.0.1:8792");
        var alerts = new List<MonitorAlert>();
        await using var monitor = new HealthMonitor([service], (_, _) => Task.FromResult(
            new ServiceSnapshot("Qwen", 8792, ServiceState.READY)));
        monitor.AlertRaised += alerts.Add;

        await monitor.PollOnceAsync();

        Assert.Equal(ServiceState.READY, monitor.Snapshots.Single().State);
        Assert.Empty(alerts);
    }

    [Fact]
    public async Task ReadyToDownAndDownToReadyEmitOneTransitionAlertEach()
    {
        var service = new ServiceDefinition("Qwen", 8792, "http://127.0.0.1:8792");
        var state = ServiceState.READY;
        var alerts = new List<MonitorAlert>();
        await using var monitor = new HealthMonitor([service], (_, _) => Task.FromResult(
            new ServiceSnapshot("Qwen", 8792, state)));
        monitor.AlertRaised += alerts.Add;

        await monitor.PollOnceAsync();
        state = ServiceState.DOWN;
        await monitor.PollOnceAsync();
        await monitor.PollOnceAsync();
        state = ServiceState.READY;
        await monitor.PollOnceAsync();

        Assert.Equal(2, alerts.Count);
        Assert.Equal("READY → DOWN", alerts[0].Message);
        Assert.Equal("DOWN → READY", alerts[1].Message);
    }

    [Fact]
    public async Task OneServiceFailureDoesNotPreventOtherServiceSnapshot()
    {
        var definitions = new[]
        {
            new ServiceDefinition("Qwen", 8792, "http://127.0.0.1:8792"),
            new ServiceDefinition("Manual LAN API", 8780, "http://127.0.0.1:8780")
        };
        await using var monitor = new HealthMonitor(definitions, (service, _) => Task.FromResult(
            new ServiceSnapshot(service.Name, service.Port, service.Name == "Qwen" ? ServiceState.DOWN : ServiceState.READY)));

        await monitor.PollOnceAsync();

        Assert.Equal(ServiceState.DOWN, monitor.Snapshots.Single(s => s.Name == "Qwen").State);
        Assert.Equal(ServiceState.READY, monitor.Snapshots.Single(s => s.Name == "Manual LAN API").State);
    }
}
