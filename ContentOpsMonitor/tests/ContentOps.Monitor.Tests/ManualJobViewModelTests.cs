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
        Assert.Equal("Tạo job thủ công để xử lý một video YouTube", viewModel.PageSubtitle);
    }
}
