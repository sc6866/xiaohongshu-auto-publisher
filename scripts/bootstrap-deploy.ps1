param(
    [string]$RepoUrl = "https://github.com/sc6866/xiaohongshu-auto-publisher.git",
    [string]$TargetDir = "$env:USERPROFILE\deploy\xiaohongshu-auto-publisher",
    [string]$Branch = "main",
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

Require-Command "git"

if (-not (Test-Path $TargetDir)) {
    Write-Step "Cloning repository"
    git clone --branch $Branch $RepoUrl $TargetDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone repository."
    }
} else {
    Write-Step "Updating repository"
    git -C $TargetDir fetch origin $Branch
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to fetch latest code."
    }
    git -C $TargetDir checkout $Branch
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to checkout branch $Branch."
    }
    git -C $TargetDir pull origin $Branch
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull latest code."
    }
}

$deployScript = Join-Path $TargetDir "scripts\deploy.ps1"
if (-not (Test-Path $deployScript)) {
    throw "Missing deploy script: $deployScript"
}

Write-Step "Running deployment"
& powershell -NoProfile -ExecutionPolicy Bypass -File $deployScript -WithTunnel:$WithTunnel -ForceRecreate:$ForceRecreate
if ($LASTEXITCODE -ne 0) {
    throw "Deployment script returned a failure status."
}
