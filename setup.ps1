[CmdletBinding()]
param(
  [switch]$NonInteractive,
  [switch]$AdminOnly
)

$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $PSScriptRoot 'runtime'
$secretsPath = Join-Path $runtimePath 'secrets'
$mptPath = Join-Path $runtimePath 'moneyprinter'
New-Item -ItemType Directory -Force -Path $secretsPath, $mptPath | Out-Null

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop was not found on PATH.' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running.' }

function Read-SecretValue([string]$label, [string]$environmentName) {
  if ($NonInteractive) { return [Environment]::GetEnvironmentVariable($environmentName) }
  $secure = Read-Host -AsSecureString $label
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Read-PlainValue([string]$label, [string]$environmentName) {
  if ($NonInteractive) { return [Environment]::GetEnvironmentVariable($environmentName) }
  return Read-Host $label
}

function Get-PasswordHash([string]$password) {
  $salt = New-Object byte[] 16
  [Security.Cryptography.RandomNumberGenerator]::Fill($salt)
  $derive = [Security.Cryptography.Rfc2898DeriveBytes]::new($password, $salt, 600000, [Security.Cryptography.HashAlgorithmName]::SHA256)
  try { return @{ algorithm='pbkdf2_sha256'; iterations='600000'; salt=([Convert]::ToHexString($salt).ToLowerInvariant()); hash=([Convert]::ToHexString($derive.GetBytes(32)).ToLowerInvariant()) } }
  finally { $derive.Dispose() }
}

$mptKey = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$adminName = Read-PlainValue 'Local administrator name' 'COCKPIT_ADMIN_NAME'
$adminEmail = Read-PlainValue 'Local administrator email' 'COCKPIT_ADMIN_EMAIL'
$adminPassword = Read-SecretValue 'Local administrator password' 'COCKPIT_ADMIN_PASSWORD'
if ([string]::IsNullOrWhiteSpace($adminName) -or [string]::IsNullOrWhiteSpace($adminEmail) -or $adminPassword.Length -lt 12) {
  throw 'Enter a name, email, and a password of at least 12 characters for the local administrator.'
}
$adminHash = Get-PasswordHash $adminPassword
$admin = @{ name=$adminName.Trim(); email=$adminEmail.Trim().ToLowerInvariant(); algorithm=$adminHash.algorithm; iterations=$adminHash.iterations; salt=$adminHash.salt; hash=$adminHash.hash } | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $secretsPath 'admin.json'), $admin + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
if ($AdminOnly) {
  icacls $runtimePath /inheritance:r /grant:r "$env:USERNAME`:(OI)(CI)F" | Out-Null
  Write-Output 'Local administrator configured.'
  exit 0
}
$moonshotKey = Read-SecretValue 'Moonshot/Kimi key (press Enter to configure later)' 'MOONSHOT_API_KEY'
$pexelsKey = Read-SecretValue 'Pexels key (press Enter to configure later)' 'PEXELS_API_KEY'
$uploadPostKey = Read-SecretValue 'Upload-Post key (press Enter to configure later)' 'UPLOAD_POST_API_KEY'
$uploadPostUser = if ($NonInteractive) { [Environment]::GetEnvironmentVariable('UPLOAD_POST_USERNAME') } else { Read-Host 'Upload-Post username (press Enter to configure later)' }

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
Write-Output 'Local configuration created. Run: docker compose up -d --wait'
