using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Services.Health;

public sealed class HealthMonitor : IAsyncDisposable
{
    private readonly IReadOnlyList<ServiceDefinition> _services;
    private readonly Func<ServiceDefinition, CancellationToken, Task<ServiceSnapshot>> _probe;
    private readonly TimeSpan _pollInterval;
    private readonly Dictionary<string, ServiceSnapshot> _previous = new(StringComparer.OrdinalIgnoreCase);
    private CancellationTokenSource? _stopSource;
    private Task? _loop;

    public HealthMonitor(
        IReadOnlyList<ServiceDefinition> services,
        Func<ServiceDefinition, CancellationToken, Task<ServiceSnapshot>> probe,
        TimeSpan? pollInterval = null)
    {
        _services = services;
        _probe = probe;
        _pollInterval = pollInterval ?? TimeSpan.FromSeconds(20);
        Snapshots = [];
    }

    public IReadOnlyList<ServiceSnapshot> Snapshots { get; private set; }
    public event Action<IReadOnlyList<ServiceSnapshot>>? SnapshotChanged;
    public event Action<MonitorAlert>? AlertRaised;

    public Task StartAsync()
    {
        if (_loop is not null) return _loop;
        _stopSource = new CancellationTokenSource();
        _loop = PollLoopAsync(_stopSource.Token);
        return Task.CompletedTask;
    }

    public async Task StopAsync()
    {
        if (_stopSource is null || _loop is null) return;
        _stopSource.Cancel();
        try { await _loop; } catch (OperationCanceledException) { }
        _loop = null;
        _stopSource.Dispose();
        _stopSource = null;
    }

    public async Task PollOnceAsync(CancellationToken cancellationToken = default)
    {
        var results = await Task.WhenAll(_services.Select(async service =>
        {
            try { return await _probe(service, cancellationToken); }
            catch (Exception ex) { return new ServiceSnapshot(service.Name, service.Port, ServiceState.DOWN, ex.Message, CheckedAt: DateTimeOffset.Now); }
        }));

        foreach (var current in results)
        {
            if (_previous.TryGetValue(current.Name, out var previous) && previous.State != current.State)
                EmitTransition(previous, current);
            _previous[current.Name] = current;
        }

        Snapshots = results.OrderBy(snapshot => snapshot.Name).ToArray();
        SnapshotChanged?.Invoke(Snapshots);
    }

    private async Task PollLoopAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            await PollOnceAsync(cancellationToken);
            await Task.Delay(_pollInterval, cancellationToken);
        }
    }

    private void EmitTransition(ServiceSnapshot previous, ServiceSnapshot current)
    {
        var recovered = previous.State == ServiceState.DOWN && current.State == ServiceState.READY;
        var failed = previous.State == ServiceState.READY && current.State == ServiceState.DOWN;
        if (!failed && !recovered) return;
        AlertRaised?.Invoke(new(
            recovered ? AlertSeverity.INFO : AlertSeverity.CRITICAL,
            current.Name,
            $"{previous.State} → {current.State}",
            DateTimeOffset.Now,
            recovered));
    }

    public async ValueTask DisposeAsync() => await StopAsync();
}
