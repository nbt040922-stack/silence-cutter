using ContentOps.Monitor.Models;
using ContentOps.Monitor.ViewModels;

namespace ContentOps.Monitor.Tests;

public sealed class ChannelsPageViewModelTests
{
    [Fact]
    public void ShowsTenRowsPerPageAndUsesRealTotal()
    {
        var page = new ChannelsPageViewModel();

        page.ReplaceChannels(CreateChannels(12));

        Assert.Equal(10, page.VisibleRows.Count);
        Assert.Equal(2, page.PageCount);
        Assert.Equal("Hiển thị 1 - 10 của 12 kênh", page.RangeText);

        page.GoToPage(2);

        Assert.Equal(2, page.VisibleRows.Count);
        Assert.Equal("Hiển thị 11 - 12 của 12 kênh", page.RangeText);
    }

    [Fact]
    public void SearchFiltersByChannelNameOrIdAndReturnsToFirstPage()
    {
        var page = new ChannelsPageViewModel();
        page.ReplaceChannels(CreateChannels(12));
        page.GoToPage(2);

        page.SearchText = "channel 03";

        Assert.Single(page.VisibleRows);
        Assert.Equal("Channel 03", page.VisibleRows[0].Name);
        Assert.Equal(1, page.CurrentPage);

        page.SearchText = "UC_011";

        Assert.Single(page.VisibleRows);
        Assert.Equal("UC_011", page.VisibleRows[0].Id);
    }

    [Fact]
    public void LeavingMultiSelectClearsSelectionsAndDisablesBulkActions()
    {
        var page = new ChannelsPageViewModel();
        page.ReplaceChannels(CreateChannels(4));

        page.ToggleMultiSelect();
        page.VisibleRows[0].IsSelected = true;
        page.VisibleRows[2].IsSelected = true;

        Assert.True(page.IsMultiSelect);
        Assert.Equal(2, page.SelectedCount);
        Assert.True(page.HasSelection);
        Assert.Equal("2 đã chọn", page.SelectedText);

        page.ToggleMultiSelect();

        Assert.False(page.IsMultiSelect);
        Assert.Equal(0, page.SelectedCount);
        Assert.False(page.HasSelection);
        Assert.All(page.VisibleRows, row => Assert.False(row.IsSelected));
    }

    [Fact]
    public void EmptyStateOnlyAppearsWhenTheFilteredPageHasNoRows()
    {
        var page = new ChannelsPageViewModel();
        page.ReplaceChannels(CreateChannels(3));

        Assert.False(page.IsEmpty);

        page.SearchText = "không tồn tại";

        Assert.True(page.IsEmpty);
    }

    [Fact]
    public void ChannelControlsExposeOnAndOffStatesFromRealFlags()
    {
        var on = new ChannelRowViewModel(new ChannelRecord("on", "On", true, null, null, null, null, true));
        var off = new ChannelRowViewModel(new ChannelRecord("off", "Off", false, null, null, null, null, false));

        Assert.Equal("Bật", on.NotificationText);
        Assert.Equal("On", on.NotificationStateKind);
        Assert.Equal("Bật", on.CutToolText);
        Assert.Equal("On", on.CutToolStateKind);
        Assert.Equal("Tắt", off.NotificationText);
        Assert.Equal("Off", off.NotificationStateKind);
        Assert.Equal("Tắt", off.CutToolText);
        Assert.Equal("Off", off.CutToolStateKind);
    }

    [Fact]
    public void BulkControlRaisesBackendRequestForSelectedChannels()
    {
        var page = new ChannelsPageViewModel();
        page.ReplaceChannels(CreateChannels(3));
        page.ToggleMultiSelect();
        page.VisibleRows[0].IsSelected = true;
        var requests = new List<(string Kind, bool Enabled, IReadOnlyList<string> ChannelIds)>();
        page.BulkControlRequested += (kind, enabled, ids) => requests.Add((kind, enabled, ids));

        page.OpenBulkControlCommand.Execute("Cut");
        page.BulkEnableCommand.Execute(null);

        var request = Assert.Single(requests);
        Assert.Equal("Cut", request.Kind);
        Assert.True(request.Enabled);
        Assert.Equal(new[] { "UC_001" }, request.ChannelIds);
    }

    [Fact]
    public void DeleteCommandIsDisabledWithoutSelectionAndEmitsOnlySelectedChannels()
    {
        var page = new ChannelsPageViewModel();
        page.ReplaceChannels(CreateChannels(3));
        var requests = new List<IReadOnlyList<string>>();
        page.DeleteChannelsRequested += ids => requests.Add(ids);

        Assert.False(page.DeleteChannelsCommand.CanExecute(null));

        page.ToggleMultiSelect();
        page.VisibleRows[1].IsSelected = true;
        Assert.True(page.DeleteChannelsCommand.CanExecute(null));
        page.DeleteChannelsCommand.Execute(null);

        Assert.Equal(new[] { "UC_002" }, Assert.Single(requests));
    }

    [Fact]
    public void AddFormRequiresLinkAndNameOnEveryRow()
    {
        var page = new ChannelsPageViewModel();

        Assert.False(page.SubmitAddChannelsCommand.CanExecute(null));
        page.AddRows[0].ChannelUrl = "https://youtube.com/@demo";
        page.AddRows[0].ChannelName = "Demo";
        Assert.True(page.SubmitAddChannelsCommand.CanExecute(null));

        page.AddDraftRowCommand.Execute(null);
        Assert.False(page.SubmitAddChannelsCommand.CanExecute(null));
    }

    private static IReadOnlyList<ChannelRecord> CreateChannels(int count) => Enumerable.Range(1, count)
        .Select(index => new ChannelRecord(
            $"UC_{index:000}",
            $"Channel {index:00}",
            index % 3 != 0,
            index % 3 == 0 ? "Paused" : "Active",
            index,
            DateTimeOffset.Now.AddMinutes(-index),
            null))
        .ToArray();
}
