using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using ContentOps.Monitor.Models;
using ContentOps.Monitor.Services.Api;

namespace ContentOps.Monitor.Tests;

public sealed class ApiAdapterTests
{
    [Fact]
    public async Task ApiClientParsesHealthAndPreservesServiceFields()
    {
        using var client = CreateClient("{\"status\":\"ok\",\"service\":\"YT_NOTIFI\",\"enabled_channels\":3}");
        var result = await client.GetJsonAsync<Dictionary<string, object>>(new Uri("http://127.0.0.1:8787/health"));

        Assert.True(result.Success);
        Assert.Equal(HttpStatusCode.OK, result.StatusCode);
        Assert.Equal("ok", result.Value!["status"].ToString());
    }

    [Fact]
    public async Task YtNotifiAdapterMapsJobsAndChannelsFromArrays()
    {
        using var client = new ApiClient(new FakeHandler(request => Task.FromResult(request.RequestUri!.AbsolutePath switch
        {
            "/api/jobs" => JsonResponse("[{\"id\":7,\"video_title\":\"Demo\",\"status\":\"COMPLETED\",\"channel_name\":\"Channel\"} ]"),
            "/api/channels" => JsonResponse("[{\"channel_id\":\"abc\",\"name\":\"Channel\",\"enabled\":true,\"cut_enabled\":false,\"status\":\"Ready\"} ]"),
            _ => JsonResponse("{}", HttpStatusCode.NotFound)
        })));
        var adapter = new YtNotifiAdapter(client, new Uri("http://127.0.0.1:8787"));

        var jobs = await adapter.GetJobsAsync();
        var channels = await adapter.GetChannelsAsync();

        Assert.True(jobs.Success);
        Assert.Equal("Demo", jobs.Value![0].Title);
        Assert.True(channels.Success);
        Assert.Equal("abc", channels.Value![0].Id);
        Assert.True(channels.Value![0].Enabled);
        Assert.False(channels.Value![0].CutEnabled);
    }

    [Fact]
    public async Task YtNotifiAdapterSendsCutFlagToChannelPatch()
    {
        HttpRequestMessage? received = null;
        var receivedBody = string.Empty;
        using var client = new ApiClient(new FakeHandler(async request =>
        {
            received = request;
            receivedBody = await request.Content!.ReadAsStringAsync();
            return JsonResponse("{}");
        }));
        var adapter = new YtNotifiAdapter(client, new Uri("http://127.0.0.1:8787"));

        var result = await adapter.SetChannelCutEnabledAsync("UC/test", true);

        Assert.True(result.Success);
        Assert.Equal(HttpMethod.Patch, received!.Method);
        Assert.Equal("/api/channels/UC%2Ftest", received.RequestUri!.AbsolutePath);
        Assert.Contains("cut_enabled", receivedBody);
    }

    [Fact]
    public async Task ManualLanAdapterCreatesJobWithoutAuthorizationHeader()
    {
        HttpRequestMessage? received = null;
        var receivedBody = string.Empty;
        long? receivedLength = null;
        using var client = new ApiClient(new FakeHandler(async request =>
        {
            received = request;
            receivedLength = request.Content?.Headers.ContentLength;
            receivedBody = await request.Content!.ReadAsStringAsync();
            return JsonResponse("{\"job_id\":\"m-1\"}", HttpStatusCode.Created);
        }));
        var adapter = new ManualLanAdapter(client, new Uri("http://127.0.0.1:8780"));

        var result = await adapter.CreateJobAsync("https://youtu.be/demo");

        Assert.True(result.Success);
        Assert.Null(received!.Headers.Authorization);
        Assert.True(receivedLength > 0);
        Assert.Contains("youtu.be/demo", receivedBody);
    }

    [Fact]
    public async Task JobAdaptersExposeCancelRetryAndDeleteRoutes()
    {
        var requests = new List<HttpRequestMessage>();
        using var client = new ApiClient(new FakeHandler(request =>
        {
            requests.Add(request);
            return Task.FromResult(JsonResponse("{}"));
        }));
        var yt = new YtNotifiAdapter(client, new Uri("http://127.0.0.1:8787"));
        var lan = new ManualLanAdapter(client, new Uri("http://127.0.0.1:8780"));

        await yt.CancelJobAsync("42");
        await yt.RetryJobAsync("42");
        await yt.DeleteJobAsync("42");
        await lan.CancelJobAsync("m/1");
        await lan.RetryJobAsync("m/1");
        await lan.DeleteJobAsync("m/1");

        Assert.Equal(HttpMethod.Post, requests[0].Method);
        Assert.Equal("/api/jobs/42/cancel", requests[0].RequestUri!.AbsolutePath);
        Assert.Equal("/api/jobs/42/retry", requests[1].RequestUri!.AbsolutePath);
        Assert.Equal(HttpMethod.Delete, requests[2].Method);
        Assert.Equal("/jobs/m%2F1/cancel", requests[3].RequestUri!.AbsolutePath);
        Assert.Equal(HttpMethod.Delete, requests[5].Method);
    }

