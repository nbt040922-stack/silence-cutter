using System.Collections.ObjectModel;
using System.Windows.Input;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.ViewModels;

public sealed class ChannelsPageViewModel : ViewModelBase
{
    private const int PageSize = 10;
    private readonly List<ChannelRowViewModel> _rows = [];
    private string _searchText = string.Empty;
    private bool _isMultiSelect;
    private int _currentPage = 1;
    private bool _isBulkModalOpen;
    private string _bulkControlKind = "Notification";
    private bool _isAddModalOpen;

    public ChannelsPageViewModel()
    {
        ToggleMultiSelectCommand = new RelayCommand(_ => ToggleMultiSelect());
        PagingCommand = new RelayCommand(value => ChangePage(value?.ToString()));
        OpenBulkControlCommand = new RelayCommand(value => OpenBulkControl(value?.ToString()), _ => HasSelection);
        BulkEnableCommand = new RelayCommand(_ => RequestBulkControl(true), _ => HasSelection);
        BulkDisableCommand = new RelayCommand(_ => RequestBulkControl(false), _ => HasSelection);
        CloseBulkControlCommand = new RelayCommand(_ => IsBulkModalOpen = false);
        OpenAddChannelsCommand = new RelayCommand(_ => OpenAddChannels());
        AddDraftRowCommand = new RelayCommand(_ => AddDraftRow());
        RemoveDraftRowCommand = new RelayCommand(row => RemoveDraftRow(row as ChannelDraftViewModel), _ => AddRows.Count > 1);
        CloseAddChannelsCommand = new RelayCommand(_ => IsAddModalOpen = false);
        SubmitAddChannelsCommand = new RelayCommand(_ => SubmitAddChannels(), _ => CanSubmitAddChannels);
        DeleteChannelsCommand = new RelayCommand(_ => RequestDeleteChannels(), _ => HasSelection);
        AddDraftRow();
    }

    public ObservableCollection<ChannelRowViewModel> VisibleRows { get; } = [];
    public ObservableCollection<ChannelDraftViewModel> AddRows { get; } = [];
    public ICommand ToggleMultiSelectCommand { get; }
    public ICommand PagingCommand { get; }
    public ICommand OpenBulkControlCommand { get; }
    public ICommand BulkEnableCommand { get; }
    public ICommand BulkDisableCommand { get; }
    public ICommand CloseBulkControlCommand { get; }
    public ICommand OpenAddChannelsCommand { get; }
    public ICommand AddDraftRowCommand { get; }
    public ICommand RemoveDraftRowCommand { get; }
    public ICommand CloseAddChannelsCommand { get; }
    public ICommand SubmitAddChannelsCommand { get; }
    public ICommand DeleteChannelsCommand { get; }
    public event Action<string, bool, IReadOnlyList<string>>? BulkControlRequested;
    public event Action<IReadOnlyList<string>>? DeleteChannelsRequested;
    public event Action<IReadOnlyList<ChannelDraft>>? AddChannelsRequested;

    public string SearchText
    {
        get => _searchText;
        set
        {
            if (!Set(ref _searchText, value)) return;
            _currentPage = 1;
            RebuildPage();
        }
    }

    public bool IsMultiSelect
    {
        get => _isMultiSelect;
        private set
        {
            if (!Set(ref _isMultiSelect, value)) return;
            OnPropertyChanged(nameof(MultiSelectText));
        }
    }

    public int CurrentPage => _currentPage;
    public int TotalCount => FilteredRows.Count();
    public int PageCount => Math.Max(1, (int)Math.Ceiling(TotalCount / (double)PageSize));
    public int SelectedCount => _rows.Count(row => row.IsSelected);
    public bool HasSelection => SelectedCount > 0;
    public IReadOnlyList<string> SelectedChannelIds => _rows.Where(row => row.IsSelected).Select(row => row.Id).ToArray();
    public bool IsEmpty => VisibleRows.Count == 0;
    public string SelectedText => $"{SelectedCount} đã chọn";
    public string MultiSelectText => IsMultiSelect ? "Thoát chọn nhiều" : "Chọn nhiều";
    public string RangeText
    {
        get
        {
            if (TotalCount == 0) return "Hiển thị 0 của 0 kênh";
            var first = ((_currentPage - 1) * PageSize) + 1;
            return $"Hiển thị {first} - {first + VisibleRows.Count - 1} của {TotalCount} kênh";
        }
    }
    public IReadOnlyList<ChannelPageItem> PageItems => BuildPageItems();
    public bool HasMultiplePages => PageCount > 1;
    public bool IsBulkModalOpen { get => _isBulkModalOpen; private set => Set(ref _isBulkModalOpen, value); }
    public bool IsAddModalOpen { get => _isAddModalOpen; private set => Set(ref _isAddModalOpen, value); }
    public bool CanSubmitAddChannels => AddRows.Count > 0 && AddRows.All(row => row.IsValid);
    public string BulkModalTitle => _bulkControlKind == "Cut" ? "Điều khiển cắt tool" : "Điều khiển thông báo";
    public string BulkModalCount => $"{SelectedCount} kênh đã chọn";
    public bool IsCutControl => _bulkControlKind == "Cut";
    public string BulkEnableText => IsCutControl ? "Bật cắt tool" : "Bật thông báo";
    public string BulkDisableText => IsCutControl ? "Tắt cắt tool" : "Tắt thông báo";
    public string BulkEnableGlyph => IsCutControl ? "\uE8C6" : "\uEA8F";
    public string BulkDisableGlyph => IsCutControl ? "\uE711" : "\uE7ED";

