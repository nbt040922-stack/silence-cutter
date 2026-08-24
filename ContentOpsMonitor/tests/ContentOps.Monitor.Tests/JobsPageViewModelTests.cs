using ContentOps.Monitor.Models;
using ContentOps.Monitor.ViewModels;

namespace ContentOps.Monitor.Tests;

public sealed class JobsPageViewModelTests
{
    [Fact]
    public void EmptyBackendUsesThreeApprovedPreviewJobs()
    {
        var page = new JobsPageViewModel();

        page.ReplaceJobs([]);

        Assert.Equal(3, page.TotalCount);
        Assert.Contains(page.VisibleRows, row => row.StatusKind == "Completed");
        Assert.Contains(page.VisibleRows, row => row.StatusKind == "Failed");
        Assert.Contains(page.VisibleRows, row => row.StatusKind == "Queued");
        Assert.NotNull(page.SelectedJob);
    }

    [Fact]
    public void PreviewModeKeepsThreeApprovedJobsWhenBackendReturnsRows()
    {
        var page = new JobsPageViewModel();

        page.ReplaceJobs([Job("real", "Backend job", "COMPLETED")]);

        Assert.True(page.IsPreviewData);
        Assert.Equal(3, page.TotalCount);
        Assert.DoesNotContain(page.VisibleRows, row => row.Id == "real");
    }

    [Fact]
    public void RealJobsReplacePreviewAndStatusFilterNarrowsRows()
    {
        var page = new JobsPageViewModel(usePreviewData: false);
        page.ReplaceJobs([
            Job("1", "Completed job", "COMPLETED"),
            Job("2", "Failed job", "FAILED")
        ]);

        page.StatusFilter = "Thất bại";

        Assert.Single(page.VisibleRows);
        Assert.Equal("2", page.VisibleRows[0].Id);
        Assert.False(page.IsPreviewData);
    }

    [Fact]
    public void SearchAndSelectionUpdateTheDetailPanel()
    {
        var page = new JobsPageViewModel(usePreviewData: false);
        page.ReplaceJobs([
            Job("1", "Alpha", "COMPLETED"),
            Job("2", "Beta", "QUEUED")
        ]);

        page.SearchText = "Beta";
        page.SelectJobCommand.Execute(page.VisibleRows[0]);

        Assert.Single(page.VisibleRows);
        Assert.Equal("Beta", page.SelectedJob!.Title);
    }

    [Fact]
    public void DataRefreshKeepsCurrentPageAndSelectedJobById()
    {
        var page = new JobsPageViewModel(usePreviewData: false);
        var initial = Enumerable.Range(1, 12).Select(index => Job($"{index}", $"Job {index}", "RUNNING")).ToArray();
        page.ReplaceJobs(initial);
        page.PagingCommand.Execute("2");
        page.SelectJobCommand.Execute(page.VisibleRows[0]);

        page.ReplaceJobs(initial.Select(job => job.Id == "11" ? job with { Title = "Updated job 11", Progress = 72 } : job));

        Assert.Equal(2, page.CurrentPage);
        Assert.Equal("11", page.SelectedJob!.Id);
        Assert.Equal("Updated job 11", page.SelectedJob.Title);
        Assert.Equal(72, page.SelectedJob.Progress);
    }

    [Fact]
    public void RunningNavigationUsesApprovedRunningPreview()
    {
        var page = new JobsPageViewModel();

        page.ApplyNavigationFilter("Đang chạy");

        Assert.True(page.IsRunningView);
        Assert.Equal("Đang chạy (Running Jobs)", page.PageTitle);
        Assert.Equal(3, page.VisibleRows.Count);
        Assert.All(page.VisibleRows, row => Assert.Equal("Running", row.StatusKind));
        Assert.Equal([78d, 45d, 20d], page.VisibleRows.Select(row => row.Progress));
        Assert.Equal(["00:12:45", "00:08:31", "00:04:21"], page.VisibleRows.Select(row => row.ElapsedText));
    }

    [Fact]
    public void RunningPreviewExposesApprovedSummaryAndEta()
    {
        var page = new JobsPageViewModel();

        page.ApplyNavigationFilter("Đang chạy");

        Assert.Equal("56%", page.AverageProgressText);
        Assert.Equal("00:11:32", page.AverageElapsedText);
        Assert.Equal("00:21:48", page.AverageEtaText);
        Assert.Equal("07:38:20", page.SelectedJob!.EstimatedCompletionTimeText);
        Assert.Equal("24/08/2024", page.SelectedJob.EstimatedCompletionDateText);
    }

