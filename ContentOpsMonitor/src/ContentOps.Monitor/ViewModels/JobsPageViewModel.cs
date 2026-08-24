using System.Collections.ObjectModel;
using System.Windows.Input;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.ViewModels;

public sealed class JobsPageViewModel : ViewModelBase
{
    private const int PageSize = 10;
    private readonly List<JobRowViewModel> _rows = [];
    private readonly bool _usePreviewData;
    private string _statusFilter = "Tất cả";
    private string _typeFilter = "Tất cả";
    private string _searchText = string.Empty;
    private int _currentPage = 1;
    private JobRowViewModel? _selectedJob;
    private string _statusViewKind = string.Empty;

    public JobsPageViewModel(bool usePreviewData = true)
    {
        _usePreviewData = usePreviewData;
        SelectJobCommand = new RelayCommand(value => SelectedJob = value as JobRowViewModel);
        PagingCommand = new RelayCommand(value => ChangePage(value?.ToString()));
        ReplaceJobs([]);
    }

    public ObservableCollection<JobRowViewModel> VisibleRows { get; } = [];
    public ICommand SelectJobCommand { get; }
    public ICommand PagingCommand { get; }
    public IReadOnlyList<string> StatusOptions { get; } = ["Tất cả", "Đang chạy", "Chờ xử lý", "Hoàn thành", "Thất bại"];
    public IReadOnlyList<string> TypeOptions { get; } = ["Tất cả", "AUTO", "MANUAL"];
    public IReadOnlyList<string> ChannelOptions => ["Tất cả kênh"];

    public string StatusFilter
    {
        get => _statusFilter;
        set
        {
            if (!Set(ref _statusFilter, value)) return;
            SetStatusView(value);
            ResetAndRebuild();
        }
    }

    public string TypeFilter
    {
        get => _typeFilter;
        set { if (Set(ref _typeFilter, value)) ResetAndRebuild(); }
    }

    public string SearchText
    {
        get => _searchText;
        set { if (Set(ref _searchText, value)) ResetAndRebuild(); }
    }

