using System.Text.Json;
using System.IO;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Configuration;

public sealed class MonitorConfigStore
{
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly string _filePath;

    public MonitorConfigStore(string? filePath = null)
    {
        _filePath = filePath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "ContentOps", "Monitor", "monitor.json");
    }

    public async Task<MonitorConfig> LoadAsync(CancellationToken cancellationToken = default)
    {
        if (!File.Exists(_filePath)) return MonitorConfig.CreateDefault();
        try
        {
            await using var stream = File.OpenRead(_filePath);
            var config = await JsonSerializer.DeserializeAsync<MonitorConfig>(stream, Options, cancellationToken);
            return config?.Endpoints.Count > 0 ? config : MonitorConfig.CreateDefault();
        }
        catch (JsonException) { return MonitorConfig.CreateDefault(); }
        catch (IOException) { return MonitorConfig.CreateDefault(); }
    }

    public async Task SaveAsync(MonitorConfig config, CancellationToken cancellationToken = default)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_filePath)!);
        await using var stream = File.Create(_filePath);
        await JsonSerializer.SerializeAsync(stream, config, Options, cancellationToken);
    }
}
