using System.Collections.ObjectModel;
using System.IO;
using System.Diagnostics;
using System.Windows;
using System.Windows.Input;
using ContentOps.Monitor.Configuration;
using ContentOps.Monitor.Models;
using ContentOps.Monitor.Services.Alerts;
using ContentOps.Monitor.Services.Api;
using ContentOps.Monitor.Services.Health;

namespace ContentOps.Monitor.ViewModels;

public sealed class MainViewModel : ViewModelBase, IAsyncDisposable
{
    private static readonly TimeSpan LiveSyncInterval = TimeSpan.FromSeconds(2);
    private readonly MonitorConfigStore _configStore = new();
    private readonly ApiClient _apiClient = new();
    private readonly YtNotifiAdapter _yt;
    private readonly ManualLanAdapter _lan;
    private readonly HealthMonitor _health;
    private readonly ServiceControlClient _serviceControl;
    private readonly AlertStore _alertStore;
    private string _currentPage = "Dashboard";
    private string _lastUpdated = "Chưa cập nhật";
    private string _manualUrl = string.Empty;
    private string _manualMessage = string.Empty;
    private string _manualPreviewMessage = "Dán URL YouTube để tải metadata";
    private string _channelScoutInput = string.Empty;
    private string _channelScoutMessage = "Dán link kênh YouTube để bắt đầu quét.";
    private string _manualMode = "Manual";
    private string _channelsMessage = string.Empty;
    private bool _isChannelsMessageVisible;
    private CancellationTokenSource? _channelsMessageCts;
    private ManualVideoMetadata? _manualPreviewMetadata;
    private CancellationTokenSource? _manualMetadataCts;
    private CancellationTokenSource? _syncCts;
    private Task? _syncLoop;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private string _jobFilter = "Tất cả";
    private int _dashboardJobsPage;
    private int _dashboardAlertsPage;
    private int _dashboardChannelsPage;
    private bool _isBusy;

