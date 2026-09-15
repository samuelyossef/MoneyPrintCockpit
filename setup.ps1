[CmdletBinding()]
param(
  [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $PSScriptRoot 'runtime'
$secretsPath = Join-Path $runtimePath 'secrets'
$mptPath = Join-Path $runtimePath 'moneyprinter'
New-Item -ItemType Directory -Force -Path $secretsPath, $mptPath | Out-Null

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop não foi encontrado no PATH.' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop não está em execução.' }

function Read-SecretValue([string]$label, [string]$environmentName) {
  if ($NonInteractive) { return [Environment]::GetEnvironmentVariable($environmentName) }
  $secure = Read-Host -AsSecureString $label
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

$mptKey = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$moonshotKey = Read-SecretValue 'Chave Moonshot/Kimi (Enter para configurar depois)' 'MOONSHOT_API_KEY'
$pexelsKey = Read-SecretValue 'Chave Pexels (Enter para configurar depois)' 'PEXELS_API_KEY'
$uploadPostKey = Read-SecretValue 'Chave Upload-Post (Enter para configurar depois)' 'UPLOAD_POST_API_KEY'
$uploadPostUser = if ($NonInteractive) { [Environment]::GetEnvironmentVariable('UPLOAD_POST_USERNAME') } else { Read-Host 'Usuário Upload-Post (Enter para configurar depois)' }

[IO.File]::WriteAllText((Join-Path $secretsPath 'mpt_api_key.txt'), $mptKey + [Environment]::NewLine)
$toml = @"
log_level = "INFO"
listen_host = "0.0.0.0"
listen_port = 8080

[app]
video_source = "pexels"
pexels_api_keys = ["$pexelsKey"]
llm_provider = "moonshot"
moonshot_api_key = "$moonshotKey"
subtitle_provider = "edge"
enable_redis = true
redis_host = "redis"
redis_port = 6379
max_concurrent_tasks = 1
max_queued_tasks = 20
upload_post_enabled = false
upload_post_api_key = "$uploadPostKey"
upload_post_username = "$uploadPostUser"
upload_post_auto_upload = false
upload_post_youtube_privacy_status = "private"
api_key = "$mptKey"
hide_config = true
tls_verify = true
edge_tts_timeout = 30
"@
[IO.File]::WriteAllText((Join-Path $mptPath 'config.toml'), $toml, [Text.UTF8Encoding]::new($false))
icacls $runtimePath /inheritance:r /grant:r "$env:USERNAME`:(OI)(CI)F" | Out-Null
Write-Output 'Configuração local criada. Execute: docker compose up -d --wait'
