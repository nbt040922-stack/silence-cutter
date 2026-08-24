using System.Globalization;
using System.IO;
using System.Text.Json;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Services.Api;

public sealed class YtNotifiAdapter(ApiClient client, Uri baseUri)
{
    public Task<ApiResult<Dictionary<string, object>>> GetHealthAsync(CancellationToken ct = default) =>
        client.GetJsonAsync<Dictionary<string, object>>(new(baseUri, "/health"), ct);

    public async Task<ApiResult<IReadOnlyList<JobRecord>>> GetJobsAsync(CancellationToken ct = default)
    {
        var result = await client.GetJsonAsync<JsonElement[]>(new(baseUri, "/api/jobs"), ct);
        return result.Success
            ? new(true, result.Value?.Select(json => JobMapper.Map(json, "AUTO", "YT_NOTIFI")).ToArray() ?? [], result.StatusCode)
            : ApiResult<IReadOnlyList<JobRecord>>.Failed(result.Error ?? "Unable to load jobs", result.StatusCode);
    }

    public async Task<ApiResult<IReadOnlyList<ChannelRecord>>> GetChannelsAsync(CancellationToken ct = default)
    {
        var result = await client.GetJsonAsync<JsonElement[]>(new(baseUri, "/api/channels"), ct);
        return result.Success
            ? new(true, result.Value?.Select(ChannelMapper.Map).ToArray() ?? [], result.StatusCode)
            : ApiResult<IReadOnlyList<ChannelRecord>>.Failed(result.Error ?? "Unable to load channels", result.StatusCode);
    }

    public Task<ApiResult<JsonElement>> SetChannelEnabledAsync(string channelId, bool enabled, CancellationToken ct = default) =>
        client.PatchJsonAsync<JsonElement>(new(baseUri, $"/api/channels/{Uri.EscapeDataString(channelId)}"), new { enabled }, ct);

    public Task<ApiResult<JsonElement>> SetChannelCutEnabledAsync(string channelId, bool enabled, CancellationToken ct = default) =>
        client.PatchJsonAsync<JsonElement>(new(baseUri, $"/api/channels/{Uri.EscapeDataString(channelId)}"), new { cut_enabled = enabled }, ct);

    public Task<ApiResult<JsonElement>> CancelJobAsync(string jobId, CancellationToken ct = default) =>
        client.PostJsonAsync<JsonElement>(new(baseUri, $"/api/jobs/{Uri.EscapeDataString(jobId)}/cancel"), new { }, cancellationToken: ct);

    public Task<ApiResult<JsonElement>> RetryJobAsync(string jobId, CancellationToken ct = default) =>
        client.PostJsonAsync<JsonElement>(new(baseUri, $"/api/jobs/{Uri.EscapeDataString(jobId)}/retry"), new { }, cancellationToken: ct);

    public Task<ApiResult<JsonElement>> DeleteJobAsync(string jobId, CancellationToken ct = default) =>
        client.DeleteJsonAsync<JsonElement>(new(baseUri, $"/api/jobs/{Uri.EscapeDataString(jobId)}"), ct);
}

public sealed class ManualLanAdapter(ApiClient client, Uri baseUri)
{
    public Task<ApiResult<Dictionary<string, object>>> GetHealthAsync(CancellationToken ct = default) =>
        client.GetJsonAsync<Dictionary<string, object>>(new(baseUri, "/health"), ct);

    public async Task<ApiResult<IReadOnlyList<JobRecord>>> GetJobsAsync(string? token, CancellationToken ct = default)
    {
        var result = await client.GetJsonAsync<JsonElement>(new(baseUri, "/jobs"), ct);
        if (!result.Success) return ApiResult<IReadOnlyList<JobRecord>>.Failed(result.Error ?? "Unable to load manual jobs", result.StatusCode);
        var jobs = result.Value!.ValueKind == JsonValueKind.Object && result.Value.TryGetProperty("jobs", out var rows)
            ? rows.EnumerateArray().Select(json => JobMapper.Map(json, "MANUAL", "Manual LAN API")).ToArray()
            : Array.Empty<JobRecord>();
        return new(true, jobs, result.StatusCode);
    }

    public Task<ApiResult<JsonElement>> CreateJobAsync(string url, CancellationToken ct = default) =>
        client.PostJsonAsync<JsonElement>(new(baseUri, "/jobs"), new { url }, cancellationToken: ct);

    public Task<ApiResult<ManualVideoMetadata>> GetMetadataAsync(string url, CancellationToken ct = default) =>
        client.GetJsonAsync<ManualVideoMetadata>(new(baseUri, $"/metadata?url={Uri.EscapeDataString(url)}"), ct);

    public Task<ApiResult<JsonElement>> CancelJobAsync(string jobId, CancellationToken ct = default) =>
        client.PostJsonAsync<JsonElement>(new(baseUri, $"/jobs/{Uri.EscapeDataString(jobId)}/cancel"), new { }, cancellationToken: ct);

    public Task<ApiResult<JsonElement>> RetryJobAsync(string jobId, CancellationToken ct = default) =>
        client.PostJsonAsync<JsonElement>(new(baseUri, $"/jobs/{Uri.EscapeDataString(jobId)}/retry"), new { }, cancellationToken: ct);

    public Task<ApiResult<JsonElement>> DeleteJobAsync(string jobId, CancellationToken ct = default) =>
        client.DeleteJsonAsync<JsonElement>(new(baseUri, $"/jobs/{Uri.EscapeDataString(jobId)}"), ct);
}

