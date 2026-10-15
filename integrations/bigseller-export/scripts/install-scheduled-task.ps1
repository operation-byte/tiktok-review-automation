[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'WELIVE - BigSeller Single SKU Export'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runner = Join-Path $ProjectRoot 'scripts\run-export.cmd'

if (-not (Test-Path $Runner)) {
  throw "Runner not found: $Runner"
}

$Action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$Runner`"" -WorkingDirectory $ProjectRoot
$Triggers = @(
  New-ScheduledTaskTrigger -Daily -At '12:00 AM'
  New-ScheduledTaskTrigger -Daily -At '6:00 AM'
  New-ScheduledTaskTrigger -Daily -At '12:00 PM'
  New-ScheduledTaskTrigger -Daily -At '6:00 PM'
)

$Settings = New-ScheduledTaskSettingsSet `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable `
  -WakeToRun `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
  -RestartCount 2 `
  -RestartInterval (New-TimeSpan -Minutes 10)

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal -Description 'Exports BigSeller Single SKU inventory for the WELIVE dashboard importer.'

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Write-Host "Installed scheduled task: $TaskName" -ForegroundColor Green
Write-Host 'Triggers: 12:00 AM, 6:00 AM, 12:00 PM, 6:00 PM daily.'
