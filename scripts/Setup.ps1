param(
    [Parameter(Mandatory = $true)][ValidateSet("Main", "Worker")][string]$Role,
    [string]$MainUrl = "http://127.0.0.1:8765",
    [string]$WorkerConfig,
    [switch]$SkipModels
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

foreach ($Tool in @("uv", "ffmpeg", "ffprobe")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "Mungon $Tool. Lexo README.md dhe instalo varësitë para vazhdimit."
    }
}

if ($Role -eq "Main") {
    & uv sync --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "Instalimi i Python dështoi." }
    & uv run boti init --url $MainUrl
    if ($LASTEXITCODE -ne 0) { throw "Inicializimi i main-it dështoi." }
    $PrivatePath = Join-Path $ProjectRoot "data\secrets"
} else {
    if (-not $WorkerConfig) { throw "Jep -WorkerConfig me skedarin privat të këtij PC-je." }
    $WorkerConfig = (Resolve-Path $WorkerConfig).Path
    & uv sync --python 3.12 --extra worker
    if ($LASTEXITCODE -ne 0) { throw "Instalimi i agjentit dështoi." }
    $Filters = (& ffmpeg -hide_banner -loglevel error -filters | Out-String)
    if ($Filters -notmatch "subtitles") {
        throw "Ky FFmpeg nuk ka titra/libass. Instalo build-in full, p.sh. Gyan.FFmpeg, pastaj provo përsëri."
    }
    $Config = Get-Content -Raw $WorkerConfig | ConvertFrom-Json
    if (-not $SkipModels) {
        if (($Config.capabilities -contains "analyze") -or ($Config.capabilities -contains "quality")) {
            if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
                throw "Instalo Ollama dhe nise aplikacionin/shërbimin e tij para vazhdimit."
            }
            & ollama pull $Config.model
            if ($LASTEXITCODE -ne 0) { throw "Shkarkimi i modelit Ollama dështoi." }
        }
        if (($Config.capabilities -contains "transcribe") -or ($Config.capabilities -contains "quality") -or ($Config.capabilities -contains "repair") -or ($Config.capabilities -contains "analyze")) {
            & uv run python -c "import sys; from faster_whisper.utils import download_model; download_model(sys.argv[1])" $Config.whisper_model
            if ($LASTEXITCODE -ne 0) { throw "Shkarkimi i Whisper dështoi." }
        }
    }
    & uv run boti doctor --config $WorkerConfig --probe-ai
    if ($LASTEXITCODE -ne 0) { throw "Kontrolli i agjentit dështoi. Rregullo varësitë e shfaqura." }
    $PrivatePath = $WorkerConfig
}

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (Test-Path -PathType Container $PrivatePath) {
    & icacls $PrivatePath /inheritance:r /grant:r "$($Identity):(OI)(CI)F" /T | Out-Null
} else {
    & icacls $PrivatePath /inheritance:r /grant:r "$($Identity):F" | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw "Nuk u kufizua qasja te skedarët privatë." }
Write-Host "Instalimi përfundoi. Nise rolin sipas README.md; pastaj instalo detyrën e nisjes së Windows."