    public JobRowViewModel? SelectedJob { get => _selectedJob; private set => Set(ref _selectedJob, value); }
    public bool IsPreviewData { get; private set; }
    public bool IsRunningView => _statusViewKind == "Running";
    public bool IsStatusView => _statusViewKind.Length > 0;
    public string PageTitle => _statusViewKind switch
    {
        "Running" => "Đang chạy (Running Jobs)",
        "Queued" => "Chờ xử lý (Queued Jobs)",
        "Completed" => "Đã hoàn thành (Completed Jobs)",
        "Failed" => "Thất bại (Failed Jobs)",
        _ => "Jobs"
    };
    public string PageSubtitle => _statusViewKind switch
    {
        "Running" => "Quản lý và theo dõi các tác vụ nội dung đang được xử lý",
        "Queued" => "Theo dõi các tác vụ đang chờ trong hàng đợi xử lý",
        "Completed" => "Tra cứu các tác vụ đã xử lý và đầu ra hoàn thành",
        "Failed" => "Kiểm tra lỗi và các tác vụ xử lý không thành công",
        _ => "Quản lý và theo dõi các tác vụ xử lý nội dung"
    };
    public string MetricOneTitle => _statusViewKind switch { "Queued" => "Chờ xử lý", "Completed" => "Đã hoàn thành", "Failed" => "Thất bại", _ => "Đang chạy" };
    public string MetricOneGlyph => _statusViewKind switch { "Queued" => "\uE823", "Completed" => "\uE73E", "Failed" => "\uE711", _ => "\uE768" };
    public string MetricOneBackground => _statusViewKind switch { "Queued" => "#4A3715", "Completed" => "#39275E", "Failed" => "#51222D", _ => "#0B503A" };
    public string MetricOneForeground => _statusViewKind switch { "Queued" => "#FFB52E", "Completed" => "#B994FF", "Failed" => "#FF6278", _ => "#36E0A0" };
    public string MetricOneValue => IsStatusView ? _rows.Count.ToString() : "0";
    public string MetricOneCaption => _statusViewKind switch { "Queued" => "job trong hàng đợi", "Completed" => "job đã hoàn tất", "Failed" => "job cần kiểm tra", _ => "job đang xử lý" };
    public string MetricTwoTitle => _statusViewKind switch { "Queued" => "Thời gian chờ TB", "Completed" => "Tỷ lệ hoàn tất", "Failed" => "Có thể thử lại", _ => "Tổng tiến độ TB" };
    public string MetricTwoValue => _statusViewKind switch { "Queued" => "00:08:20", "Completed" => "100%", "Failed" => "2", "Running" => "56%", _ => "--" };
    public string MetricTwoCaption => _statusViewKind switch { "Queued" => "thời gian chờ trung bình", "Completed" => "trong danh sách hiện tại", "Failed" => "job đủ điều kiện", _ => "tiến độ trung bình" };
    public string MetricThreeTitle => _statusViewKind switch { "Queued" => "Vị trí hàng đợi", "Completed" => "Thời gian xử lý TB", "Failed" => "Giai đoạn lỗi nhiều", _ => "Thời gian chạy TB" };
    public string MetricThreeValue => _statusViewKind switch { "Queued" => "1 - 3", "Completed" => "00:14:26", "Failed" => "Silence", "Running" => "00:11:32", _ => "--" };
    public string MetricThreeCaption => _statusViewKind switch { "Queued" => "thứ tự xử lý", "Completed" => "thời gian trung bình", "Failed" => "2 trong 3 job", _ => "thời gian trung bình" };
    public string MetricFourTitle => _statusViewKind switch { "Queued" => "Dự kiến bắt đầu", "Completed" => "Dung lượng đầu ra", "Failed" => "Lỗi gần nhất", _ => "Ước tính hoàn thành" };
    public string MetricFourValue => _statusViewKind switch { "Queued" => "00:06:00", "Completed" => "468 MB", "Failed" => "08:42:10", "Running" => "00:21:48", _ => "--" };
    public string MetricFourCaption => _statusViewKind switch { "Queued" => "bắt đầu job kế tiếp", "Completed" => "tổng dung lượng", "Failed" => "thời điểm ghi nhận", _ => "hoàn thành trung bình" };
    public string AverageProgressText => MetricTwoValue;
    public string AverageElapsedText => MetricThreeValue;
    public string AverageEtaText => MetricFourValue;
    public string DurationColumnTitle => _statusViewKind switch { "Queued" => "Thời gian chờ", "Completed" => "Thời gian xử lý", _ => "Thời gian chạy" };
    public string EstimateColumnTitle => _statusViewKind switch { "Queued" => "Dự kiến bắt đầu", "Completed" => "Hoàn thành lúc", "Failed" => "Lỗi lúc", _ => "Ước tính hoàn thành" };
    public int TotalCount => _rows.Count;
    public int RunningCount => _rows.Count(row => row.StatusKind == "Running");
    public int QueuedCount => _rows.Count(row => row.StatusKind == "Queued");
    public int CompletedCount => _rows.Count(row => row.StatusKind == "Completed");
    public int FailedCount => _rows.Count(row => row.StatusKind == "Failed");
    public int FilteredCount => FilteredRows.Count();
    public int PageCount => Math.Max(1, (int)Math.Ceiling(FilteredCount / (double)PageSize));
    public int CurrentPage => _currentPage;
    public string RangeText => FilteredCount == 0 ? "Hiển thị 0 job" : $"Hiển thị {((_currentPage - 1) * PageSize) + 1} - {((_currentPage - 1) * PageSize) + VisibleRows.Count} của {FilteredCount} job";
    public IReadOnlyList<JobPageItem> PageItems => Enumerable.Range(1, PageCount).Select(page => new JobPageItem(page, page == _currentPage)).ToArray();

    public void ReplaceJobs(IEnumerable<JobRecord> jobs)
    {
        var records = jobs.ToArray();
        IsPreviewData = _usePreviewData || records.Length == 0;
        _rows.Clear();
        if (IsPreviewData && IsStatusView) _rows.AddRange(StatusPreviewJobs(_statusViewKind));
        else _rows.AddRange((IsPreviewData ? PreviewJobs() : records).Select(record => new JobRowViewModel(record)));
        _currentPage = 1;
        RebuildPage();
        NotifySummary();
    }

    public void ApplyNavigationFilter(string filter)
    {
        SetStatusView(filter);
        if (filter is "AUTO" or "MANUAL")
        {
            _statusFilter = "Tất cả";
            _typeFilter = filter;
        }
        else
        {
            _typeFilter = "Tất cả";
            _statusFilter = filter == "Đã hoàn thành" ? "Hoàn thành" : filter;
        }
        OnPropertyChanged(nameof(StatusFilter));
        OnPropertyChanged(nameof(TypeFilter));
        ResetAndRebuild();
    }

