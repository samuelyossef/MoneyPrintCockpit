[CmdletBinding()]
param(
  [switch]$NonInteractive,
  [switch]$AdminOnly,
  [switch]$RotateGrowthPassword
)

$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $PSScriptRoot 'runtime'
$secretsPath = Join-Path $runtimePath 'secrets'
$mptPath = Join-Path $runtimePath 'moneyprinter'
New-Item -ItemType Directory -Force -Path $secretsPath, $mptPath | Out-Null

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop não foi encontrado no PATH.' }

# PS 5.1: stderr de comando nativo é convertido em ErrorRecord e, com
# ErrorActionPreference=Stop, abortaria o setup. Os helpers abaixo isolam isso.
function Invoke-DockerExit([string[]]$DockerArguments) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & docker @DockerArguments *> $null } finally { $ErrorActionPreference = $previous }
  return $LASTEXITCODE
}
function Invoke-DockerText([string[]]$DockerArguments) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { return ((& docker @DockerArguments 2>$null) -join [Environment]::NewLine) } finally { $ErrorActionPreference = $previous }
}

if ((Invoke-DockerExit @('info')) -ne 0) { throw 'Docker Desktop não está em execução.' }

# ---- Senhas locais do Growth OS (PostgreSQL/MinIO) -------------------------
# Ficam em `.env`, fora do versionamento e lido automaticamente pelo Docker
# Compose; valores já presentes são preservados. Quando o volume do PostgreSQL
# já existe (instalação anterior), a senha nova é aplicada em serviço com
# ALTER USER: não é preciso recriar o volume nem perder dados do Growth OS.
$envPath = Join-Path $PSScriptRoot '.env'
$envContent = if (Test-Path $envPath) { [IO.File]::ReadAllText($envPath) } else { '' }
function New-LocalPasswordHex([int]$bytes = 24) {
  $buffer = New-Object byte[] $bytes
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
  ([BitConverter]::ToString($buffer)).Replace('-', '').ToLowerInvariant()
}
function Get-EnvValue([string]$name) {
  if ($envContent -match ("(?m)^" + $name + "=([^\r\n]+)")) { return $Matches[1].Trim() }
  return $null
}
function Set-EnvValue([string]$name, [string]$value) {
  if ($script:envContent -match ("(?m)^" + $name + "=([^\r\n]*)")) {
    $script:envContent = [regex]::Replace($script:envContent, ("(?m)^" + $name + "=[^\r\n]*"), ($name + '=' + $value))
  } else {
    if ($script:envContent -and -not $script:envContent.EndsWith([Environment]::NewLine)) { $script:envContent += [Environment]::NewLine }
    $script:envContent += ($name + '=' + $value) + [Environment]::NewLine
  }
}

# MinIO não tem volume na composição atual: basta gerar a senha se faltar.
if (-not (Get-EnvValue 'MINIO_ROOT_PASSWORD')) { Set-EnvValue 'MINIO_ROOT_PASSWORD' (New-LocalPasswordHex) }

$growthExisting = Get-EnvValue 'GROWTH_POSTGRES_PASSWORD'
$growthUser = Get-EnvValue 'GROWTH_POSTGRES_USER'
if (-not $growthUser) { $growthUser = 'moneyprinter' }
$composeProject = $null
$configJson = Invoke-DockerText @('compose', 'config', '--format', 'json')
if (-not $configJson) { throw 'Não foi possível ler a configuração do Docker Compose.' }
try { $composeProject = ($configJson | ConvertFrom-Json).name } catch { }
$volumeExists = $false
if ($composeProject) {
  $growthVolume = "${composeProject}_growth-postgres-data"
  $volumeNames = (Invoke-DockerText @('volume', 'ls', '--format', '{{.Name}}')) -split '\r?\n'
  $volumeExists = [bool]($volumeNames | Where-Object { $_ -eq $growthVolume })
}