    public MainViewModel()
    {
        Config = MonitorConfig.CreateDefault();
        _yt = new(_apiClient, new Uri(Config.Endpoints["YT_NOTIFI"].BaseUrl));
        _lan = new(_apiClient, new Uri(Config.Endpoints["Manual LAN API"].BaseUrl));
        _serviceControl = new(_apiClient, new Uri(Config.ServiceControlBaseUrl));
        _alertStore = new(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ContentOps", "Monitor", "alerts.json"));
        var definitions = Config.Endpoints.Values.Select(endpoint => new ServiceDefinition(endpoint.Name, endpoint.Port, endpoint.BaseUrl)).ToArray();
        _health = new(definitions, (service, ct) => ServiceAdapters.GetHealthAsync(_apiClient, service, ct), LiveSyncInterval);
        _health.SnapshotChanged += UpdateServices;
        _health.AlertRaised += AddAlert;
        NavigateCommand = new RelayCommand(page => NavigateItem(page as NavigationItem));
        JobFilterCommand = new RelayCommand(filter => ApplyJobFilter(filter?.ToString() ?? "Tất cả"));
        RefreshCommand = new RelayCommand(_ => _ = RefreshAsync(), _ => !IsBusy);
        ServiceStartCommand = new RelayCommand(name => _ = ControlServiceAsync(name?.ToString(), "start"));
        ServiceToggleCommand = new RelayCommand(snapshot => _ = ToggleServiceAsync(snapshot as ServiceSnapshot));
        ServiceRestartCommand = new RelayCommand(name => _ = ControlServiceAsync(name?.ToString(), "restart"));
        ServiceHealthCommand = new RelayCommand(name => OpenServiceHealth(name?.ToString()));
        JobActionCommand = new RelayCommand(value => _ = ExecuteJobActionAsync(value as JobActionRequest));
        JobPrimaryActionCommand = new RelayCommand(value => _ = ExecuteJobActionAsync(new(value as JobRowViewModel ?? JobsPage.SelectedJob!, "primary")));
        JobSecondaryActionCommand = new RelayCommand(value => _ = ExecuteJobActionAsync(new(value as JobRowViewModel ?? JobsPage.SelectedJob!, "secondary")));
        JobOpenOutputCommand = new RelayCommand(value => _ = ExecuteJobActionAsync(new(value as JobRowViewModel ?? JobsPage.SelectedJob!, "open-output")));
        JobCancelCommand = new RelayCommand(value => _ = ExecuteJobActionAsync(new(value as JobRowViewModel ?? JobsPage.SelectedJob!, "cancel")));
        OpenLogFolderCommand = new RelayCommand(_ => OpenLogFolder());
        SubmitManualJobCommand = new RelayCommand(_ => _ = SubmitManualJobAsync(), _ => !IsBusy && AreManualUrlsValid());
        CancelManualJobCommand = new RelayCommand(_ => { ManualUrl = string.Empty; ManualMessage = string.Empty; ManualPreviewMessage = "Dán URL YouTube để tải metadata"; });
        DiscoverChannelJobsCommand = new RelayCommand(_ => _ = DiscoverChannelJobsAsync(), _ => !IsBusy && AreChannelScoutUrlsValid());
        SelectManualModeCommand = new RelayCommand(_ => ManualMode = "Manual");
        SelectChannelScoutModeCommand = new RelayCommand(_ => ManualMode = "ChannelScout");
        ToggleChannelCommand = new RelayCommand(channel => _ = ToggleChannelAsync(channel as ChannelRecord));
        DashboardPagingCommand = new RelayCommand(value => ChangeDashboardPage(value?.ToString()));
        ChannelsPage.BulkControlRequested += HandleBulkControlRequested;
        ChannelsPage.AddChannelsRequested += HandleAddChannelsRequested;
        ChannelsPage.DeleteChannelsRequested += HandleDeleteChannelsRequested;
    }

    public MonitorConfig Config { get; private set; }
    public ObservableCollection<ServiceSnapshot> Services { get; } = [];
    public ObservableCollection<ServiceSnapshot> DashboardServices { get; } = [];
    public ObservableCollection<JobRecord> Jobs { get; } = [];
    public ObservableCollection<JobRecord> ActiveJobs { get; } = [];
    public ObservableCollection<JobRecord> VisibleJobs { get; } = [];
    public JobsPageViewModel JobsPage { get; } = new(false);
    public ObservableCollection<ChannelRecord> Channels { get; } = [];
    public ChannelsPageViewModel ChannelsPage { get; } = new();
    public ObservableCollection<MonitorAlert> Alerts { get; } = [];
    public ObservableCollection<JobRecord> DashboardActiveJobs { get; } = [];
    public ObservableCollection<MonitorAlert> DashboardAlerts { get; } = [];
    public ObservableCollection<ChannelRecord> DashboardChannels { get; } = [];
    public ObservableCollection<NavigationItem> NavigationItems { get; } =
    [
        new("Dashboard", "Dashboard", "\uE80F"), new("Kênh theo dõi", "Channels", "\uE716"), new("Jobs", "Jobs", "\uE8FD", false, true),
        new("Tất cả", "Jobs", "\uE8FD", true), new("Đang chạy", "Jobs", "\uE768", true), new("Chờ xử lý", "Jobs", "\uE823", true),
        new("Đã hoàn thành", "Jobs", "\uE73E", true), new("Thất bại", "Jobs", "\uE711", true), new("Tạo Job mới", "Manual", "\uE710", true),
        new("Services", "Services", "\uE950"), new("Cảnh báo", "Alerts", "\uEA8F"), new("Nhật ký", "Logs", "\uE756"), new("Cấu hình", "Config", "\uE713")
    ];
    public string CurrentPage
    {
        get => _currentPage;
        private set
        {
            if (!Set(ref _currentPage, value)) return;
            OnPropertyChanged(nameof(PageTitle));
            OnPropertyChanged(nameof(PageSubtitle));
            OnPropertyChanged(nameof(IsLiveObservationPage));
        }
    }
    public bool IsLiveObservationPage => CurrentPage == "Dashboard";
    public string PageTitle => CurrentPage switch { "Channels" => "Kênh theo dõi", "Jobs" => JobsPage.PageTitle, "Manual" => "Tạo Job mới", _ => CurrentPage };
    public string PageSubtitle => CurrentPage switch
    {
        "Channels" => "Quản lý và giám sát các kênh YouTube",
        "Jobs" => JobsPage.PageSubtitle,
        "Manual" => "Tạo job thủ công để xử lý một hoặc nhiều video YouTube",
        _ => "Tổng quan hệ thống ContentOps"
    };
    public string LastUpdated { get => _lastUpdated; private set => Set(ref _lastUpdated, value); }
    public string ManualUrl
    {
        get => _manualUrl;
        set
        {
            if (!Set(ref _manualUrl, value)) return;
            _manualMetadataCts?.Cancel();
            _manualPreviewMetadata = null;
            OnPropertyChanged(nameof(ManualPreviewTitle));
            OnPropertyChanged(nameof(ManualPreviewChannel));
            OnPropertyChanged(nameof(ManualPreviewDuration));
            OnPropertyChanged(nameof(ManualUrlCount));
            var firstUrl = ManualUrls.FirstOrDefault();
            ManualPreviewMessage = firstUrl is not null && IsValidManualUrl(firstUrl) ? "Đang tải metadata video đầu tiên…" : "Dán URL YouTube, mỗi dòng một video";
            ((RelayCommand)SubmitManualJobCommand).RaiseCanExecuteChanged();
            if (firstUrl is not null && IsValidManualUrl(firstUrl)) _ = LoadManualMetadataAsync(firstUrl);
        }
    }
    public int ManualUrlCount => ManualUrls.Count;
    public string ManualMessage { get => _manualMessage; private set => Set(ref _manualMessage, value); }
    public string ManualPreviewMessage { get => _manualPreviewMessage; private set => Set(ref _manualPreviewMessage, value); }
    public string ChannelScoutInput
    {
        get => _channelScoutInput;
        set
        {
            if (!Set(ref _channelScoutInput, value)) return;
            OnPropertyChanged(nameof(ChannelScoutCount));
            ((RelayCommand)DiscoverChannelJobsCommand).RaiseCanExecuteChanged();
        }
    }
    public int ChannelScoutCount => ChannelScoutChannels.Count;
    public string ChannelScoutMessage { get => _channelScoutMessage; private set => Set(ref _channelScoutMessage, value); }
    public string ChannelScoutButtonText => IsBusy && IsChannelScoutMode ? "Đang quét…" : "Quét và tạo Jobs";
    public string ManualMode { get => _manualMode; private set { if (Set(ref _manualMode, value)) { OnPropertyChanged(nameof(IsManualMode)); OnPropertyChanged(nameof(IsChannelScoutMode)); OnPropertyChanged(nameof(ChannelScoutButtonText)); } } }
    public bool IsManualMode => ManualMode == "Manual";
    public bool IsChannelScoutMode => ManualMode == "ChannelScout";
    public string ChannelsMessage { get => _channelsMessage; private set => Set(ref _channelsMessage, value); }
    public bool IsChannelsMessageVisible { get => _isChannelsMessageVisible; private set => Set(ref _isChannelsMessageVisible, value); }
    public string ManualPreviewTitle => _manualPreviewMetadata?.Title ?? "Chưa có metadata video";
    public string ManualPreviewChannel => _manualPreviewMetadata?.Channel ?? "--";
    public string ManualPreviewDuration => _manualPreviewMetadata?.Duration ?? "--";
    public string JobFilter { get => _jobFilter; private set => Set(ref _jobFilter, value); }
    public bool IsBusy { get => _isBusy; private set { if (Set(ref _isBusy, value)) { OnPropertyChanged(nameof(ChannelScoutButtonText)); ((RelayCommand)RefreshCommand).RaiseCanExecuteChanged(); ((RelayCommand)SubmitManualJobCommand).RaiseCanExecuteChanged(); ((RelayCommand)DiscoverChannelJobsCommand).RaiseCanExecuteChanged(); } } }
    public int HealthyCount => Services.Count(service => service.State == ServiceState.READY);
    public int AlertCount => Alerts.Count(alert => !alert.Resolved);
    public string HealthSummary => Services.Count == 0 ? "Đang kiểm tra dịch vụ" : $"{HealthyCount}/{Services.Count} services healthy";
    public string HealthState => Services.Any(s => s.State == ServiceState.DOWN) ? "DOWN" : Services.Any(s => s.State == ServiceState.DEGRADED) ? "DEGRADED" : "READY";
    public string OverallStatus => Services.Any(service => service.State == ServiceState.DOWN) ? "Có dịch vụ DOWN" : Services.Any(service => service.State == ServiceState.DEGRADED) ? "Đang suy giảm" : "Hệ thống ổn định";
    public int TotalJobsToday => Jobs.Count(job => job.Created?.LocalDateTime.Date == DateTime.Today);
    public int RunningJobs => Jobs.Count(job => IsStatus(job, "PROCESSING", "RUNNING", "ANALYZING"));
    public int QueuedJobs => Jobs.Count(job => IsStatus(job, "QUEUED", "WAITING"));
    public int CompletedJobs => Jobs.Count(job => IsStatus(job, "COMPLETED", "DONE"));
    public int FailedJobs => Jobs.Count(job => IsStatus(job, "FAILED", "ERROR"));
    public ICommand NavigateCommand { get; }
    public ICommand JobFilterCommand { get; }
    public ICommand RefreshCommand { get; }
    public ICommand ServiceStartCommand { get; }
    public ICommand ServiceToggleCommand { get; }
    public ICommand ServiceRestartCommand { get; }
    public ICommand ServiceHealthCommand { get; }
    public ICommand JobActionCommand { get; }
    public ICommand JobPrimaryActionCommand { get; }
    public ICommand JobSecondaryActionCommand { get; }
    public ICommand JobOpenOutputCommand { get; }
    public ICommand JobCancelCommand { get; }
    public ICommand OpenLogFolderCommand { get; }
    public ICommand SubmitManualJobCommand { get; }
    public ICommand CancelManualJobCommand { get; }
    public ICommand DiscoverChannelJobsCommand { get; }
    public ICommand SelectManualModeCommand { get; }
    public ICommand SelectChannelScoutModeCommand { get; }
    public ICommand ToggleChannelCommand { get; }
    public ICommand DashboardPagingCommand { get; }
    public bool DashboardJobsHasPages => ActiveJobs.Count > 5;
    public bool DashboardAlertsHasPages => Alerts.Count > 5;
    public bool DashboardChannelsHasPages => Channels.Count > 5;
    public bool DashboardJobsHasMorePages => DashboardJobsPageCount > 5;
    public bool DashboardAlertsHasMorePages => DashboardAlertsPageCount > 5;
    public bool DashboardChannelsHasMorePages => DashboardChannelsPageCount > 5;
    public int DashboardJobsPageCount => Math.Max(1, (int)Math.Ceiling(ActiveJobs.Count / 5d));
    public int DashboardAlertsPageCount => Math.Max(1, (int)Math.Ceiling(Alerts.Count / 5d));
    public int DashboardChannelsPageCount => Math.Max(1, (int)Math.Ceiling(Channels.Count / 5d));
    public IReadOnlyList<DashboardPageItem> DashboardJobPages => BuildDashboardPageItems(DashboardJobsPageCount, _dashboardJobsPage);
    public IReadOnlyList<DashboardPageItem> DashboardAlertPages => BuildDashboardPageItems(DashboardAlertsPageCount, _dashboardAlertsPage);
    public IReadOnlyList<DashboardPageItem> DashboardChannelPages => Enumerable.Range(0, Math.Min(5, DashboardChannelsPageCount)).Select(index => new DashboardPageItem(index + 1, index == _dashboardChannelsPage)).ToArray();
    public IReadOnlyList<string> DashboardChartDates => Enumerable.Range(0, 7).Select(index => DateTime.Today.AddDays(index - 6).ToString("dd/MM")).ToArray();
    public string DashboardJobsPageText => $"{_dashboardJobsPage + 1}";
    public string DashboardAlertsPageText => $"{_dashboardAlertsPage + 1}";
    public string DashboardChannelsPageText => $"{_dashboardChannelsPage + 1}";
    public event Action<MonitorAlert>? AlertRaised;

    public async Task InitializeAsync()
    {
        Config = await _configStore.LoadAsync();
        await EnsureServiceControlAsync();
        Alerts.Clear(); foreach (var alert in await _alertStore.LoadAsync()) Alerts.Add(alert);
        Navigate("Dashboard");
        await RefreshAsync();
        _syncCts = new CancellationTokenSource();
        _syncLoop = SyncLoopAsync(_syncCts.Token);
    }

    public void Navigate(string page)
    {
        CurrentPage = page;
        foreach (var item in NavigationItems)
            item.IsSelected = item.Page == page && (!item.IsChild || (page == "Jobs" && item.Label == JobFilter) || (page == "Manual" && item.Label == "Tạo Job mới"));
        if (page == "Jobs" && JobFilter == "Tất cả") NavigationItems.First(item => item.Label == "Jobs").IsSelected = true;
    }

    private void NavigateItem(NavigationItem? item)
    {
        if (item is null) return;
        if (item.IsGroup)
        {
            item.IsExpanded = !item.IsExpanded;
            foreach (var child in NavigationItems.Where(child => child.IsChild)) child.IsVisible = item.IsExpanded;
            return;
        }
        if (item.IsChild && item.Page == "Jobs") JobFilter = item.Label;
        Navigate(item.Page);
        if (item.IsChild && item.Page == "Jobs") RebuildVisibleJobs();
    }

    public async Task RefreshAsync()
    {
        if (!await _refreshGate.WaitAsync(0)) return;
        try
        {
            await _health.PollOnceAsync();
            var jobs = await _yt.GetJobsAsync();
            var manualJobs = await _lan.GetJobsAsync(Config.ManualLanToken);
            var allJobs = (jobs.Success ? jobs.Value! : Array.Empty<JobRecord>())
                .Concat(manualJobs.Success ? manualJobs.Value! : Array.Empty<JobRecord>())
                .ToArray();
            if (jobs.Success || manualJobs.Success)
            {
                Jobs.Clear(); foreach (var job in allJobs) Jobs.Add(job);
                JobsPage.ReplaceJobs(allJobs);
                RebuildVisibleJobs(applyJobsPageFilter: false);
                RebuildDashboardPages();
                OnPropertyChanged(nameof(TotalJobsToday)); OnPropertyChanged(nameof(RunningJobs)); OnPropertyChanged(nameof(QueuedJobs)); OnPropertyChanged(nameof(CompletedJobs)); OnPropertyChanged(nameof(FailedJobs));
            }
            var channels = await _yt.GetChannelsAsync();
            if (channels.Success)
            {
                Channels.Clear(); foreach (var channel in channels.Value!) Channels.Add(channel);
                ChannelsPage.ReplaceChannels(channels.Value!);
            }
            RebuildDashboardPages();
            LastUpdated = $"Cập nhật {DateTime.Now:HH:mm:ss}";
        }
        finally { _refreshGate.Release(); }
    }

    private async Task SyncLoopAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                await Task.Delay(LiveSyncInterval, cancellationToken);
                if (IsLiveObservationPage) await RefreshAsync();
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
    }

