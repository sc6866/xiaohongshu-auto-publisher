param(
    [switch]$WithTunnel,
    [switch]$ForceRecreate
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path $Path)) {
        return $values
    }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or $trimmed -notmatch "=") {
            continue
        }
        $parts = $trimmed -split "=", 2
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        $values[$key] = $value
    }
    return $values
}

function Invoke-DockerCompose {
    param([string[]]$Arguments)
    Write-Host ("docker " + ($Arguments -join " ")) -ForegroundColor DarkGray
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: docker $($Arguments -join ' ')"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$envExamplePath = Join-Path $repoRoot ".env.example"
$envPath = Join-Path $repoRoot ".env"
$composeFile = Join-Path $repoRoot "docker-compose.deploy.yml"

Require-Command "docker"

if (-not (Test-Path $composeFile)) {
    throw "Missing compose file: $composeFile"
}

if (-not (Test-Path $envPath)) {
    if (-not (Test-Path $envExamplePath)) {
        throw "Missing env template: $envExamplePath"
    }
    Copy-Item $envExamplePath $envPath
    Write-Host "Created .env from .env.example. Please fill in the required keys, then rerun the script." -ForegroundColor Yellow
    Write-Host "Path: $envPath"
    exit 1
}

$envValues = Read-DotEnv -Path $envPath
$requiredKeys = @(
    "DASHSCOPE_API_KEY",
    "BAIDU_OCR_API_KEY",
    "BAIDU_OCR_SECRET_KEY"
)
if ($WithTunnel) {
    $requiredKeys += "CF_TUNNEL_TOKEN"
}

$missing = @()
foreach ($key in $requiredKeys) {
    if (-not $envValues.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($envValues[$key])) {
        $missing += $key
    }
}

if ($missing.Count -gt 0) {
    Write-Host "Please fill these variables in .env before deployment:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
    Write-Host "Env file: $envPath"
    exit 1
}

Write-Step "Pulling latest image"
$pullArgs = @("compose", "-f", $composeFile, "pull")
if ($WithTunnel) {
    $pullArgs = @("compose", "-f", $composeFile, "--profile", "tunnel", "pull")
}
Invoke-DockerCompose -Arguments $pullArgs

Write-Step "Starting containers"
$upArgs = @("compose", "-f", $composeFile, "up", "-d")
if ($WithTunnel) {
    $upArgs = @("compose", "-f", $composeFile, "--profile", "tunnel", "up", "-d")
}
if ($ForceRecreate) {
    $upArgs += "--force-recreate"
}
Invoke-DockerCompose -Arguments $upArgs

Write-Step "Checking local health"
Start-Sleep -Seconds 4
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8787/healthz" -TimeoutSec 8
    $health | ConvertTo-Json -Depth 5
} catch {
    Write-Host "Local health check did not respond yet. You can inspect logs with:" -ForegroundColor Yellow
    Write-Host "docker compose -f docker-compose.deploy.yml logs -f app"
}

Write-Host ""
Write-Host "Deployment completed." -ForegroundColor Green
Write-Host "App URL: http://127.0.0.1:8787"
if ($WithTunnel) {
    Write-Host "Tunnel profile was enabled. Check Cloudflare Zero Trust for the public hostname." -ForegroundColor Green
}
