$ErrorActionPreference = "Stop"

$AppName = "Nakwasina-Public"
$ZipUrl = "https://github.com/cplante-unishka/Nakwasina-Public/archive/refs/heads/main.zip"

$PythonRequired = [version]"3.14.5"
$PythonExe = "C:\Program Files\Python314\python.exe"
$PythonInstaller = "$env:TEMP\python-3.14.5-amd64.exe"
$PythonUrl = "https://www.python.org/ftp/python/3.14.5/python-3.14.5-amd64.exe"

$InstallDir = "C:\Users\Public\$AppName"
$TmpDir = "$env:TEMP\$AppName-install"
$ShortcutPath = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\$AppName.lnk"

Write-Host "Installing $AppName..."

Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $TmpDir | Out-Null

$NeedPythonInstall = $true

if (Test-Path $PythonExe) {
    $CurrentVersionText = & $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    $CurrentVersion = [version]$CurrentVersionText

    Write-Host "Found Python $CurrentVersion at $PythonExe"

    if ($CurrentVersion -ge $PythonRequired) {
        $NeedPythonInstall = $false
    }
}

if ($NeedPythonInstall) {
    Write-Host "Downloading Python $PythonRequired..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonInstaller

    Write-Host "Installing Python $PythonRequired..."
    Start-Process -FilePath $PythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -Wait
}

if (!(Test-Path $PythonExe)) {
    throw "Python 3.14.5 was not installed correctly."
}

Write-Host "Using Python: $PythonExe"

Write-Host "Downloading application..."
$ZipPath = "$TmpDir\main.zip"
Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath

Write-Host "Extracting application..."
Expand-Archive -Path $ZipPath -DestinationPath $TmpDir -Force

Write-Host "Installing to $InstallDir..."
Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $InstallDir | Out-Null

Copy-Item "$TmpDir\Nakwasina-Public-main\*" $InstallDir -Recurse -Force

Write-Host "Installing requirements..."
& $PythonExe -m ensurepip --upgrade
& $PythonExe -m pip install --upgrade pip setuptools wheel

if (Test-Path "$InstallDir\requirements.txt") {
    & $PythonExe -m pip install --upgrade -r "$InstallDir\requirements.txt"
}

Write-Host "Creating Start Menu shortcut..."

$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PythonExe
$Shortcut.Arguments = "`"$InstallDir\gui_app.py`""
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Save()

Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Installation complete."
Write-Host "Shortcut created at:"
Write-Host $ShortcutPath