$rotate = $false
if (-not $volumeExists) {
  # Primeira inicialização: o Compose cria o volume já com a senha de `.env`.
  if ($RotateGrowthPassword -or -not $growthExisting) { Set-EnvValue 'GROWTH_POSTGRES_PASSWORD' (New-LocalPasswordHex) }
} elseif ($RotateGrowthPassword) {
  $rotate = $true
} elseif ($AdminOnly) {
  if (-not $growthExisting) {
    Write-Warning "O volume do Growth OS já existe e não há senha registrada em .env. Rode .\setup.ps1 -RotateGrowthPassword para gerar uma senha forte sem perder dados."
  }
} else {
  if ((Invoke-DockerExit @('compose', 'up', '-d', '--wait', 'postgresql')) -ne 0) {
    throw 'Não foi possível iniciar o serviço postgresql para verificar a senha local.'
  }
  if (-not $growthExisting) {
    $rotate = $true
    Write-Output 'Volume do Growth OS detectado sem senha registrada: a senha local do PostgreSQL será gerada e aplicada em serviço (sem perda de dados).'
  } else {
    $probeUri = "postgresql://${growthUser}:${growthExisting}@localhost:5432/postgres"
    Invoke-DockerExit @('compose', 'exec', '-T', 'postgresql', 'psql', $probeUri, '-c', 'SELECT 1') | Out-Null
    if ($LASTEXITCODE -ne 0) {
      $rotate = $true
      Write-Output 'A senha registrada em .env não corresponde à do volume do Growth OS: a senha local do PostgreSQL será trocada em serviço (sem perda de dados).'
    }
  }
}

if ($rotate) {
  $newPassword = New-LocalPasswordHex
  if ((Invoke-DockerExit @('compose', 'up', '-d', '--wait', 'postgresql')) -ne 0) {
    throw 'Não foi possível iniciar o serviço postgresql para trocar a senha local.'
  }
  # O socket local do container aceita conexões sem senha (trust); o ALTER USER
  # aplica a senha nova ao usuário existente, preservando todos os dados.
  $alter = "ALTER USER ${growthUser} WITH PASSWORD '${newPassword}';"
  if ((Invoke-DockerExit @('compose', 'exec', '-T', 'postgresql', 'psql', '-U', $growthUser, '-d', 'postgres', '-c', $alter)) -ne 0) {
    throw "Não foi possível trocar a senha do usuário '${growthUser}' no PostgreSQL."
  }
  Set-EnvValue 'GROWTH_POSTGRES_PASSWORD' $newPassword
}
[IO.File]::WriteAllText($envPath, $envContent, [Text.UTF8Encoding]::new($false))

# Serviços já em execução precisam ser recriados para enxergar a senha nova.
if ($rotate) {
  $running = @((Invoke-DockerText @('compose', 'ps', '--services', '--filter', 'status=running')) -split '\r?\n' | Where-Object { $_ })
  if ($running.Count -gt 0) {
    if ((Invoke-DockerExit @('compose', 'up', '-d', '--wait')) -eq 0) {
      Write-Output 'Serviços recriados com a senha nova do Growth OS.'
    } else {
      Write-Warning 'A senha foi trocada, mas a recriação dos serviços falhou. Rode: docker compose up -d --wait'
    }
  }
}

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
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($salt) } finally { $rng.Dispose() }
  $derive = [Security.Cryptography.Rfc2898DeriveBytes]::new($password, $salt, 600000, [Security.Cryptography.HashAlgorithmName]::SHA256)
  try { return @{ algorithm='pbkdf2_sha256'; iterations='600000'; salt=(([BitConverter]::ToString($salt)).Replace('-', '').ToLowerInvariant()); hash=(([BitConverter]::ToString($derive.GetBytes(32))).Replace('-', '').ToLowerInvariant()) } }
  finally { $derive.Dispose() }
}

$mptKey = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$adminName = Read-PlainValue 'Nome do administrador local' 'COCKPIT_ADMIN_NAME'
$adminEmail = Read-PlainValue 'E-mail do administrador local' 'COCKPIT_ADMIN_EMAIL'
$adminPassword = Read-SecretValue 'Senha do administrador local' 'COCKPIT_ADMIN_PASSWORD'
if ([string]::IsNullOrWhiteSpace($adminName) -or [string]::IsNullOrWhiteSpace($adminEmail) -or $adminPassword.Length -lt 12) {
  throw 'Informe nome, e-mail e uma senha de pelo menos 12 caracteres para o administrador local.'
}
$adminHash = Get-PasswordHash $adminPassword
$admin = @{ name=$adminName.Trim(); email=$adminEmail.Trim().ToLowerInvariant(); algorithm=$adminHash.algorithm; iterations=$adminHash.iterations; salt=$adminHash.salt; hash=$adminHash.hash } | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $secretsPath 'admin.json'), $admin + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
if ($AdminOnly) {
  icacls $runtimePath /inheritance:r /grant:r "$env:USERNAME`:(OI)(CI)F" | Out-Null
  Write-Output 'Administrador local configurado.'
  exit 0
}
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
