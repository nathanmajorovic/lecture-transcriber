# Maakt een snelkoppeling "Lecture Transcriber" op het bureaublad en in het Startmenu.
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw  = Join-Path $env:USERPROFILE ".venvs\lecture-transcriber\Scripts\pythonw.exe"
$sh   = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Lecture Transcriber.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Lecture Transcriber.lnk")
)
foreach ($lnkPath in $targets) {
    $lnk = $sh.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "-m lecture_transcriber.gui"
    $lnk.WorkingDirectory = $proj
    $lnk.IconLocation = "$env:SystemRoot\System32\SndVol.exe,0"
    $lnk.Description = "Lesopnames lokaal omzetten naar een transcript"
    $lnk.Save()
    Write-Output "Snelkoppeling: $lnkPath"
}
