# Waits for World of Warcraft to close, then generates the voiceovers for the
# lines collected during that session. The addon writes them to SavedVariables
# on logout, so this runs at exactly the right moment.
#
#   powershell -ExecutionPolicy Bypass -File auto_generate.ps1
#   powershell -ExecutionPolicy Bypass -File auto_generate.ps1 -GeneratorArgs "--language","de"
#
# Leave it running in the background; it keeps watching for the next session.
# To start it automatically, put a shortcut to auto_generate.bat into:
#   shell:startup   (Win+R, then that command)

param(
    [string[]]$GeneratorArgs = @(),
    [string]$ProcessName = "WowB",
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "generate_voices.py"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "Python was not found in PATH." }

Write-Host "Watching for $ProcessName. Voiceovers are generated after the game closes. Ctrl+C stops this." -ForegroundColor Cyan

while ($true) {
    while (-not (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)) {
        Start-Sleep -Seconds 15
    }
    Write-Host "$(Get-Date -Format 'HH:mm') $ProcessName is running, waiting for it to close..."
    while (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue) {
        Start-Sleep -Seconds 15
    }
    Start-Sleep -Seconds 5   # let the client finish writing SavedVariables
    Write-Host "$(Get-Date -Format 'HH:mm') generating voiceovers..." -ForegroundColor Green
    & $python $script @GeneratorArgs
    if ($Once) { break }
}