    public bool? AreAllVisibleSelected
    {
        get => VisibleRows.Count == 0 ? false : VisibleRows.All(row => row.IsSelected) ? true : VisibleRows.Any(row => row.IsSelected) ? null : false;
        set
        {
            if (value is null) return;
            foreach (var row in VisibleRows) row.IsSelected = value.Value;
        }
    }

    public void ReplaceChannels(IEnumerable<ChannelRecord> channels)
    {
        var selectedIds = IsMultiSelect ? _rows.Where(row => row.IsSelected).Select(row => row.Id).ToHashSet(StringComparer.OrdinalIgnoreCase) : [];
        foreach (var row in _rows) row.SelectionChanged -= OnRowSelectionChanged;
        _rows.Clear();
        foreach (var channel in channels)
        {
            var row = new ChannelRowViewModel(channel) { IsSelected = selectedIds.Contains(channel.Id) };
            row.SelectionChanged += OnRowSelectionChanged;
            _rows.Add(row);
        }
        RebuildPage();
        NotifySelectionChanged();
    }

    public void ToggleMultiSelect()
    {
        IsMultiSelect = !IsMultiSelect;
        if (!IsMultiSelect)
            foreach (var row in _rows) row.IsSelected = false;
        NotifySelectionChanged();
    }

    public void GoToPage(int page)
    {
        _currentPage = Math.Clamp(page, 1, PageCount);
        RebuildPage();
    }

    private IEnumerable<ChannelRowViewModel> FilteredRows => string.IsNullOrWhiteSpace(SearchText)
        ? _rows
        : _rows.Where(row => row.Name.Contains(SearchText.Trim(), StringComparison.OrdinalIgnoreCase)
            || row.Id.Contains(SearchText.Trim(), StringComparison.OrdinalIgnoreCase));

    private void ChangePage(string? value)
    {
        if (value == "prev") GoToPage(_currentPage - 1);
        else if (value == "next") GoToPage(_currentPage + 1);
        else if (int.TryParse(value, out var page)) GoToPage(page);
    }

    private void RebuildPage()
    {
        _currentPage = Math.Clamp(_currentPage, 1, PageCount);
        VisibleRows.Clear();
        foreach (var row in FilteredRows.Skip((_currentPage - 1) * PageSize).Take(PageSize)) VisibleRows.Add(row);
        OnPropertyChanged(nameof(CurrentPage));
        OnPropertyChanged(nameof(TotalCount));
        OnPropertyChanged(nameof(PageCount));
        OnPropertyChanged(nameof(RangeText));
        OnPropertyChanged(nameof(PageItems));
        OnPropertyChanged(nameof(HasMultiplePages));
        OnPropertyChanged(nameof(IsEmpty));
        OnPropertyChanged(nameof(AreAllVisibleSelected));
    }

    private IReadOnlyList<ChannelPageItem> BuildPageItems()
    {
        if (PageCount <= 7) return Enumerable.Range(1, PageCount).Select(PageItem).ToArray();
        var pages = new List<ChannelPageItem> { PageItem(1) };
        var start = Math.Max(2, _currentPage - 1);
        var end = Math.Min(PageCount - 1, _currentPage + 1);
        if (start > 2) pages.Add(ChannelPageItem.Ellipsis);
        for (var page = start; page <= end; page++) pages.Add(PageItem(page));
        if (end < PageCount - 1) pages.Add(ChannelPageItem.Ellipsis);
        pages.Add(PageItem(PageCount));
        return pages;
    }

    private ChannelPageItem PageItem(int page) => new(page, page == _currentPage, false);

    private void OpenBulkControl(string? kind)
    {
        if (!HasSelection) return;
        _bulkControlKind = kind == "Cut" ? "Cut" : "Notification";
        OnPropertyChanged(nameof(BulkModalTitle));
        OnPropertyChanged(nameof(BulkModalCount));
        OnPropertyChanged(nameof(IsCutControl));
        OnPropertyChanged(nameof(BulkEnableText));
        OnPropertyChanged(nameof(BulkDisableText));
        OnPropertyChanged(nameof(BulkEnableGlyph));
        OnPropertyChanged(nameof(BulkDisableGlyph));
        IsBulkModalOpen = true;
    }