    private void SetStatusView(string filter)
    {
        var kind = filter switch
        {
            "Đang chạy" => "Running",
            "Chờ xử lý" => "Queued",
            "Đã hoàn thành" or "Hoàn thành" => "Completed",
            "Thất bại" => "Failed",
            _ => string.Empty
        };
        if (_statusViewKind == kind) return;
        _statusViewKind = kind;
        if (IsPreviewData)
        {
            _rows.Clear();
            if (IsStatusView) _rows.AddRange(StatusPreviewJobs(_statusViewKind));
            else _rows.AddRange(PreviewJobs().Select(record => new JobRowViewModel(record)));
        }
        OnPropertyChanged(nameof(IsRunningView));
        OnPropertyChanged(nameof(IsStatusView));
        OnPropertyChanged(nameof(PageTitle));
        OnPropertyChanged(nameof(PageSubtitle));
        OnPropertyChanged(nameof(MetricOneTitle)); OnPropertyChanged(nameof(MetricOneGlyph)); OnPropertyChanged(nameof(MetricOneBackground)); OnPropertyChanged(nameof(MetricOneForeground)); OnPropertyChanged(nameof(MetricOneValue)); OnPropertyChanged(nameof(MetricOneCaption));
        OnPropertyChanged(nameof(MetricTwoTitle)); OnPropertyChanged(nameof(MetricTwoValue)); OnPropertyChanged(nameof(MetricTwoCaption));
        OnPropertyChanged(nameof(MetricThreeTitle)); OnPropertyChanged(nameof(MetricThreeValue)); OnPropertyChanged(nameof(MetricThreeCaption));
        OnPropertyChanged(nameof(MetricFourTitle)); OnPropertyChanged(nameof(MetricFourValue)); OnPropertyChanged(nameof(MetricFourCaption));
        OnPropertyChanged(nameof(AverageProgressText));
        OnPropertyChanged(nameof(AverageElapsedText));
        OnPropertyChanged(nameof(AverageEtaText));
        OnPropertyChanged(nameof(DurationColumnTitle));
        OnPropertyChanged(nameof(EstimateColumnTitle));
        NotifySummary();
    }

    private IEnumerable<JobRowViewModel> FilteredRows => _rows
        .Where(row => _statusFilter switch
        {
            "Đang chạy" => row.StatusKind == "Running",
            "Chờ xử lý" => row.StatusKind == "Queued",
            "Hoàn thành" => row.StatusKind == "Completed",
            "Thất bại" => row.StatusKind is "Failed" or "Interrupted",
            _ => true
        })
        .Where(row => _typeFilter == "Tất cả" || row.Type.Equals(_typeFilter, StringComparison.OrdinalIgnoreCase))
        .Where(row => string.IsNullOrWhiteSpace(_searchText)
            || row.Title.Contains(_searchText.Trim(), StringComparison.OrdinalIgnoreCase)
            || row.Channel.Contains(_searchText.Trim(), StringComparison.OrdinalIgnoreCase)
            || row.Id.Contains(_searchText.Trim(), StringComparison.OrdinalIgnoreCase));

    private void ResetAndRebuild() { _currentPage = 1; RebuildPage(); }

    private void ChangePage(string? value)
    {
        if (value == "prev") _currentPage--;
        else if (value == "next") _currentPage++;
        else if (int.TryParse(value, out var page)) _currentPage = page;
        RebuildPage();
    }

    private void RebuildPage()
    {
        _currentPage = Math.Clamp(_currentPage, 1, PageCount);
        VisibleRows.Clear();
        foreach (var row in FilteredRows.Skip((_currentPage - 1) * PageSize).Take(PageSize)) VisibleRows.Add(row);
        if (SelectedJob is null || !VisibleRows.Contains(SelectedJob)) SelectedJob = VisibleRows.FirstOrDefault();
        OnPropertyChanged(nameof(FilteredCount));
        OnPropertyChanged(nameof(PageCount));
        OnPropertyChanged(nameof(CurrentPage));
        OnPropertyChanged(nameof(RangeText));
        OnPropertyChanged(nameof(PageItems));
    }

    private void NotifySummary()
    {
        OnPropertyChanged(nameof(IsPreviewData));
        OnPropertyChanged(nameof(TotalCount));
        OnPropertyChanged(nameof(RunningCount));
        OnPropertyChanged(nameof(QueuedCount));
        OnPropertyChanged(nameof(CompletedCount));
        OnPropertyChanged(nameof(FailedCount));
    }

