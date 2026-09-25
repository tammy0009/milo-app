# Puts a MILO icon on the Desktop and in the Start menu. Run once (again if the folder moves).
# The icon runs pythonw, so no console window appears; the app logs to data\milo-app.log.
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "No .venv yet - run 'uv sync' in $root first." }

$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs")
)
foreach ($place in $places) {
    $link = $shell.CreateShortcut((Join-Path $place "MILO.lnk"))
    $link.TargetPath = $pythonw
    $link.Arguments = "-m milo_app"
    $link.WorkingDirectory = $root
    $link.IconLocation = (Join-Path $root "assets\milo.ico")
    $link.Description = "MILO"
    $link.Save()
    Write-Output "Shortcut: $($link.FullName)"
}
