using ContentOps.Monitor.ViewModels;

namespace ContentOps.Monitor.Tests;

public sealed class ManualJobViewModelTests
{
    [Fact]
    public async Task SubmitAcceptsOnlyYoutubeUrls()
    {
        await using var viewModel = new MainViewModel();

        viewModel.ManualUrl = "https://example.com/video";
        Assert.False(viewModel.SubmitManualJobCommand.CanExecute(null));

        viewModel.ManualUrl = "https://youtu.be/dQw4w9WgXcQ";
        Assert.True(viewModel.SubmitManualJobCommand.CanExecute(null));
    }

    [Fact]
    public async Task SubmitAcceptsMultipleYoutubeUrlsOnePerLine()
    {
        await using var viewModel = new MainViewModel();

        viewModel.ManualUrl = "https://youtu.be/first\nhttps://www.youtube.com/watch?v=second";

        Assert.True(viewModel.SubmitManualJobCommand.CanExecute(null));
        Assert.Equal(2, viewModel.ManualUrlCount);
    }

    [Fact]
    public async Task SubmitRejectsBatchWhenAnyLineIsNotYoutubeUrl()
    {
        await using var viewModel = new MainViewModel();

        viewModel.ManualUrl = "https://youtu.be/first\nhttps://example.com/not-youtube";

        Assert.False(viewModel.SubmitManualJobCommand.CanExecute(null));
    }

    [Fact]
    public async Task CancelClearsTheManualUrl()
    {
        await using var viewModel = new MainViewModel();
        viewModel.ManualUrl = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

        viewModel.CancelManualJobCommand.Execute(null);

        Assert.Equal(string.Empty, viewModel.ManualUrl);
    }

    [Fact]
    public async Task ManualNavigationUsesApprovedHeader()
    {
        await using var viewModel = new MainViewModel();

        viewModel.Navigate("Manual");

        Assert.Equal("Tạo Job mới", viewModel.PageTitle);
        Assert.Equal("Tạo job thủ công để xử lý một hoặc nhiều video YouTube", viewModel.PageSubtitle);
    }

    [Fact]
    public async Task ChannelDiscoveryAcceptsMultipleYoutubeChannelUrls()
    {
        await using var viewModel = new MainViewModel();

        viewModel.ChannelScoutInput = "https://youtube.com/@one\nhttps://www.youtube.com/channel/UC_demo";

        Assert.True(viewModel.DiscoverChannelJobsCommand.CanExecute(null));
        Assert.Equal(2, viewModel.ChannelScoutCount);
    }

    [Fact]
    public async Task ChannelDiscoveryExposesVisibleIdleStatus()
    {
        await using var viewModel = new MainViewModel();

        Assert.Equal("Quét và tạo Jobs", viewModel.ChannelScoutButtonText);
        Assert.Equal("Dán link kênh YouTube để bắt đầu quét.", viewModel.ChannelScoutMessage);
    }

    [Fact]
    public async Task ChannelToastStartsHidden()
    {
        await using var viewModel = new MainViewModel();

        Assert.False(viewModel.IsChannelsMessageVisible);
    }

    [Fact]
    public async Task DashboardRefreshNotifiesPagedJobsAndAlerts()
    {
        await using var viewModel = new MainViewModel();
        var changed = new List<string?>();
        viewModel.PropertyChanged += (_, args) => changed.Add(args.PropertyName);

        await viewModel.RefreshAsync();

        Assert.Contains(nameof(viewModel.DashboardActiveJobs), changed);
        Assert.Contains(nameof(viewModel.DashboardAlerts), changed);
    }

    [Fact]
    public async Task DashboardAlertFooterSupportsNumberedPages()
    {
        await using var viewModel = new MainViewModel();
        for (var index = 0; index < 12; index++)
            viewModel.Alerts.Add(new(ContentOps.Monitor.Models.AlertSeverity.INFO, $"Service {index}", "message", DateTimeOffset.UtcNow));

        viewModel.DashboardPagingCommand.Execute("Alerts:page:2");

        Assert.Equal(5, viewModel.DashboardAlerts.Count);
        Assert.Contains(viewModel.DashboardAlertPages, item => item.Number == 2 && item.IsSelected);
        Assert.False(viewModel.DashboardAlertsHasMorePages);
    }

    [Fact]
    public async Task ChannelDiscoveryRejectsNonYoutubeUrls()
    {
        await using var viewModel = new MainViewModel();

        viewModel.ChannelScoutInput = "https://youtube.com/@one\nhttps://example.com/not-channel";

        Assert.False(viewModel.DiscoverChannelJobsCommand.CanExecute(null));
    }

    [Fact]
    public async Task BackgroundRefreshDoesNotDisableManualJobCommand()
    {
        await using var viewModel = new MainViewModel();
        viewModel.ManualUrl = "https://youtu.be/dQw4w9WgXcQ";

        var refresh = viewModel.RefreshAsync();

        Assert.True(viewModel.SubmitManualJobCommand.CanExecute(null));
        await refresh;
    }

    [Fact]
    public async Task LiveObservationIsEnabledOnlyOnDashboard()
    {
        await using var viewModel = new MainViewModel();

        viewModel.Navigate("Jobs");
        Assert.False(viewModel.IsLiveObservationPage);

        viewModel.Navigate("Dashboard");
        Assert.True(viewModel.IsLiveObservationPage);
    }
}