    private void ApplyJobFilter(string filter) { JobFilter = filter; RebuildVisibleJobs(); Navigate("Jobs"); }

    private void RebuildVisibleJobs(bool applyJobsPageFilter = true)
    {
        var rows = JobFilter switch
        {
            "Đang chạy" => Jobs.Where(job => IsStatus(job, "PROCESSING", "RUNNING", "ANALYZING")),
            "Chờ xử lý" => Jobs.Where(job => IsStatus(job, "QUEUED", "WAITING")),
            "Đã hoàn thành" => Jobs.Where(job => IsStatus(job, "COMPLETED", "DONE")),
            "Thất bại" => Jobs.Where(job => IsStatus(job, "FAILED", "ERROR", "INTERRUPTED")),
            "AUTO" => Jobs.Where(job => string.Equals(job.Origin, "AUTO", StringComparison.OrdinalIgnoreCase)),
            "MANUAL" => Jobs.Where(job => string.Equals(job.Origin, "MANUAL", StringComparison.OrdinalIgnoreCase)),
            _ => Jobs
        };
        ActiveJobs.Clear(); foreach (var row in Jobs.Where(job => IsStatus(job, "PROCESSING", "RUNNING", "ANALYZING", "QUEUED", "WAITING"))) ActiveJobs.Add(row);
        VisibleJobs.Clear(); foreach (var row in rows) VisibleJobs.Add(row);
        if (applyJobsPageFilter) JobsPage.ApplyNavigationFilter(JobFilter);
        OnPropertyChanged(nameof(PageTitle));
        OnPropertyChanged(nameof(PageSubtitle));
        OnPropertyChanged(nameof(ActiveJobs));
        OnPropertyChanged(nameof(VisibleJobs));
        RebuildDashboardPages();
    }