    private void OnRowSelectionChanged() => NotifySelectionChanged();

    private void NotifySelectionChanged()
    {
        OnPropertyChanged(nameof(SelectedCount));
        OnPropertyChanged(nameof(SelectedText));
        OnPropertyChanged(nameof(HasSelection));
        OnPropertyChanged(nameof(BulkModalCount));
        OnPropertyChanged(nameof(AreAllVisibleSelected));
        ((RelayCommand)OpenBulkControlCommand).RaiseCanExecuteChanged();
        ((RelayCommand)BulkEnableCommand).RaiseCanExecuteChanged();
        ((RelayCommand)BulkDisableCommand).RaiseCanExecuteChanged();
        ((RelayCommand)DeleteChannelsCommand).RaiseCanExecuteChanged();
    }

    private void OpenAddChannels()
    {
        IsAddModalOpen = true;
    }

    private void AddDraftRow()
    {
        var row = new ChannelDraftViewModel();
        row.Changed += OnDraftChanged;
        AddRows.Add(row);
        ((RelayCommand)RemoveDraftRowCommand).RaiseCanExecuteChanged();
    }

    private void RemoveDraftRow(ChannelDraftViewModel? row)
    {
        if (row is null || AddRows.Count <= 1) return;
        row.Changed -= OnDraftChanged;
        AddRows.Remove(row);
        ((RelayCommand)RemoveDraftRowCommand).RaiseCanExecuteChanged();
        OnDraftChanged();
    }

    private void OnDraftChanged()
    {
        OnPropertyChanged(nameof(CanSubmitAddChannels));
        ((RelayCommand)SubmitAddChannelsCommand).RaiseCanExecuteChanged();
    }

    private void SubmitAddChannels()
    {
        var rows = AddRows.Where(row => row.IsValid)
            .Select(row => new ChannelDraft(row.ChannelUrl.Trim(), row.ChannelName.Trim()))
            .ToArray();
        if (rows.Length == 0) return;
        AddChannelsRequested?.Invoke(rows);
        IsAddModalOpen = false;
        foreach (var row in AddRows) row.Changed -= OnDraftChanged;
        AddRows.Clear();
        AddDraftRow();
    }

    private void RequestDeleteChannels()
    {
        if (HasSelection) DeleteChannelsRequested?.Invoke(SelectedChannelIds);
    }

    private void RequestBulkControl(bool enabled)
    {
        if (!HasSelection) return;
        BulkControlRequested?.Invoke(_bulkControlKind, enabled, SelectedChannelIds);
        IsBulkModalOpen = false;
    }
}

public sealed record ChannelDraft(string ChannelUrl, string ChannelName);

public sealed class ChannelDraftViewModel : ViewModelBase
{
    private string _channelUrl = string.Empty;
    private string _channelName = string.Empty;
    public event Action? Changed;
    public string ChannelUrl { get => _channelUrl; set { if (Set(ref _channelUrl, value)) Changed?.Invoke(); } }
    public string ChannelName { get => _channelName; set { if (Set(ref _channelName, value)) Changed?.Invoke(); } }
    public bool IsValid => !string.IsNullOrWhiteSpace(ChannelUrl) && !string.IsNullOrWhiteSpace(ChannelName);
}

public sealed class ChannelRowViewModel(ChannelRecord channel) : ViewModelBase
{
    private bool _isSelected;
    public event Action? SelectionChanged;
    public ChannelRecord Channel { get; } = channel;
    public string Id => Channel.Id;
    public string Name => Channel.Name;
    public bool Enabled => Channel.Enabled;
    public string StatusKind => Channel.StatusKind;
    public string NewVideosText => Channel.NewVideosCount?.ToString() ?? "--";
    public string LastEventText => Channel.LastEventText;
    public string NotificationText => Enabled ? "Bật" : "Tắt";
    public string NotificationStateKind => Enabled ? "On" : "Off";
    public string CutToolText => Channel.CutEnabled ? "Bật" : "Tắt";
    public string CutToolStateKind => Channel.CutEnabled ? "On" : "Off";
    public string NoteText => string.IsNullOrWhiteSpace(Channel.LastError) ? "--" : Channel.LastError;
    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (!Set(ref _isSelected, value)) return;
            SelectionChanged?.Invoke();
        }
    }
}

public sealed record ChannelPageItem(int Number, bool IsSelected, bool IsEllipsis)
{
    public static ChannelPageItem Ellipsis { get; } = new(0, false, true);
    public string Label => IsEllipsis ? "…" : Number.ToString();
}