    private static JobRecord[] PreviewJobs() =>
    [
        new("JOB_240824_0001", "Cách AI đang thay đổi thế giới (2024)", "https://youtu.be/preview-completed", "006US", "AUTO", "COMPLETED", "Output", 100, new DateTimeOffset(2024, 8, 24, 6, 45, 11, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 7, 25, 11, TimeSpan.FromHours(7)), "D:\\ContentOps\\output\\JOB_240824_0001", null, "YT_NOTIFI"),
        new("JOB_240824_0002", "ChatGPT-5 có gì mới? Hướng dẫn chi tiết", "https://youtu.be/preview-failed", "006US", "AUTO", "FAILED", "Silence", 64, new DateTimeOffset(2024, 8, 24, 5, 42, 18, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 6, 2, 44, TimeSpan.FromHours(7)), null, "Silence processing failed", "YT_NOTIFI"),
        new("JOB_240824_0003", "Thị trường chứng khoán nhận định tuần tới", "https://youtu.be/preview-queued", "006US", "MANUAL", "QUEUED", "Notify", 0, new DateTimeOffset(2024, 8, 24, 7, 24, 51, TimeSpan.FromHours(7)), null, null, null, "Manual LAN API")
    ];

    private static JobRowViewModel[] RunningPreviewJobs() =>
    [
        new(new("JOB_240824_0001", "Cách AI đang thay đổi thế giới (2024)", "https://youtu.be/preview-running-1", "006US", "AUTO", "RUNNING", "Output", 78, new DateTimeOffset(2024, 8, 24, 7, 12, 26, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 7, 25, 11, TimeSpan.FromHours(7)), null, null, "YT_NOTIFI"), "00:12:45", "07:38:20", "24/08/2024"),
        new(new("JOB_240824_0002", "10 Bí quyết quản lý thời gian hiệu quả", "https://youtu.be/preview-running-2", "007US", "AUTO", "RUNNING", "Output", 45, new DateTimeOffset(2024, 8, 24, 7, 16, 40, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 7, 25, 11, TimeSpan.FromHours(7)), null, null, "YT_NOTIFI"), "00:08:31", "07:35:10", "24/08/2024"),
        new(new("JOB_240824_0003", "Thị trường chứng khoán nhận định tuần tới", "https://youtu.be/preview-running-3", "008US", "AUTO", "RUNNING", "Output", 20, new DateTimeOffset(2024, 8, 24, 7, 20, 50, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 7, 25, 11, TimeSpan.FromHours(7)), null, null, "YT_NOTIFI"), "00:04:21", "07:31:50", "24/08/2024")
    ];