    private void ChangeDashboardPage(string? command)
    {
        if (string.IsNullOrWhiteSpace(command)) return;
        var parts = command.Split(':', 2);
        if (parts.Length != 2) return;
        var page = parts[0];
        if (parts[1].StartsWith("page:", StringComparison.OrdinalIgnoreCase) && int.TryParse(parts[1][5..], out var selectedPage))
        {
            if (page == "Jobs") _dashboardJobsPage = ClampPage(selectedPage - 1, ActiveJobs.Count);
            if (page == "Alerts") _dashboardAlertsPage = ClampPage(selectedPage - 1, Alerts.Count);
            if (page == "Channels") _dashboardChannelsPage = ClampPage(selectedPage - 1, Channels.Count);
        }
        else
        {
            var direction = parts[1].Equals("next", StringComparison.OrdinalIgnoreCase) ? 1 : -1;
            if (page == "Jobs") _dashboardJobsPage = ClampPage(_dashboardJobsPage + direction, ActiveJobs.Count);
            if (page == "Alerts") _dashboardAlertsPage = ClampPage(_dashboardAlertsPage + direction, Alerts.Count);
            if (page == "Channels") _dashboardChannelsPage = ClampPage(_dashboardChannelsPage + direction, Channels.Count);
        }
        RebuildDashboardPages();
    }

