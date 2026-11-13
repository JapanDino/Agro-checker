param([switch]$Disable)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskOldPython = Join-Path $taskRoot '.venv\Scripts\pythonw.exe'
$taskOldScript = Join-Path $PSScriptRoot 'prospective_scouting.py'
$taskScript = Join-Path $PSScriptRoot 'prospective_scouting.py'
$taskShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskPythonQuoted = "'" + $taskPython.Replace("'", "''") + "'"
$taskScriptQuoted = "'" + $taskScript.Replace("'", "''") + "'"
$taskCommand = '& ' + $taskPythonQuoted + ' -X utf8 ' + $taskScriptQuoted + ' cycle --refresh; exit $LASTEXITCODE'
$taskArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "' + $taskCommand + '"'
$taskPreviousShellArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'run_prospective_job.ps1')`""
$taskName = 'Onlyfarms-ProspectiveCheck'
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskExisting) {
    $taskOldMatches = $taskExisting.Actions.Execute -eq $taskOldPython -and $taskExisting.Actions.Arguments -eq "-X utf8 `"$taskOldScript`" cycle --refresh"
    $taskNewMatches = $taskExisting.Actions.Execute -eq $taskShell -and $taskExisting.Actions.Arguments -eq $taskArguments
    $taskPreviousShellMatches = $taskExisting.Actions.Execute -eq $taskShell -and $taskExisting.Actions.Arguments -eq $taskPreviousShellArguments
    if ((!$taskOldMatches -and !$taskNewMatches -and !$taskPreviousShellMatches) -or $taskExisting.Actions.WorkingDirectory -ne $taskRoot) {
        throw 'Task name is already used by another installation; nothing was changed.'
    }
    if ($Disable) { Disable-ScheduledTask -TaskName $taskName | Out-Null; Write-Output 'Disabled'; exit }
}
elseif ($Disable) { Write-Output 'Task not installed'; exit }
if (!(Test-Path -LiteralPath (Join-Path $taskRoot 'data\prospective-scouting\frozen\protocol.json'))) {
    throw 'Register the frozen study first: scripts/prospective_scouting.py register'
}
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Project python.exe is required.' }
if ((Get-TimeZone).Id -ne 'Russian Standard Time') {
    throw 'This installer expects Moscow time. Set an explicit timezone-aware trigger before using elsewhere.'
}
$taskAction = New-ScheduledTaskAction -Execute $taskShell -Argument $taskArguments -WorkingDirectory $taskRoot
$taskTrigger = New-ScheduledTaskTrigger -Daily -At '18:00'
$taskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($taskExisting) {
    if ($taskExisting.State -eq 'Running') { throw 'Stop this task before updating its own launcher.' }
    Set-ScheduledTask -TaskName $taskName -Action $taskAction -Settings $taskSettings | Out-Null
} else {
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Description 'Onlyfarms: refresh Sentinel, freeze equal-budget future lists, evaluate mature satellite outcomes. No retuning or farm visits.' | Out-Null
}
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object NextRunTime,LastTaskResult
