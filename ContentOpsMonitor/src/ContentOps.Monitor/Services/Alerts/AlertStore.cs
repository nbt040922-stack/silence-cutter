using System.Text.Json;
using System.IO;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Services.Alerts;

public sealed class AlertStore(string filePath)
{
    private const int MaxAlerts = 500;
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly SemaphoreSlim _gate = new(1, 1);

    public async Task<IReadOnlyList<MonitorAlert>> LoadAsync(CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            if (!File.Exists(filePath)) return [];
            await using var stream = File.OpenRead(filePath);
            return await JsonSerializer.DeserializeAsync<List<MonitorAlert>>(stream, Options, cancellationToken) ?? [];
        }
        catch (JsonException) { return []; }
        catch (IOException) { return []; }
        finally { _gate.Release(); }
    }

    public async Task AppendAsync(MonitorAlert alert, CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var alerts = new List<MonitorAlert>();
            if (File.Exists(filePath))
            {
                try
                {
                    await using var read = File.OpenRead(filePath);
                    alerts = await JsonSerializer.DeserializeAsync<List<MonitorAlert>>(read, Options, cancellationToken) ?? [];
                }
                catch (JsonException) { }
                catch (IOException) { }
            }
            alerts.Add(alert);
            var recent = alerts.OrderByDescending(item => item.Timestamp).Take(MaxAlerts).Reverse().ToArray();
            Directory.CreateDirectory(Path.GetDirectoryName(filePath)!);
            await using var write = File.Create(filePath);
            await JsonSerializer.SerializeAsync(write, recent, Options, cancellationToken);
        }
        finally { _gate.Release(); }
    }
}
