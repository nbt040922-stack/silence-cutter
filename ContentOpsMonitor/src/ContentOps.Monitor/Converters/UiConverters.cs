using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Converters;

public sealed class StateBrushConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) => value?.ToString()?.ToUpperInvariant() switch
    {
        "READY" => new SolidColorBrush(System.Windows.Media.Color.FromRgb(44, 166, 119)),
        "DEGRADED" => new SolidColorBrush(System.Windows.Media.Color.FromRgb(181, 126, 45)),
        "DOWN" => new SolidColorBrush(System.Windows.Media.Color.FromRgb(179, 69, 84)),
        "STARTING" => new SolidColorBrush(System.Windows.Media.Color.FromRgb(62, 130, 187)),
        _ => new SolidColorBrush(System.Windows.Media.Color.FromRgb(77, 95, 118))
    };

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => System.Windows.Data.Binding.DoNothing;
}

public sealed class SeverityBrushConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) => value switch
    {
        AlertSeverity.CRITICAL or AlertSeverity.ERROR => new SolidColorBrush(System.Windows.Media.Color.FromRgb(179, 69, 84)),
        AlertSeverity.WARNING => new SolidColorBrush(System.Windows.Media.Color.FromRgb(181, 126, 45)),
        _ => new SolidColorBrush(System.Windows.Media.Color.FromRgb(62, 130, 187))
    };

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => System.Windows.Data.Binding.DoNothing;
}

public sealed class ActiveNavBrushConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        string.Equals(value?.ToString(), parameter?.ToString(), StringComparison.OrdinalIgnoreCase)
            ? new SolidColorBrush(System.Windows.Media.Color.FromRgb(27, 60, 91)) : System.Windows.Media.Brushes.Transparent;

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => System.Windows.Data.Binding.DoNothing;
}

public sealed class EmptyCollectionVisibilityConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is System.Collections.ICollection collection && collection.Count == 0 ? System.Windows.Visibility.Visible : System.Windows.Visibility.Collapsed;
    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => System.Windows.Data.Binding.DoNothing;
}

public sealed class BoolVisibilityConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is bool visible && visible ? System.Windows.Visibility.Visible : System.Windows.Visibility.Collapsed;
    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => System.Windows.Data.Binding.DoNothing;
}