    private void RebuildDashboardPages()
    {
        FillPage(DashboardActiveJobs, ActiveJobs, ref _dashboardJobsPage);
        FillPage(DashboardAlerts, Alerts, ref _dashboardAlertsPage);
        FillPage(DashboardChannels, Channels, ref _dashboardChannelsPage);
        OnPropertyChanged(nameof(DashboardActiveJobs)); OnPropertyChanged(nameof(DashboardAlerts));
        OnPropertyChanged(nameof(DashboardJobsHasPages)); OnPropertyChanged(nameof(DashboardAlertsHasPages)); OnPropertyChanged(nameof(DashboardChannelsHasPages));
        OnPropertyChanged(nameof(DashboardJobsPageText)); OnPropertyChanged(nameof(DashboardAlertsPageText)); OnPropertyChanged(nameof(DashboardChannelsPageText));
        OnPropertyChanged(nameof(DashboardJobsPageCount)); OnPropertyChanged(nameof(DashboardAlertsPageCount)); OnPropertyChanged(nameof(DashboardChannelsPageCount));
        OnPropertyChanged(nameof(DashboardJobsHasMorePages)); OnPropertyChanged(nameof(DashboardAlertsHasMorePages)); OnPropertyChanged(nameof(DashboardChannelsHasMorePages));
        OnPropertyChanged(nameof(DashboardJobPages)); OnPropertyChanged(nameof(DashboardAlertPages)); OnPropertyChanged(nameof(DashboardChannelPages)); OnPropertyChanged(nameof(DashboardChannels));
    }

    private static IReadOnlyList<DashboardPageItem> BuildDashboardPageItems(int pageCount, int currentPage) =>
        Enumerable.Range(0, Math.Min(5, pageCount)).Select(index => new DashboardPageItem(index + 1, index == currentPage)).ToArray();

    private static void FillPage<T>(ObservableCollection<T> target, IEnumerable<T> source, ref int page)
    {
        var totalPages = Math.Max(1, (int)Math.Ceiling(source.Count() / 5d));
        page = Math.Clamp(page, 0, totalPages - 1);
        target.Clear(); foreach (var item in source.Skip(page * 5).Take(5)) target.Add(item);
    }

    private static int ClampPage(int page, int count) => Math.Clamp(page, 0, Math.Max(0, (int)Math.Ceiling(count / 5d) - 1));

    private async Task SubmitManualJobAsync()
    {
        var urls = ManualUrls;
        var submitted = 0;
        var errors = new List<string>();
        IsBusy = true;
        try
        {
            foreach (var url in urls)
            {
                var result = await _lan.CreateJobAsync(url);
                if (result.Success) submitted++;
                else errors.Add(result.Error ?? $"Không gửi được {url}");
            }
            ManualMessage = errors.Count == 0
                ? $"Đã gửi {submitted} job vào hàng đợi."
                : $"Đã gửi {submitted}/{urls.Count} job. {errors[0]}";
        }
        finally { IsBusy = false; }
        if (submitted > 0) await RefreshAsync();
    }

