using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using ContentOps.Monitor.Services.Api;

namespace ContentOps.Monitor.Tests;

public sealed class ServiceControlClientTests
{
    [Fact]
    public async Task StartPostsToNamedServiceEndpoint()
    {
        HttpRequestMessage? request = null;
        using var api = new ApiClient(new FakeHandler(message =>
        {
            request = message;
            return Task.FromResult(Response("{\"state\":\"READY\"}"));
        }));
        var client = new ServiceControlClient(api, new Uri("http://127.0.0.1:8793"));

        var result = await client.StartAsync("Qwen");

        Assert.True(result.Success);
        Assert.Equal(HttpMethod.Post, request!.Method);
        Assert.Equal("/api/services/Qwen/start", request.RequestUri!.AbsolutePath);
    }

    [Fact]
    public async Task RestartEscapesServiceName()
    {
        HttpRequestMessage? request = null;
        using var api = new ApiClient(new FakeHandler(message =>
        {
            request = message;
            return Task.FromResult(Response("{\"state\":\"READY\"}"));
        }));
        var client = new ServiceControlClient(api, new Uri("http://127.0.0.1:8793"));

        await client.RestartAsync("Silence Scheduler");

        Assert.Equal("/api/services/Silence%20Scheduler/restart", request!.RequestUri!.AbsolutePath);
    }

    private static HttpResponseMessage Response(string body) => new(HttpStatusCode.OK) { Content = JsonContent.Create(JsonDocument.Parse(body).RootElement) };

    private sealed class FakeHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> responder) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => responder(request);
    }
}
