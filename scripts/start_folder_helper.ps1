$ErrorActionPreference = "Stop"
$port = 8793
$prefix = "http://127.0.0.1:$port/"
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($prefix)
$listener.Start()
try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $response = $context.Response
        $response.Headers["Access-Control-Allow-Origin"] = "*"
        $response.Headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        $response.Headers["Access-Control-Allow-Headers"] = "Content-Type"
        if ($context.Request.HttpMethod -eq "OPTIONS") {
            $response.StatusCode = 204
            $body = ""
        } elseif ($context.Request.HttpMethod -eq "POST" -and $context.Request.Url.AbsolutePath -eq "/open") {
            try {
                $reader = [System.IO.StreamReader]::new($context.Request.InputStream, $context.Request.ContentEncoding)
                $payload = $reader.ReadToEnd() | ConvertFrom-Json
                $path = [string]$payload.path
                if ([string]::IsNullOrWhiteSpace($path) -or -not [System.IO.Path]::IsPathRooted($path)) {
                    throw "folder path must be absolute"
                }
                Start-Process -FilePath "explorer.exe" -ArgumentList @($path) | Out-Null
                $body = '{"ok":true}'
                $response.StatusCode = 200
            } catch {
                $body = (@{ error = $_.Exception.Message } | ConvertTo-Json -Compress)
                $response.StatusCode = 400
            }
        } else {
            $body = '{"error":"not found"}'
            $response.StatusCode = 404
        }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$body)
        $response.ContentType = "application/json"
        $response.ContentLength64 = $bytes.Length
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        $response.Close()
    }
} finally {
    $listener.Stop()
    $listener.Close()
}