    private async Task DiscoverChannelJobsAsync()
    {
        var channels = ChannelScoutChannels;
        ChannelScoutMessage = $"Đang quét {channels.Count} kênh… Job sẽ được xếp ngay sau khi tìm thấy video.";
        IsBusy = true;
        var shouldRefresh = false;
        try
        {
            var result = await _lan.DiscoverJobsAsync(channels);
            if (!result.Success || result.Value is null)
            {
                ChannelScoutMessage = result.Error ?? "Không thể quét các kênh.";
                return;
            }
            var created = result.Value.Created.Count;
            var skipped = result.Value.Skipped.Count;
            var errors = result.Value.Errors.Count;
            ChannelScoutMessage = $"Đã xếp {created} job. Bỏ qua {skipped}, lỗi {errors}.";
            shouldRefresh = created > 0;
        }
        catch (Exception error)
        {
            ChannelScoutMessage = $"Quét thất bại: {error.Message}";
        }
        finally { IsBusy = false; }
        if (shouldRefresh) await RefreshAsync();
    }

    private async Task LoadManualMetadataAsync(string url)
    {
        var cts = new CancellationTokenSource();
        _manualMetadataCts = cts;
        try
        {
            await Task.Delay(300, cts.Token);
            var result = await _lan.GetMetadataAsync(url, cts.Token);
            if (cts.IsCancellationRequested) return;
            if (!result.Success || result.Value is null)
            {
                ManualPreviewMessage = "Không lấy được metadata; vẫn có thể gửi job bằng URL này.";
                return;
            }
            _manualPreviewMetadata = result.Value;
            OnPropertyChanged(nameof(ManualPreviewTitle));
            OnPropertyChanged(nameof(ManualPreviewChannel));
            OnPropertyChanged(nameof(ManualPreviewDuration));
            ManualPreviewMessage = "Metadata đã cập nhật từ Manual LAN API";
        }
        catch (OperationCanceledException) { }
        catch
        {
            if (!cts.IsCancellationRequested) ManualPreviewMessage = "Không lấy được metadata; vẫn có thể gửi job bằng URL này.";
        }
        finally
        {
            if (ReferenceEquals(_manualMetadataCts, cts)) _manualMetadataCts = null;
            cts.Dispose();
        }
    }

    private async Task ToggleChannelAsync(ChannelRecord? channel)
    {
        if (channel is null) return;
        await _yt.SetChannelEnabledAsync(channel.Id, !channel.Enabled); await RefreshAsync();
    }

    private async void HandleBulkControlRequested(string kind, bool enabled, IReadOnlyList<string> channelIds)
    {
        IsBusy = true;
        try
        {
            await Task.WhenAll(channelIds.Select(channelId => kind == "Cut"
                ? _yt.SetChannelCutEnabledAsync(channelId, enabled)
                : _yt.SetChannelEnabledAsync(channelId, enabled)));
        }
        finally { IsBusy = false; }
        await RefreshAsync();
    }

    private async void HandleAddChannelsRequested(IReadOnlyList<ChannelDraft> drafts)
    {
        IsBusy = true;
        try
        {
            var bulk = await _yt.AddChannelsBulkAsync(drafts.Select(draft => draft.ChannelUrl).ToArray());
            if (!bulk.Success || bulk.Value is null)
            {
                ShowChannelsMessage($"Không thêm được kênh: {bulk.Error ?? "API bulk không phản hồi."}");
                return;
            }

            var names = drafts.ToDictionary(draft => draft.ChannelUrl, draft => draft.ChannelName, StringComparer.OrdinalIgnoreCase);
            var renameTasks = bulk.Value.Results
                .Where(item => item.Status.Equals("ADDED", StringComparison.OrdinalIgnoreCase)
                    && !string.IsNullOrWhiteSpace(item.ChannelId)
                    && names.TryGetValue(item.Input, out _))
                .Select(item => _yt.RenameChannelAsync(item.ChannelId!, names[item.Input]));
            var renameResults = await Task.WhenAll(renameTasks);
            var renameFailed = renameResults.Count(result => !result.Success);
            var firstFailure = bulk.Value.Results.FirstOrDefault(item => item.Status.Equals("FAILED", StringComparison.OrdinalIgnoreCase));
            ShowChannelsMessage(bulk.Value.Failed == 0 && renameFailed == 0
                ? $"Đã thêm {bulk.Value.Added} kênh."
                : $"Đã thêm {bulk.Value.Added}/{drafts.Count} kênh. Lỗi {bulk.Value.Failed + renameFailed}. {firstFailure?.Error ?? "Một số thao tác chưa hoàn tất."}");
        }
        finally { IsBusy = false; }
        await RefreshAsync();
    }

