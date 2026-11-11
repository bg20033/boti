param(
    [Parameter(Mandatory = $true)][ValidateSet("Main", "Worker")][string]$Role,
    [string]$WorkerConfig,
    [string]$DataDirectory = "data",
    [string]$BindAddress = "0.0.0.0",
    [switch]$IncludeOllama,
    [PSCredential]$Credential
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Ekzekuto Setup.ps1 para instalimit të detyrës." }

if ($Role -eq "Worker") {
    if (-not $WorkerConfig) { throw "Jep -WorkerConfig." }
    $WorkerConfig = (Resolve-Path $WorkerConfig).Path
    $Arguments = "-m boti worker --config `"$WorkerConfig`""
    $WorkerId = (Get-Content -Raw $WorkerConfig | ConvertFrom-Json).id
    $TaskName = "Boti-Worker-$WorkerId"
} else {
    if (-not [IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory = Join-Path $ProjectRoot $DataDirectory }
    $Arguments = "-m boti --data `"$DataDirectory`" serve --host $BindAddress"
    $TaskName = "Boti-Main"
}

if (-not $Credential) {
    $Credential = Get-Credential -Message "Llogaria Windows që do ta nisë Boti-n pas ndezjes (përdor fjalëkalimin, jo PIN-in)."
}
if (-not $Credential) { throw "Nuk u dha llogaria e nisjes." }
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -User $Credential.UserName -Password ($Credential.GetNetworkCredential().Password) -RunLevel Limited -Force | Out-Null
if ($Role -eq "Worker" -and $IncludeOllama) {
    $Ollama = (Get-Command ollama -ErrorAction Stop).Source
    $OllamaAction = New-ScheduledTaskAction -Execute $Ollama -Argument "serve" -WorkingDirectory $ProjectRoot
    Register-ScheduledTask -TaskName "Boti-Ollama" -Action $OllamaAction -Trigger $Trigger -Settings $Settings `
        -User $Credential.UserName -Password ($Credential.GetNetworkCredential().Password) -RunLevel Limited -Force | Out-Null
    Write-Host "U instalua edhe Boti-Ollama. Përdore vetëm nëse Ollama nuk niset nga një shërbim tjetër."
}
Write-Host "U instalua $TaskName. Windows ruan kredencialet e detyrës; ato nuk ruhen në projekt."
Write-Host "Për ta nisur tani: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Për ta hequr: Unregister-ScheduledTask -TaskName '$TaskName'"
