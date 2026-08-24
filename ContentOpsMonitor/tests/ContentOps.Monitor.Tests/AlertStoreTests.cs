using ContentOps.Monitor.Models;
using ContentOps.Monitor.Services.Alerts;

namespace ContentOps.Monitor.Tests;

public sealed class AlertStoreTests
{
    [Fact]
    public async Task AlertStorePersistsAndReloadsRecentAlerts()
    {
        var path = Path.Combine(Path.GetTempPath(), $"contentops-alerts-{Guid.NewGuid():N}.json");
        try
        {
            var store = new AlertStore(path);
            await store.AppendAsync(new MonitorAlert(AlertSeverity.CRITICAL, "Qwen", "READY → DOWN", DateTimeOffset.UtcNow));

            var alerts = await store.LoadAsync();

            var alert = Assert.Single(alerts);
            Assert.Equal("Qwen", alert.Component);
            Assert.Equal(AlertSeverity.CRITICAL, alert.Severity);
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }
}
