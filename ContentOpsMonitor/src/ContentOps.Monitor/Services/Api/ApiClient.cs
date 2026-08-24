using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text;

namespace ContentOps.Monitor.Services.Api;

public sealed record ApiResult<T>(
    bool Success,
    T? Value,
    HttpStatusCode? StatusCode = null,
    string? Error = null)
{
    public static ApiResult<T> Failed(string error, HttpStatusCode? statusCode = null) => new(false, default, statusCode, error);
}

public sealed class ApiClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true
    };

    private readonly HttpClient _httpClient;
    private readonly bool _disposeClient;

    public ApiClient(HttpMessageHandler? handler = null, TimeSpan? timeout = null)
    {
        _httpClient = handler is null ? new HttpClient() : new HttpClient(handler);
        _disposeClient = true;
        _httpClient.Timeout = timeout ?? TimeSpan.FromSeconds(3);
    }

    public async Task<ApiResult<T>> GetJsonAsync<T>(Uri uri, CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await _httpClient.GetAsync(uri, cancellationToken);
            return await ReadResponseAsync<T>(response, cancellationToken);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            return ApiResult<T>.Failed(ex.Message);
        }
    }

    public async Task<ApiResult<T>> PostJsonAsync<T>(Uri uri, object payload, string? bearerToken = null, CancellationToken cancellationToken = default)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, uri)
            {
                Content = CreateJsonContent(payload)
            };
            if (!string.IsNullOrWhiteSpace(bearerToken))
                request.Headers.Authorization = new("Bearer", bearerToken);
            using var response = await _httpClient.SendAsync(request, cancellationToken);
            return await ReadResponseAsync<T>(response, cancellationToken);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            return ApiResult<T>.Failed(ex.Message);
        }
    }

    public async Task<ApiResult<T>> PatchJsonAsync<T>(Uri uri, object payload, CancellationToken cancellationToken = default)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Patch, uri) { Content = CreateJsonContent(payload) };
            using var response = await _httpClient.SendAsync(request, cancellationToken);
            return await ReadResponseAsync<T>(response, cancellationToken);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            return ApiResult<T>.Failed(ex.Message);
        }
    }

    public async Task<ApiResult<T>> DeleteJsonAsync<T>(Uri uri, CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await _httpClient.DeleteAsync(uri, cancellationToken);
            return await ReadResponseAsync<T>(response, cancellationToken);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            return ApiResult<T>.Failed(ex.Message);
        }
    }

    private static async Task<ApiResult<T>> ReadResponseAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        var body = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode)
            return ApiResult<T>.Failed(string.IsNullOrWhiteSpace(body) ? response.ReasonPhrase ?? "HTTP error" : body, response.StatusCode);
        try
        {
            var value = JsonSerializer.Deserialize<T>(body, JsonOptions);
            return new ApiResult<T>(true, value, response.StatusCode);
        }
        catch (JsonException ex)
        {
            return ApiResult<T>.Failed(ex.Message, response.StatusCode);
        }
    }

    private static HttpContent CreateJsonContent(object payload) =>
        new StringContent(JsonSerializer.Serialize(payload, JsonOptions), Encoding.UTF8, "application/json");

    public void Dispose()
    {
        if (_disposeClient) _httpClient.Dispose();
    }
}
