$ErrorActionPreference = 'Stop'
$repo = 'Qwen/Qwen2.5-VL-3B-Instruct-AWQ'
$tree = Invoke-RestMethod "https://huggingface.co/api/models/$repo/tree/main?recursive=true"
$files = foreach ($item in $tree) {
  if ($item.type -ne 'file') { continue }
  $name = [string]$item.path
  if ($name -notmatch '^(added_tokens|chat_template|config|generation_config|merges|model\.safetensors|preprocessor_config|special_tokens_map|tokenizer|tokenizer_config|vocab)') { continue }
  $sha = if ($item.lfs) { [string]$item.lfs.oid } else {
    $tmp = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName())
    try {
      Write-Host "Hashing $name"
      $uri = "https://huggingface.co/$repo/resolve/main/$($name)?download=true"
      Write-Host $uri
      Invoke-WebRequest $uri -OutFile $tmp -UseBasicParsing -ErrorAction Stop
      (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
  }
  [ordered]@{name=$name; size_bytes=[int64]$item.size; sha256=$sha; url="https://huggingface.co/$repo/resolve/main/$($name)?download=true"}
}
[ordered]@{model='Qwen2.5-VL-3B-Instruct-AWQ'; revision='main'; files=@($files)} |
  ConvertTo-Json -Depth 5 | Set-Content installer\core_model_manifest_3b.json -Encoding UTF8