    private async void HandleDeleteChannelsRequested(IReadOnlyList<string> channelIds)
    {
        if (channelIds.Count == 0) return;
        IsBusy = true;
        try
        {
            var results = await Task.WhenAll(channelIds.Select(channelId => _yt.DeleteChannelAsync(channelId)));
            var deleted = results.Count(result => result.Success);
            ShowChannelsMessage(deleted == channelIds.Count
                ? $"Đã xóa {deleted} kênh đã chọn."
                : $"Đã xóa {deleted}/{channelIds.Count} kênh; một số kênh bị từ chối.");
        }
        finally { IsBusy = false; }
        if (ChannelsPage.IsMultiSelect) ChannelsPage.ToggleMultiSelect();
        await RefreshAsync();
    }

    public async ValueTask DisposeAsync()
    {
        _manualMetadataCts?.Cancel();
        _manualMetadataCts?.Dispose();
        _channelsMessageCts?.Cancel();
        _channelsMessageCts?.Dispose();
        _syncCts?.Cancel();
        if (_syncLoop is not null)
        {
            try { await _syncLoop; } catch (OperationCanceledException) { }
        }
        _syncCts?.Dispose();
        _refreshGate.Dispose();
        await _health.DisposeAsync();
        _apiClient.Dispose();
    }

    private void ShowChannelsMessage(string message)
    {
        ChannelsMessage = message;
        IsChannelsMessageVisible = true;
        _channelsMessageCts?.Cancel();
        var cts = new CancellationTokenSource();
        _channelsMessageCts = cts;
        _ = HideChannelsMessageAsync(cts);
    }

    private async Task HideChannelsMessageAsync(CancellationTokenSource cts)
    {
        try
        {
            await Task.Delay(TimeSpan.FromSeconds(5), cts.Token);
            if (!cts.IsCancellationRequested) IsChannelsMessageVisible = false;
        }
        catch (OperationCanceledException) { }
        finally { cts.Dispose(); }
    }