    private static JobRowViewModel[] StatusPreviewJobs(string statusKind) => statusKind switch
    {
        "Running" => RunningPreviewJobs(),
        "Queued" =>
        [
            new(new("JOB_240824_0101", "Podcast công nghệ số 42", null, "006US", "AUTO", "QUEUED", "Notify", 0, new DateTimeOffset(2024, 8, 24, 8, 30, 0, TimeSpan.FromHours(7)), null, null, null, "YT_NOTIFI"), "00:12:10", "08:48:00", "24/08/2024"),
            new(new("JOB_240824_0102", "Hành trình khám phá Việt Nam", null, "007US", "MANUAL", "QUEUED", "Notify", 0, new DateTimeOffset(2024, 8, 24, 8, 34, 0, TimeSpan.FromHours(7)), null, null, null, "Manual LAN API"), "00:08:20", "08:54:00", "24/08/2024"),
            new(new("JOB_240824_0103", "Tin tức AI nổi bật tuần này", null, "008US", "AUTO", "QUEUED", "Notify", 0, new DateTimeOffset(2024, 8, 24, 8, 39, 0, TimeSpan.FromHours(7)), null, null, null, "YT_NOTIFI"), "00:04:30", "09:00:00", "24/08/2024")
        ],
        "Completed" =>
        [
            new(new("JOB_240824_0201", "Cách AI đang thay đổi thế giới (2024)", null, "006US", "AUTO", "COMPLETED", "Output", 100, new DateTimeOffset(2024, 8, 24, 7, 10, 0, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 7, 25, 22, TimeSpan.FromHours(7)), "D:\\ContentOps\\output\\JOB_240824_0201", null, "YT_NOTIFI"), "00:15:22", "07:25:22", "24/08/2024"),
            new(new("JOB_240824_0202", "10 Bí quyết quản lý thời gian hiệu quả", null, "007US", "AUTO", "COMPLETED", "Output", 100, new DateTimeOffset(2024, 8, 24, 7, 35, 0, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 7, 47, 18, TimeSpan.FromHours(7)), "D:\\ContentOps\\output\\JOB_240824_0202", null, "YT_NOTIFI"), "00:12:18", "07:47:18", "24/08/2024"),
            new(new("JOB_240824_0203", "Thị trường chứng khoán nhận định tuần tới", null, "008US", "MANUAL", "COMPLETED", "Output", 100, new DateTimeOffset(2024, 8, 24, 8, 0, 0, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 8, 15, 38, TimeSpan.FromHours(7)), "D:\\ContentOps\\output\\JOB_240824_0203", null, "Manual LAN API"), "00:15:38", "08:15:38", "24/08/2024")
        ],
        "Failed" =>
        [
            new(new("JOB_240824_0301", "ChatGPT-5 có gì mới? Hướng dẫn chi tiết", null, "006US", "AUTO", "FAILED", "Silence", 64, new DateTimeOffset(2024, 8, 24, 8, 30, 0, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 8, 42, 10, TimeSpan.FromHours(7)), null, "Silence processing failed", "YT_NOTIFI"), "00:12:10", "08:42:10", "24/08/2024"),
            new(new("JOB_240824_0302", "Review camera hành trình mới nhất", null, "007US", "AUTO", "FAILED", "Download", 38, new DateTimeOffset(2024, 8, 24, 8, 10, 0, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 8, 18, 34, TimeSpan.FromHours(7)), null, "Download source unavailable", "YT_NOTIFI"), "00:08:34", "08:18:34", "24/08/2024"),
            new(new("JOB_240824_0303", "Tổng hợp tin công nghệ buổi sáng", null, "008US", "MANUAL", "FAILED", "Silence", 84, new DateTimeOffset(2024, 8, 24, 7, 50, 0, TimeSpan.FromHours(7)), new DateTimeOffset(2024, 8, 24, 8, 6, 42, TimeSpan.FromHours(7)), null, "Silence service unavailable", "Manual LAN API"), "00:16:42", "08:06:42", "24/08/2024")
        ],
        _ => []
    };
}

