$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stage = Join-Path $repo "internal_release_rc"
$payload = Join-Path $stage "Silence Cutter RC"
$runtime = Join-Path $payload "resources\runtime\python"
$bin = Join-Path $payload "resources\bin"
$models = Join-Path $payload "resources\models"

& (Join-Path $runtime "python.exe") -c "import torch, funasr, silero_vad, yt_dlp, yt_dlp_ejs; print(torch.__version__); print(yt_dlp.version.__version__)"
& (Join-Path $bin "ffmpeg.exe") -version | Select-Object -First 1
& (Join-Path $bin "deno.exe") --version | Select-Object -First 1

$critical = @(
    (Join-Path $payload "Silence Cutter.exe"),
    (Join-Path $runtime "python.exe"),
    (Join-Path $runtime "python311.dll"),
    (Join-Path $runtime "python311.zip"),
    (Join-Path $runtime "Lib\site-packages\torch\lib\torch_cuda.dll"),
    (Join-Path $runtime "Lib\site-packages\torch\lib\torch_cpu.dll"),
    (Join-Path $bin "ffmpeg.exe"),
    (Join-Path $bin "ffprobe.exe"),
    (Join-Path $bin "deno.exe"),
    (Join-Path $payload "resources\benchmark\hardware_benchmark.mp4")
)
$critical += Get-ChildItem -LiteralPath $models -Recurse -File | Select-Object -ExpandProperty FullName
$critical += Get-ChildItem -LiteralPath (Join-Path $payload "resources\app") -Recurse -File | Select-Object -ExpandProperty FullName
$manifest = foreach ($path in $critical) {
    $item = Get-Item -LiteralPath $path -ErrorAction Stop
    [pscustomobject]@{
        path = $item.FullName.Substring($payload.Length + 1)
        size = $item.Length
        sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $stage "RC_MANIFEST.json") -Encoding UTF8
Write-Host "RC critical-file manifest complete: $($manifest.Count) files"