    [Fact]
    public void RefreshKeepsRunningPreviewWhileRunningViewIsSelected()
    {
        var page = new JobsPageViewModel();
        page.ApplyNavigationFilter("Đang chạy");

        page.ReplaceJobs([]);

        Assert.Equal(3, page.VisibleRows.Count);
        Assert.All(page.VisibleRows, row => Assert.Equal("Running", row.StatusKind));
    }

    [Theory]
    [InlineData("Chờ xử lý", "Queued", "Chờ xử lý (Queued Jobs)")]
    [InlineData("Đã hoàn thành", "Completed", "Đã hoàn thành (Completed Jobs)")]
    [InlineData("Thất bại", "Failed", "Thất bại (Failed Jobs)")]
    public void StatusNavigationUsesThreeMatchingPreviewJobs(string filter, string statusKind, string title)
    {
        var page = new JobsPageViewModel();

        page.ApplyNavigationFilter(filter);

        Assert.True(page.IsStatusView);
        Assert.Equal(title, page.PageTitle);
        Assert.Equal(3, page.VisibleRows.Count);
        Assert.All(page.VisibleRows, row => Assert.Equal(statusKind, row.StatusKind));
    }

    [Theory]
    [InlineData("Chờ xử lý", "Thời gian chờ", "Dự kiến bắt đầu", "Hủy khỏi hàng đợi", "\uE823")]
    [InlineData("Đã hoàn thành", "Thời gian xử lý", "Hoàn thành lúc", "Mở thư mục đầu ra", "\uE73E")]
    [InlineData("Thất bại", "Thời gian chạy", "Lỗi lúc", "Thử lại", "\uE711")]
    public void StatusViewChangesColumnsAndActions(string filter, string durationTitle, string estimateTitle, string primaryAction, string glyph)
    {
        var page = new JobsPageViewModel();

        page.ApplyNavigationFilter(filter);

        Assert.Equal(durationTitle, page.DurationColumnTitle);
        Assert.Equal(estimateTitle, page.EstimateColumnTitle);
        Assert.Equal(primaryAction, page.SelectedJob!.PrimaryActionText);
        Assert.Equal(glyph, page.MetricOneGlyph);
    }

    [Theory]
    [InlineData("Chờ xử lý", "3", "00:08:20", "1 - 3", "00:06:00")]
    [InlineData("Đã hoàn thành", "3", "100%", "00:14:26", "468 MB")]
    [InlineData("Thất bại", "3", "2", "Silence", "08:42:10")]
    public void StatusViewExposesMatchingMetrics(string filter, string first, string second, string third, string fourth)
    {
        var page = new JobsPageViewModel();

        page.ApplyNavigationFilter(filter);

        Assert.Equal(first, page.MetricOneValue);
        Assert.Equal(second, page.MetricTwoValue);
        Assert.Equal(third, page.MetricThreeValue);
        Assert.Equal(fourth, page.MetricFourValue);
    }

    [Theory]
    [InlineData("Chờ xử lý", "Queued")]
    [InlineData("Đã hoàn thành", "Completed")]
    [InlineData("Thất bại", "Failed")]
    public void RefreshKeepsSelectedStatusPreview(string filter, string statusKind)
    {
        var page = new JobsPageViewModel();
        page.ApplyNavigationFilter(filter);

        page.ReplaceJobs([]);

        Assert.Equal(3, page.VisibleRows.Count);
        Assert.All(page.VisibleRows, row => Assert.Equal(statusKind, row.StatusKind));
    }

    [Theory]
    [InlineData("QUEUED", "primary", "cancel")]
    [InlineData("QUEUED", "secondary", "delete")]
    [InlineData("COMPLETED", "primary", "open-output")]
    [InlineData("COMPLETED", "secondary", "copy-url")]
    [InlineData("FAILED", "primary", "retry")]
    [InlineData("FAILED", "secondary", "delete")]
    [InlineData("PROCESSING", "primary", "cancel")]
    [InlineData("PROCESSING", "secondary", "cancel")]
    [InlineData("COMPLETED", "cancel", "delete")]
    public void DetailActionsResolveToSupportedOperations(string status, string requested, string expected)
    {
        var row = new JobRowViewModel(Job("job", "Demo", status));

        Assert.Equal(expected, JobActionResolver.Resolve(requested, row));
    }

    private static JobRecord Job(string id, string title, string status) => new(
        id, title, null, "006US", "AUTO", status, "Output", 100,
        DateTimeOffset.Now.AddMinutes(-5), DateTimeOffset.Now, null, null, "YT_NOTIFI");
}