public sealed class JobRowViewModel(JobRecord job, string? elapsedText = null, string? estimatedCompletionTimeText = null, string? estimatedCompletionDateText = null)
{
    public JobRecord Job { get; } = job;
    public string Id => Job.Id;
    public string Title => string.IsNullOrWhiteSpace(Job.DisplayName) ? (string.IsNullOrWhiteSpace(Job.Title) ? "Job chưa có tiêu đề" : Job.Title) : Job.DisplayName;
    public string Channel => string.IsNullOrWhiteSpace(Job.Channel) ? "--" : Job.Channel;
    public string Type => string.IsNullOrWhiteSpace(Job.Origin) ? "--" : Job.Origin.ToUpperInvariant();
    public double Progress => Math.Clamp(Job.Progress ?? 0, 0, 100);
    public string ProgressText => $"{Progress:0}%";
    public string Stage => string.IsNullOrWhiteSpace(Job.Stage) ? "--" : Job.Stage;
    public string StatusKind => Job.Status?.ToUpperInvariant() switch
    {
        "PROCESSING" or "RUNNING" or "ANALYZING" => "Running",
        "QUEUED" or "WAITING" => "Queued",
        "COMPLETED" or "DONE" => "Completed",
        "FAILED" or "ERROR" => "Failed",
        "INTERRUPTED" or "CANCELLED" or "CANCELED" => "Interrupted",
        _ => "Unknown"
    };
    public string StatusText => StatusKind switch { "Running" => "ĐANG CHẠY", "Queued" => "CHỜ XỬ LÝ", "Completed" => "HOÀN THÀNH", "Failed" => "THẤT BẠI", "Interrupted" => "ĐÃ DỪNG", _ => "KHÔNG RÕ" };
    public string StatusBackground => StatusKind switch { "Running" => "#174C42", "Queued" => "#684711", "Completed" => "#493073", "Failed" => "#6B2531", "Interrupted" => "#344A62", _ => "#25384C" };
    public string StatusForeground => StatusKind switch { "Running" => "#36E0A0", "Queued" => "#FFB52E", "Completed" => "#B994FF", "Failed" => "#FF6278", "Interrupted" => "#A8C0D8", _ => "#91A7BE" };
    public string TypeBackground => Type == "MANUAL" ? "#684819" : "#153F67";
    public string TypeForeground => Type == "MANUAL" ? "#FFB84C" : "#5CB8FF";
    public string CreatedTimeText => Job.Created?.ToLocalTime().ToString("HH:mm:ss") ?? "--:--:--";
    public string CreatedDateText => Job.Created?.ToLocalTime().ToString("dd/MM/yyyy") ?? "--/--/----";
    public string ElapsedText => elapsedText ?? (Job.DurationSeconds is { } seconds ? TimeSpan.FromSeconds(Math.Max(0, seconds)).ToString(@"hh\:mm\:ss") : Job.Created is null ? "--:--:--" : ((Job.Updated ?? DateTimeOffset.Now) - Job.Created.Value) is var elapsed && elapsed >= TimeSpan.Zero ? elapsed.ToString(@"hh\:mm\:ss") : "--:--:--");
    public string EstimatedCompletionTimeText => estimatedCompletionTimeText ?? "--:--:--";
    public string EstimatedCompletionDateText => estimatedCompletionDateText ?? "--/--/----";
    public string UpdatedText => Job.Updated?.ToLocalTime().ToString("dd/MM/yyyy HH:mm:ss") ?? "--";
    public string OutputText => string.IsNullOrWhiteSpace(Job.Output) ? "--" : Job.Output;
    public string ErrorText => string.IsNullOrWhiteSpace(Job.Error) ? "Không có lỗi" : Job.Error;
    public string MetadataText
    {
        get
        {
            var lines = new List<string>();
            Add("Input", Job.InputMode);
            Add("URL", Job.VideoUrl);
            Add("Source", Job.SourcePath);
            Add("Report", Job.ReportPath);
            Add("PID", Job.ProcessId?.ToString());
            Add("Bắt đầu", Job.Started?.ToLocalTime().ToString("dd/MM/yyyy HH:mm:ss"));
            Add("Kết thúc", Job.Finished?.ToLocalTime().ToString("dd/MM/yyyy HH:mm:ss"));
            Add("ETA", Job.EtaSeconds is { } eta ? TimeSpan.FromSeconds(Math.Max(0, eta)).ToString(@"hh\:mm\:ss") : Job.EtaStatus);
            Add("Scheduler", Job.SchedulerState);
            return lines.Count == 0 ? "Metadata: --" : string.Join(Environment.NewLine, lines);

            void Add(string label, string? value)
            {
                if (!string.IsNullOrWhiteSpace(value)) lines.Add($"{label}: {value}");
            }
        }
    }
    public string PrimaryActionText => StatusKind switch { "Queued" => "Hủy khỏi hàng đợi", "Completed" => "Mở thư mục đầu ra", "Failed" or "Interrupted" => "Thử lại", _ => "Tạm dừng" };
    public string SecondaryActionText => StatusKind switch { "Queued" => "Xóa lịch sử", "Completed" => "Sao chép URL", "Failed" or "Interrupted" => "Xóa lịch sử", _ => "Dừng" };
    public string PrimaryActionGlyph => StatusKind switch { "Queued" => "✕", "Completed" => "▱", "Failed" or "Interrupted" => "↻", _ => "Ⅱ" };
    public string SecondaryActionGlyph => StatusKind switch { "Queued" => "⌫", "Completed" => "⧉", "Failed" or "Interrupted" => "⌫", _ => "□" };
    public string PipelineText => StatusKind switch
    {
        "Completed" => "Notify ✓  →  Download ✓  →  Silence ✓  →  Output ✓",
        "Failed" => "Notify ✓  →  Download ✓  →  Silence ✕  →  Output ○",
        "Running" => "Notify ✓  →  Download ✓  →  Silence ◉  →  Output ○",
        _ => "Notify ○  →  Download ○  →  Silence ○  →  Output ○"
    };
    public string PipelineColor => StatusKind switch { "Completed" => "#31D393", "Failed" => "#FF6278", "Running" => "#54A8FF", _ => "#7D94AB" };
    public string ThumbnailBrush => StatusKind switch { "Completed" => "#174365", "Failed" => "#4E2638", "Queued" => "#4A3A1C", _ => "#183B58" };
    public string ThumbnailGlyph => StatusKind switch { "Completed" => "\uE8B7", "Failed" => "\uE7BA", "Queued" => "\uE823", _ => "\uE768" };
}

public sealed record JobPageItem(int Number, bool IsSelected);