public static class ServiceAdapters
{
    public static async Task<ServiceSnapshot> GetHealthAsync(ApiClient client, ServiceDefinition service, CancellationToken ct = default)
    {
        var result = await client.GetJsonAsync<JsonElement>(service.HealthUri, ct);
        if (!result.Success)
            return new(service.Name, service.Port, ServiceState.DOWN, result.Error, CheckedAt: DateTimeOffset.Now);
        var json = result.Value!;
        var status = GetString(json, "status")?.ToUpperInvariant();
        var ready = status is "OK" or "READY";
        var degraded = status is "NOT_READY" or "WRONG_RUNTIME" or "ERROR" or "DEGRADED";
        if (service.Name.Equals("Silence Scheduler", StringComparison.OrdinalIgnoreCase) && GetBool(json, "qwen_health") == false)
            degraded = true;
        return new(service.Name, service.Port, ready ? ServiceState.READY : degraded ? ServiceState.DEGRADED : ServiceState.UNKNOWN,
            string.IsNullOrWhiteSpace(status) ? null : status, GetInt(json, "bridge_pid"), CheckedAt: DateTimeOffset.Now);
    }

    private static string? GetString(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object && json.TryGetProperty(name, out var value) ? value.ToString() : null;
    private static bool? GetBool(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object && json.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.True or JsonValueKind.False ? value.GetBoolean() : null;
    private static int? GetInt(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object && json.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : null;
}

internal static class JobMapper
{
    public static JobRecord Map(JsonElement json, string? defaultOrigin = null, string? sourceService = null)
    {
        var status = Get(json, "status");
        var schedulerFailure = Get(json, "scheduler_failure_detail");
        var sourcePath = Get(json, "source_path");
        var title = Get(json, "video_title") ?? Get(json, "display_name") ?? Get(json, "title");
        if (IsGenericYoutubeTitle(title) && !string.IsNullOrWhiteSpace(sourcePath))
            title = Path.GetFileNameWithoutExtension(sourcePath);
        var displayName = Get(json, "display_name") ?? title;
        if (IsGenericYoutubeTitle(displayName) && !string.IsNullOrWhiteSpace(sourcePath))
            displayName = title;
        return new(
            Get(json, "id") ?? Get(json, "job_id") ?? "--",
            title,
            Get(json, "video_url") ?? Get(json, "url"),
            Get(json, "channel_name") ?? Get(json, "channel"),
            Get(json, "origin") ?? Get(json, "source") ?? defaultOrigin,
            status, MapStage(json),
            GetDouble(json, "progress") ?? GetDouble(json, "progress_percent") ?? GetDouble(json, "overall_progress") ?? GetDouble(json, "total_job_progress") ?? GetDouble(json, "process_progress") ?? GetDouble(json, "download_progress"),
            GetDate(json, "created_at"), GetDate(json, "updated_at") ?? GetDate(json, "finished_at"),
            Get(json, "processed_file_path") ?? Get(json, "output_path") ?? Get(json, "output_dir"),
            Get(json, "error") ?? schedulerFailure ?? Get(json, "formatter_error"), sourceService,
            displayName, Get(json, "input_mode"), sourcePath, Get(json, "report_path"),
            GetDate(json, "started_at"), GetDate(json, "finished_at"), GetInt(json, "pid"),
            GetDouble(json, "total_elapsed_seconds"), GetDouble(json, "total_eta_seconds"), Get(json, "eta_status"),
            Get(json, "scheduler_state") ?? status, schedulerFailure);
    }

    private static string? MapStage(JsonElement json)
    {
        var stage = Get(json, "stage") ?? Get(json, "current_stage") ?? Get(json, "process_state") ?? Get(json, "download_state");
        return stage?.ToUpperInvariant() switch
        {
            "DONE" or "COMPLETED" => "Output",
            "ANALYZING" => "Silence",
            "DOWNLOADING" or "DOWNLOAD" => "Download",
            "FAILED" or "ERROR" => "Error",
            "QUEUED" or "WAITING" => "Notify",
            _ => stage
        };
    }

    private static string? Get(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object && json.TryGetProperty(name, out var value) ? value.ToString() : null;
    private static double? GetDouble(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object
        && json.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.Number
        && value.TryGetDouble(out var number) ? number : null;
    private static int? GetInt(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object
        && json.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.Number
        && value.TryGetInt32(out var number) ? number : null;
    private static bool IsGenericYoutubeTitle(string? value) => value is "www.youtube.com" or "youtube.com" or "youtu.be";
    private static DateTimeOffset? GetDate(JsonElement json, string name) => DateTimeOffset.TryParse(Get(json, name), CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var date) ? date : null;
}

internal static class ChannelMapper
{
    public static ChannelRecord Map(JsonElement json) => new(
        Get(json, "channel_id") ?? "--", Get(json, "name") ?? "--",
        GetBool(json, "enabled"), Get(json, "status"), null,
        GetDate(json, "last_success_at") ?? GetDate(json, "last_poll_at"), null,
        GetBool(json, "cut_enabled"));

    private static string? Get(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object && json.TryGetProperty(name, out var value) ? value.ToString() : null;
    private static bool GetBool(JsonElement json, string name) => json.ValueKind == JsonValueKind.Object && json.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;
    private static DateTimeOffset? GetDate(JsonElement json, string name) => DateTimeOffset.TryParse(Get(json, name), out var date) ? date : null;
}
