$ErrorActionPreference = "Stop"
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8792/health" -TimeoutSec 2
    Write-Host "Qwen Worker: $($health.status)"
    Write-Host "Model: $($health.model)"
    Write-Host "CUDA: $(if ($health.model_loaded) { 'loaded' } else { 'not loaded' })"
    Write-Host "Warm: $(if ($health.warmed_up) { 'yes' } else { 'no' })"
    Write-Host "Model loads: $($health.model_load_count)"
} catch {
    Write-Host "Qwen Worker: UNAVAILABLE"
    exit 1
}