    [Fact]
    public async Task YtNotifiAdapterMapsRealJobProgressStageAndOutput()
    {
        using var client = new ApiClient(new FakeHandler(_ => Task.FromResult(JsonResponse("[{\"id\":38,\"video_title\":\"Real title\",\"status\":\"COMPLETED\",\"process_progress\":100,\"process_state\":\"DONE\",\"processed_file_path\":\"D:\\\\output.mp4\",\"created_at\":\"2026-08-23T21:01:09+07:00\",\"updated_at\":\"2026-08-23T21:07:35+07:00\"}]"))));
        var adapter = new YtNotifiAdapter(client, new Uri("http://127.0.0.1:8787"));

        var result = await adapter.GetJobsAsync();

        Assert.True(result.Success);
        var job = result.Value![0];
        Assert.Equal("Real title", job.Title);
        Assert.Equal(100, job.Progress);
        Assert.Equal("Output", job.Stage);
        Assert.Equal("D:\\output.mp4", job.Output);
        Assert.Equal("AUTO", job.Origin);
    }

    [Fact]
    public async Task ManualLanAdapterAcceptsNullProgressFields()
    {
        using var client = new ApiClient(new FakeHandler(_ => Task.FromResult(JsonResponse("{\"jobs\":[{\"id\":\"m-1\",\"title\":\"Manual\",\"status\":\"FAILED\",\"progress\":null,\"overall_progress\":0}]}"))));
        var adapter = new ManualLanAdapter(client, new Uri("http://127.0.0.1:8780"));

        var result = await adapter.GetJobsAsync(null);

        Assert.True(result.Success);
        Assert.Equal(0, result.Value![0].Progress);
    }

    [Fact]
    public async Task ManualLanAdapterUsesSourceFilenameForLegacyGenericTitle()
    {
        using var client = new ApiClient(new FakeHandler(_ => Task.FromResult(JsonResponse("{\"jobs\":[{\"id\":\"old-1\",\"title\":\"www.youtube.com\",\"source_path\":\"D:\\\\jobs\\\\Real video title.mp4\",\"status\":\"PROCESSING\"}]}"))));
        var adapter = new ManualLanAdapter(client, new Uri("http://127.0.0.1:8780"));

        var result = await adapter.GetJobsAsync(null);

        Assert.True(result.Success);
        Assert.Equal("Real video title", result.Value![0].Title);
        Assert.Equal("Real video title", result.Value[0].DisplayName);
    }

    [Fact]
    public async Task ManualLanAdapterPreservesLanJobMetadata()
    {
        using var client = new ApiClient(new FakeHandler(_ => Task.FromResult(JsonResponse("""
            {"jobs":[{"id":"m-42","title":"www.youtube.com","display_name":"Video title from LAN",
            "input_mode":"YOUTUBE","url":"https://youtu.be/demo","source_path":"D:\\jobs\\source.mp4",
            "status":"FAILED","stage":"failed","progress":0,"created_at":"2026-08-24T09:27:04+07:00",
            "started_at":"2026-08-24T09:27:05+07:00","finished_at":"2026-08-24T09:27:09+07:00",
            "output_path":null,"report_path":"D:\\jobs\\report.json","pid":1234,
            "total_elapsed_seconds":4.25,"total_eta_seconds":null,"eta_status":"NOT_APPLICABLE",
            "origin":"MANUAL_LAN","scheduler_state":"FAILED","scheduler_failure_detail":"report missing"}]}
            """))));
        var adapter = new ManualLanAdapter(client, new Uri("http://127.0.0.1:8780"));

        var result = await adapter.GetJobsAsync(null);

        Assert.True(result.Success);
        var job = result.Value![0];
        Assert.Equal("Video title from LAN", job.DisplayName);
        Assert.Equal("YOUTUBE", job.InputMode);
        Assert.Equal("https://youtu.be/demo", job.VideoUrl);
        Assert.Equal("D:\\jobs\\source.mp4", job.SourcePath);
        Assert.Equal("D:\\jobs\\report.json", job.ReportPath);
        Assert.Equal(1234, job.ProcessId);
        Assert.Equal(4.25, job.DurationSeconds);
        Assert.Equal("NOT_APPLICABLE", job.EtaStatus);
        Assert.Equal("FAILED", job.SchedulerState);
        Assert.Equal("report missing", job.SchedulerFailureDetail);
        Assert.Equal("2026-08-24T09:27:05.0000000+07:00", job.Started?.ToString("O"));
        Assert.Equal("2026-08-24T09:27:09.0000000+07:00", job.Finished?.ToString("O"));
    }

    [Fact]
    public async Task ManualLanAdapterLoadsVideoMetadataForPreview()
    {
        HttpRequestMessage? received = null;
        using var client = new ApiClient(new FakeHandler(request =>
        {
            received = request;
            return Task.FromResult(JsonResponse("{\"title\":\"Real video\",\"channel\":\"Real channel\",\"duration_seconds\":1122,\"duration\":\"18:42\",\"thumbnail\":\"https://img.test/thumb.jpg\",\"url\":\"https://youtu.be/demo\"}"));
        }));
        var adapter = new ManualLanAdapter(client, new Uri("http://127.0.0.1:8780"));

        var result = await adapter.GetMetadataAsync("https://youtu.be/demo");

        Assert.True(result.Success);
        Assert.Equal("Real video", result.Value!.Title);
        Assert.Equal("Real channel", result.Value.Channel);
        Assert.Equal("18:42", result.Value.Duration);
        Assert.Equal("/metadata", received!.RequestUri!.AbsolutePath);
        Assert.Contains("url=https%3A%2F%2Fyoutu.be%2Fdemo", received.RequestUri.Query);
    }

    private static ApiClient CreateClient(string body) => new(new FakeHandler(_ => Task.FromResult(JsonResponse(body))));

    private static HttpResponseMessage JsonResponse(string body, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = JsonContent.Create(JsonDocument.Parse(body).RootElement) };

    private sealed class FakeHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> responder) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => responder(request);
    }
}
