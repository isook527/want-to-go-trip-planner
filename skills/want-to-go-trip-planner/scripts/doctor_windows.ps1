param(
    [ValidateSet("zh-CN", "en-US")]
    [string]$Locale = "zh-CN"
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$configPath = Join-Path (Split-Path $PSScriptRoot -Parent) "config\product.json"
$productConfig = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json

function Test-Executable([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

$pythonName = $null
$pythonPrefix = @()

if (Test-Executable "python") {
    & python --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $pythonName = "python"
    }
}

if ($null -eq $pythonName -and (Test-Executable "py")) {
    & py -3 --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $pythonName = "py"
        $pythonPrefix = @("-3")
    }
}

if ($null -eq $pythonName -and (Test-Executable "python3")) {
    & python3 --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $pythonName = "python3"
    }
}

if ($null -eq $pythonName) {
    $missingPythonMessage = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String("57y65bCRIFB5dGhvbiAzLjkg5oiW5pu06auY54mI5pys77yM5peg5rOV5ZCv5Yqo5oOz5Y675bqT5a6J6KOF5qOA5rWL44CC")
    )
    $result = [ordered]@{
        schemaVersion = $productConfig.installDoctorMarker
        platform = [ordered]@{
            name = "Windows"
            supported = $true
        }
        status = "blocked"
        coreReady = $false
        fullReady = $false
        dependencies = [ordered]@{
            python = [ordered]@{ installed = $false; ready = $false; minimum = "3.9" }
            node = [ordered]@{ installed = (Test-Executable "node") }
            tesseract = [ordered]@{ installed = (Test-Executable "tesseract") }
            ffmpeg = [ordered]@{ installed = (Test-Executable "ffmpeg") }
            ffprobe = [ordered]@{ installed = (Test-Executable "ffprobe") }
        }
        blockingIssues = @($missingPythonMessage)
        featureWarnings = @()
        installCommands = @("winget install --id Python.Python.3.12 -e")
    }
    $result | ConvertTo-Json -Depth 6
    exit 2
}

$doctorPath = Join-Path $PSScriptRoot "extract_evidence.py"
$arguments = @()
$arguments += $pythonPrefix
$arguments += @($doctorPath, "doctor", "--locale", $Locale)
& $pythonName @arguments
exit $LASTEXITCODE
