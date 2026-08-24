using ContentOps.Monitor.Models;
using System.IO;

namespace ContentOps.Monitor.Services.ProcessControl;

public sealed class OwnedProcessInspector
{
    public OwnedProcessInfo? TryGetOwnedProcess(ServiceDefinition service, MonitorConfig config)
    {
        // A configured executable identity is required before exposing destructive controls.
        // Port ownership alone is intentionally insufficient and no broad process-name match is used.
        if (!config.ManagedExecutablePaths.TryGetValue(service.Name, out var executablePath) || !File.Exists(executablePath))
            return null;
        return null;
    }
}
