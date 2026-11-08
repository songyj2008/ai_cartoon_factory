param(
    [Parameter(Mandatory = $true)][string]$ProjectPath,
    [Parameter(Mandatory = $true)][string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath $ProjectPath).Path
$pythonExe = (Resolve-Path -LiteralPath $PythonPath).Path
$appFile = Join-Path $projectRoot 'app.py'
if (-not (Test-Path -LiteralPath $appFile -PathType Leaf)) {
    throw 'Project app.py does not exist.'
}
$projectConfig = Get-Content -LiteralPath (Join-Path $projectRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$serverPort = 7860
if ($projectConfig.server_port) { $serverPort = [int]$projectConfig.server_port }
if ($env:AI_CARTOON_FACTORY_PORT) { $serverPort = [int]$env:AI_CARTOON_FACTORY_PORT }
if ($serverPort -lt 1 -or $serverPort -gt 65535) { throw 'Invalid server port.' }

# Match the exact script argument and interpreter. A window title is not a
# process identity and does not exist for a hidden/background launch.
$scriptArgument = [regex]::Escape($appFile)
$commandPattern = '^\s*(?:"[^"]+"|\S+)\s+(?:-(?:u|B|E|s|S|I)\s+)*(?:"' + $scriptArgument + '"|' + $scriptArgument + ')(?:\s|$)'
$servers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.ExecutablePath -eq $pythonExe -and $_.CommandLine.Replace('/', '\') -match $commandPattern
})
foreach ($server in $servers) {
    # Recheck before stopping in case a process exited during enumeration.
    $verified = Get-CimInstance Win32_Process -Filter "ProcessId = $($server.ProcessId)"
    if ($verified -and $verified.ExecutablePath -eq $pythonExe -and $verified.CommandLine.Replace('/', '\') -match $commandPattern) {
        Stop-Process -Id $verified.ProcessId
        Write-Output "Stopped project backend PID $($verified.ProcessId)."
    }
}
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $serverPort -ErrorAction SilentlyContinue)
    if (-not $listeners.Count) { exit 0 }
    $unknown = @($listeners | Where-Object { $_.OwningProcess -notin $servers.ProcessId })
    if ($unknown.Count) {
        $owners = ($unknown.OwningProcess | Sort-Object -Unique) -join ', '
        throw "Port $serverPort is occupied by another process (PID: $owners). Close its original window before retrying; it was not stopped."
    }
    Start-Sleep -Milliseconds 250
}
throw "Port $serverPort was not released. Check the original server before retrying."