    private async Task EnsureServiceControlAsync()
    {
        var probe = await _serviceControl.GetServicesAsync();
        if (probe.Success) return;
        var script = new[]
        {
            Path.Combine(AppContext.BaseDirectory, "contentops_service_control.py"),
            Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "contentops_service_control.py"),
            @"D:\Silence_cutter\contentops_service_control.py"
        }.Select(Path.GetFullPath).FirstOrDefault(File.Exists);
        if (!File.Exists(script)) return;
        var python = new[]
        {
            @"D:\Silence_cutter\.venv\Scripts\python.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "AppData", "Local", "Programs", "Python", "Python311", "python.exe")
        }.FirstOrDefault(File.Exists);
        if (!File.Exists(python)) return;
        Process.Start(new ProcessStartInfo(python, $"\"{Path.GetFullPath(script)}\" 8794") { WorkingDirectory = Path.GetDirectoryName(Path.GetFullPath(script))!, UseShellExecute = false, CreateNoWindow = true });
        for (var attempt = 0; attempt < 12; attempt++)
        {
            await Task.Delay(250);
            if ((await _serviceControl.GetServicesAsync()).Success) return;
        }
    }

    private async Task ControlServiceAsync(string? name, string action)
    {
        if (string.IsNullOrWhiteSpace(name) || IsBusy) return;
        IsBusy = true;
        try
        {
            var result = action switch
            {
                "start" => await _serviceControl.StartAsync(name),
                "stop" => await _serviceControl.StopAsync(name),
                "restart" => await _serviceControl.RestartAsync(name),
                _ => ApiResult<System.Text.Json.JsonElement>.Failed("Unsupported service action")
            };
            LastUpdated = result.Success ? $"{name}: đã {action}" : $"{name}: {result.Error}";
            await _health.PollOnceAsync();
        }
        finally { IsBusy = false; }
    }

    private Task ToggleServiceAsync(ServiceSnapshot? snapshot) => snapshot is null
        ? Task.CompletedTask
        : ControlServiceAsync(snapshot.Name, snapshot.State is ServiceState.DOWN or ServiceState.UNKNOWN ? "start" : "stop");

    private async Task ExecuteJobActionAsync(JobActionRequest? request)
    {
        if (request?.Job is null || IsBusy) return;
        var row = request.Job;
        var action = JobActionResolver.Resolve(request.Action, row);
        if (action == "copy-url")
        {
            if (!string.IsNullOrWhiteSpace(row.Job.VideoUrl)) System.Windows.Clipboard.SetText(row.Job.VideoUrl);
            LastUpdated = "Đã sao chép URL job.";
            return;
        }
        if (action == "open-output")
        {
            if (!string.IsNullOrWhiteSpace(row.Job.Output))
            {
                var output = row.Job.Output;
                var target = Directory.Exists(output) ? output : Path.GetDirectoryName(output);
                if (!string.IsNullOrWhiteSpace(target))
                {
                    Process.Start(new ProcessStartInfo("explorer.exe", $"\"{target}\"") { UseShellExecute = true });
                    LastUpdated = "Đã mở thư mục output.";
                }
                else LastUpdated = "Không xác định được thư mục output.";
            }
            else LastUpdated = "Job chưa có output.";
            return;
        }
        IsBusy = true;
        try
        {
            var useManual = row.Job.SourceService?.Contains("Manual", StringComparison.OrdinalIgnoreCase) == true;
            var result = action switch
            {
                "cancel" => useManual ? await _lan.CancelJobAsync(row.Id) : await _yt.CancelJobAsync(row.Id),
                "retry" => useManual ? await _lan.RetryJobAsync(row.Id) : await _yt.RetryJobAsync(row.Id),
                "delete" => useManual ? await _lan.DeleteJobAsync(row.Id) : await _yt.DeleteJobAsync(row.Id),
                _ => ApiResult<System.Text.Json.JsonElement>.Failed("Không hỗ trợ thao tác job")
            };
            LastUpdated = result.Success ? $"Đã thực hiện: {row.PrimaryActionText}" : $"Không thực hiện được: {result.Error}";
        }
        finally { IsBusy = false; }
        await RefreshAsync();
    }

    private void OpenServiceHealth(string? name)
    {
        if (string.IsNullOrWhiteSpace(name) || !Config.Endpoints.TryGetValue(name, out var endpoint)) return;
        Process.Start(new ProcessStartInfo(endpoint.HealthUri.ToString()) { UseShellExecute = true });
    }

    private void OpenLogFolder()
    {
        var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ContentOps", "Monitor");
        Directory.CreateDirectory(folder);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{folder}\"") { UseShellExecute = true });
    }
    private static bool IsStatus(JobRecord job, params string[] statuses) => statuses.Contains(job.Status, StringComparer.OrdinalIgnoreCase);
    private IReadOnlyList<string> ManualUrls => SplitLines(ManualUrl);
    private IReadOnlyList<string> ChannelScoutChannels => SplitLines(ChannelScoutInput);
    private bool AreManualUrlsValid() => ManualUrls.Count > 0 && ManualUrls.All(IsValidManualUrl);
    private bool AreChannelScoutUrlsValid() => ChannelScoutChannels.Count > 0 && ChannelScoutChannels.All(IsValidChannelUrl);
    private static IReadOnlyList<string> SplitLines(string value) => value.Split(["\r\n", "\n", "\r"], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    private static bool IsValidManualUrl(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri is not null
        && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps)
        && (uri.Host.Equals("youtu.be", StringComparison.OrdinalIgnoreCase)
            || uri.Host.Equals("youtube.com", StringComparison.OrdinalIgnoreCase)
            || uri.Host.EndsWith(".youtube.com", StringComparison.OrdinalIgnoreCase));
    private static bool IsValidChannelUrl(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri is not null
        && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps)
        && (uri.Host.Equals("youtube.com", StringComparison.OrdinalIgnoreCase)
            || uri.Host.Equals("www.youtube.com", StringComparison.OrdinalIgnoreCase)
            || uri.Host.Equals("m.youtube.com", StringComparison.OrdinalIgnoreCase));

    private void UpdateServices(IReadOnlyList<ServiceSnapshot> snapshots) => OnUi(() =>
    {
        Services.Clear(); foreach (var snapshot in snapshots) Services.Add(snapshot);
        DashboardServices.Clear(); foreach (var snapshot in snapshots.OrderBy(snapshot => snapshot.Name switch { "YT_NOTIFI" => 0, "YTDOWNLOAD" => 1, "Silence Scheduler" => 2, "Qwen" => 3, "Manual LAN API" => 4, _ => 5 })) DashboardServices.Add(snapshot);
        OnPropertyChanged(nameof(Services)); OnPropertyChanged(nameof(DashboardServices)); OnPropertyChanged(nameof(HealthyCount)); OnPropertyChanged(nameof(HealthSummary)); OnPropertyChanged(nameof(HealthState)); OnPropertyChanged(nameof(OverallStatus));
    });

    private void AddAlert(MonitorAlert alert) => OnUi(() =>
    {
        Alerts.Insert(0, alert); RebuildDashboardPages(); _ = _alertStore.AppendAsync(alert); OnPropertyChanged(nameof(AlertCount)); AlertRaised?.Invoke(alert);
    });

    private static void OnUi(Action action)
    {
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is not null && !dispatcher.CheckAccess()) dispatcher.Invoke(action); else action();
    }
}

public sealed record DashboardPageItem(int Number, bool IsSelected);
public sealed record JobActionRequest(JobRowViewModel Job, string Action);

public static class JobActionResolver
{
    public static string Resolve(string requested, JobRowViewModel row) => requested switch
    {
        "primary" => row.StatusKind switch
        {
            "Queued" => "cancel",
            "Completed" => "open-output",
            "Failed" or "Interrupted" => "retry",
            _ => "cancel"
        },
        "secondary" => row.StatusKind switch
        {
            "Queued" => "delete",
            "Completed" => "copy-url",
            "Failed" or "Interrupted" => "delete",
            _ => "cancel"
        },
        "cancel" when row.StatusKind is "Completed" or "Failed" or "Interrupted" => "delete",
        _ => requested
    };
}
