using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Input;

namespace ContentOps.Monitor.ViewModels;

public abstract class ViewModelBase : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;
    protected void OnPropertyChanged([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
    protected bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        OnPropertyChanged(name);
        return true;
    }
}

public sealed class RelayCommand(Action<object?> execute, Predicate<object?>? canExecute = null) : ICommand
{
    public event EventHandler? CanExecuteChanged;
    public bool CanExecute(object? parameter) => canExecute?.Invoke(parameter) ?? true;
    public void Execute(object? parameter) => execute(parameter);
    public void RaiseCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}

public sealed class NavigationItem(string label, string page, string glyph, bool isChild = false, bool isGroup = false, bool isVisible = true) : ViewModelBase
{
    private bool _isSelected;
    private bool _isExpanded = true;
    public string Label { get; } = label;
    public string Page { get; } = page;
    public string Glyph { get; } = glyph;
    public bool IsChild { get; } = isChild;
    public bool IsGroup { get; } = isGroup;
    public bool IsVisible { get => isVisible; set => Set(ref isVisible, value); }
    public bool IsExpanded { get => _isExpanded; set { if (Set(ref _isExpanded, value)) OnPropertyChanged(nameof(ChevronGlyph)); } }
    public string ChevronGlyph => IsExpanded ? "\uE70D" : "\uE76C";
    public Thickness NavMargin => IsChild ? new Thickness(20, 1, 0, 1) : new Thickness(0, 2, 0, 2);
    public bool IsSelected { get => _isSelected; set => Set(ref _isSelected, value); }
}
