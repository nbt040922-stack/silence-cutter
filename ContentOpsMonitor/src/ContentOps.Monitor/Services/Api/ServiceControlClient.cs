using System.Text.Json;

namespace ContentOps.Monitor.Services.Api;

public sealed class ServiceControlClient(ApiClient client, Uri baseUri)
{
    public Task<ApiResult<JsonElement>> GetServicesAsync(CancellationToken ct = default) =>
        client.GetJsonAsync<JsonElement>(new(baseUri, "/api/services"), ct);

    public Task<ApiResult<JsonElement>> StartAsync(string serviceName, CancellationToken ct = default) =>
        SendAsync(serviceName, "start", ct);

    public Task<ApiResult<JsonElement>> StopAsync(string serviceName, CancellationToken ct = default) =>
        SendAsync(serviceName, "stop", ct);

    public Task<ApiResult<JsonElement>> RestartAsync(string serviceName, CancellationToken ct = default) =>
        SendAsync(serviceName, "restart", ct);

    private Task<ApiResult<JsonElement>> SendAsync(string serviceName, string action, CancellationToken ct) =>
        client.PostJsonAsync<JsonElement>(new(baseUri, $"/api/services/{Uri.EscapeDataString(serviceName)}/{action}"), new { }, cancellationToken: ct);
}
