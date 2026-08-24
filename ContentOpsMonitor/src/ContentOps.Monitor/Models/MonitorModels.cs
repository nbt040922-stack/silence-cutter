using System.Collections.ObjectModel;
using System.Text.Json.Serialization;

namespace ContentOps.Monitor.Models;

public enum ServiceState
{
    READY,
    STARTING,
    DEGRADED,
    DOWN,
    UNKNOWN
}

public enum AlertSeverity
{
    INFO,
    WARNING,
    ERROR,
    CRITICAL
}

public sealed record ServiceEndpoint(string Name, int Port, string BaseUrl)
{
    public Uri HealthUri => new($"{BaseUrl.TrimEnd('/')}/health");
}

public sealed record ServiceDefinition(
    string Name,
    int Port,
    string BaseUrl,
    bool Critical = true)
{
    public Uri HealthUri => new($"{BaseUrl.TrimEnd('/')}/health");
}

public sealed record OwnedProcessInfo(int ProcessId, string ExecutablePath, string CommandLine);

public sealed record ServiceSnapshot(
    string Name,
    int Port,
    ServiceState State,
    string? Detail = null,
    int? ProcessId = null,
    double? CpuPercent = null,
    long? MemoryBytes = null,
    TimeSpan? Uptime = null,
    DateTimeOffset? CheckedAt = null,
    bool IsManaged = false)
{
    public string StateText => State.ToString();
    public string MemoryText => MemoryBytes is null ? "--" : $"{MemoryBytes.Value / 1024d / 1024d:0.#} MB";
    public string CpuText => CpuPercent is null ? "--" : $"{CpuPercent:0.#}%";
    public string UptimeText => Uptime is null ? "--" : Uptime.Value.TotalHours >= 1
        ? $"{(int)Uptime.Value.TotalHours:00}h {Uptime.Value.Minutes:00}m"
        : $"{Uptime.Value.Minutes:00}m {Uptime.Value.Seconds:00}s";
    public string ControlStatus => IsManaged ? "MANAGED" : "UNMANAGED SERVICE";
    public string ControlGlyph => State is ServiceState.DOWN or ServiceState.UNKNOWN ? "\uE768" : "\uE71A";
    public string ControlTooltip => State is ServiceState.DOWN or ServiceState.UNKNOWN ? "Khởi động service" : "Dừng service";
    public string DashboardName => Name == "Qwen" ? "Qwen (AI Engine)" : Name;
    public string IconGlyph => Name switch
    {
        "Qwen" => "\uE8B4",
        "Silence Scheduler" => "\uE7C5",
        "Manual LAN API" => "\uE774",
        _ => "\uE950"
    };
}

public sealed record MonitorAlert(
    AlertSeverity Severity,
    string Component,
    string Message,
    DateTimeOffset Timestamp,
    bool Resolved = false);

public sealed record JobRecord(
    string Id,
    string? Title,
    string? VideoUrl,
    string? Channel,
    string? Origin,
    string? Status,
    string? Stage,
    double? Progress,
    DateTimeOffset? Created,
    DateTimeOffset? Updated,
    string? Output,
    string? Error,
    string? SourceService,
    string? DisplayName = null,
    string? InputMode = null,
    string? SourcePath = null,
    string? ReportPath = null,
    DateTimeOffset? Started = null,
    DateTimeOffset? Finished = null,
    int? ProcessId = null,
    double? DurationSeconds = null,
    double? EtaSeconds = null,
    string? EtaStatus = null,
    string? SchedulerState = null,
    string? SchedulerFailureDetail = null);

public sealed record ManualVideoMetadata(
    string? Title,
    string? Channel,
    double? DurationSeconds,
    string? Duration,
    string? Thumbnail,
    string? Url);

public sealed record ResolvedChannel(
    [property: JsonPropertyName("channel_id")]
    string? ChannelId,
    [property: JsonPropertyName("canonical_url")]
    string? CanonicalUrl,
    [property: JsonPropertyName("title")]
    string? Title);

public sealed record ChannelRecord(
    string Id,
    string Name,
    bool Enabled,
    string? SubscriptionStatus,
    int? NewVideosCount,
    DateTimeOffset? LastEvent,
    string? LastError,
    bool CutEnabled = false)
{
    public string StatusText => LastError is not null ? "✕ Error" : Enabled ? "✓ Active" : "Ⅱ Paused";
    public string StatusKind => LastError is not null ? "Error" : Enabled ? "Active" : "Paused";
    public string NewVideosText => NewVideosCount?.ToString() ?? "0";
    public string LastEventText
    {
        get
        {
            if (LastEvent is null) return "--";
            var elapsed = DateTimeOffset.Now - LastEvent.Value;
            return elapsed.TotalMinutes < 60 ? $"{Math.Max(1, (int)elapsed.TotalMinutes)} phút trước" : elapsed.TotalHours < 24 ? $"{Math.Max(1, (int)elapsed.TotalHours)} giờ trước" : $"{Math.Max(1, (int)elapsed.TotalDays)} ngày trước";
        }
    }
}

public sealed class MonitorConfig
{
    public string ServiceControlBaseUrl { get; init; } = "http://127.0.0.1:8794";
    public TimeSpan PollInterval { get; init; } = TimeSpan.FromSeconds(20);
    public bool NotificationsEnabled { get; init; } = true;
    public bool MinimizeToTray { get; init; } = true;
    public Dictionary<string, ServiceEndpoint> Endpoints { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public string? ManualLanToken { get; init; }
    public List<string> LogPaths { get; init; } = [];
    public Dictionary<string, string> ManagedExecutablePaths { get; init; } = new(StringComparer.OrdinalIgnoreCase);

    public static MonitorConfig CreateDefault() => new()
    {
        ServiceControlBaseUrl = "http://127.0.0.1:8794",
        Endpoints = new Dictionary<string, ServiceEndpoint>(StringComparer.OrdinalIgnoreCase)
        {
            ["YT_NOTIFI"] = new("YT_NOTIFI", 8787, "http://127.0.0.1:8787"),
            ["YTDOWNLOAD"] = new("YTDOWNLOAD", 8790, "http://127.0.0.1:8790"),
            ["Silence Scheduler"] = new("Silence Scheduler", 8791, "http://127.0.0.1:8791"),
            ["Qwen"] = new("Qwen", 8792, "http://127.0.0.1:8792"),
            ["Manual LAN API"] = new("Manual LAN API", 8780, "http://127.0.0.1:8780")
        }
    };
}
