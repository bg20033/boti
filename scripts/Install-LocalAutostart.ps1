param(
    [switch]$Check,
    [switch]$AtBoot
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$mainTask = 'Boti-Main'
$workerTask = 'Boti-Worker-Local'
$ollamaTask = 'Boti-Ollama-Local'
$watchdogTask = 'Boti-Local-Watchdog'

function Start-TaskIfReady([string]$Name) {
    $task = Get-ScheduledTask -TaskName $Name
    if ($task.State -eq 'Ready') {
        Start-ScheduledTask -InputObject $task
    }
}

if ($Check) {
    Start-TaskIfReady $mainTask
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 4 | Out-Null
    } catch {
        Start-TaskIfReady $ollamaTask
        return
    }
    Start-TaskIfReady $workerTask
    return
}

$python = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe')).Path
$data = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'data')).Path
$workerConfig = (Resolve-Path -LiteralPath (Join-Path $data 'secrets\worker-local.json')).Path
$ollama = (Get-Command ollama.exe -ErrorAction Stop).Source
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$account = $identity.Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$taskAccount = @{ Principal = $principal }
if ($AtBoot) {
    $windowsPrincipal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Per nisje para login-it, hape PowerShell me Run as administrator.'
    }
    $credential = Get-Credential -UserName $account -Message 'Jep fjalekalimin e Windows per nisje para login-it.'
    if (-not $credential -or $credential.UserName -ne $account) {
        throw "Perdor llogarine $account qe ka konfigurimin dhe modelet e Boti-t."
    }
    $taskAccount = @{
        User = $account
        Password = $credential.GetNetworkCredential().Password
        RunLevel = 'Limited'
    }
}
$serviceSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$services = [ordered]@{}
$services[$mainTask] = New-ScheduledTaskAction -Execute $python -WorkingDirectory $projectRoot `
    -Argument ('-m boti --data "{0}" serve --host 0.0.0.0 --port 8765' -f $data)
$services[$workerTask] = New-ScheduledTaskAction -Execute $python -WorkingDirectory $projectRoot `
    -Argument ('-m boti worker --config "{0}"' -f $workerConfig)
$services[$ollamaTask] = New-ScheduledTaskAction -Execute $ollama -WorkingDirectory $projectRoot -Argument 'serve'

foreach ($name in $services.Keys) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($AtBoot) {
        Register-ScheduledTask -TaskName $name -Action $services[$name] `
            -Settings $serviceSettings @taskAccount -Force | Out-Null
    } elseif ($existing) {
        Set-ScheduledTask -TaskName $name -TaskPath $existing.TaskPath -Settings $serviceSettings | Out-Null
    } else {
        Register-ScheduledTask -TaskName $name -Action $services[$name] `
            -Principal $principal -Settings $serviceSettings | Out-Null
    }
}

$checkAction = New-ScheduledTaskAction -Execute $powershell -WorkingDirectory $projectRoot `
    -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "{0}" -Check' -f $PSCommandPath)
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $account
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$everyMinute.Repetition.StopAtDurationEnd = $false
$triggers = @($atLogon, $everyMinute)
if ($AtBoot) {
    $triggers += New-ScheduledTaskTrigger -AtStartup
}
$checkSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 45) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $watchdogTask -Action $checkAction -Trigger $triggers `
    -Settings $checkSettings @taskAccount -Force | Out-Null
Start-ScheduledTask -TaskName $watchdogTask
if ($AtBoot) {
    Write-Host 'Boti nis me Windows para login-it dhe kontrollohet cdo minute.'
} else {
    Write-Host 'Boti nis kur hyn ne Windows dhe kontrollohet cdo minute.'
}
