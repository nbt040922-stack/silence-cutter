using ContentOps.Monitor.Configuration;
using ContentOps.Monitor.Models;

namespace ContentOps.Monitor.Tests;

public sealed class ConfigStoreTests
{
    [Fact]
    public async Task MissingConfigLoadsDefaultsAndSaveRoundTrips()
    {
        var path = Path.Combine(Path.GetTempPath(), $"contentops-config-{Guid.NewGuid():N}.json");
        try
        {
            var store = new MonitorConfigStore(path);
            var defaults = await store.LoadAsync();
            await store.SaveAsync(new MonitorConfig
            {
                NotificationsEnabled = false,
                PollInterval = defaults.PollInterval,
                Endpoints = defaults.Endpoints
            });
            var loaded = await store.LoadAsync();

            Assert.Equal(TimeSpan.FromSeconds(20), defaults.PollInterval);
            Assert.False(loaded.NotificationsEnabled);
            Assert.Equal(8787, loaded.Endpoints["YT_NOTIFI"].Port);
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }
}
