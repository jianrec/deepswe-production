[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "packy.env"),
    [switch]$SkipProduction
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedSelf {
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Root", $Root, "-EnvFile", $EnvFile
    )
    if ($SkipProduction) { $arguments += "-SkipProduction" }
    $child = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $child.ExitCode
}

if (-not (Test-Administrator)) {
    Write-Host "Requesting administrator permission..."
    Invoke-ElevatedSelf
}

$cpu = Get-CimInstance Win32_Processor
if (-not $cpu.VirtualizationFirmwareEnabled) {
    Write-Error @"
Intel hardware virtualization is disabled in BIOS/UEFI.
Enable Intel Virtualization Technology (VT-x) in BIOS, reboot Windows, and run this launcher again.
This is the only step Windows cannot enable from PowerShell.
"@
    exit 2
}

$features = @(
    "Microsoft-Windows-Subsystem-Linux",
    "VirtualMachinePlatform"
)
$rebootRequired = $false
foreach ($feature in $features) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $feature).State
    if ($state -ne "Enabled") {
        Write-Host "Enabling $feature..."
        & dism.exe /online /enable-feature /featurename:$feature /all /norestart
        if ($LASTEXITCODE -ne 0) { throw "Failed to enable Windows feature $feature (exit $LASTEXITCODE)" }
        $rebootRequired = $true
    }
}

Write-Host "Installing or updating WSL..."
& wsl.exe --install --no-distribution
if ($LASTEXITCODE -notin @(0, 1)) { throw "wsl --install failed (exit $LASTEXITCODE)" }
& wsl.exe --update
if ($LASTEXITCODE -ne 0) { Write-Warning "wsl --update returned exit code $LASTEXITCODE; continuing." }

if ($rebootRequired) {
    Write-Host "Windows must reboot before Docker can use WSL 2."
    $answer = Read-Host "Type R to reboot now, or anything else to stop"
    if ($answer -match "^r$") {
        Restart-Computer
    }
    exit 3
}

$dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path $dockerExe)) { throw "Docker Desktop executable not found: $dockerExe" }
    Start-Process $dockerExe
}

$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
$env:Path = "$dockerBin;$env:Path"
$ready = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    $null = & docker.exe info 2>$null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Write-Host "Waiting for Docker Engine ($attempt/60)..."
    Start-Sleep -Seconds 5
}
if (-not $ready) { throw "Docker Engine did not become ready within five minutes." }

if ($SkipProduction) { exit 0 }
if (-not (Test-Path $EnvFile)) { throw "Provider env file not found: $EnvFile" }

Push-Location $Root
try {
    $python = Get-Command python.exe -ErrorAction Stop
    & $python.Source scripts/finalize_task.py --root $Root --slot task-0013 --repeats 3 --mutants 4 --retry-failed
    if ($LASTEXITCODE -ne 0) { throw "task-0013 QA failed (exit $LASTEXITCODE)" }
    & $python.Source scripts/produce.py --root $Root --env-file $EnvFile --batch-size 1 --workers 1 --retry-failed
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
