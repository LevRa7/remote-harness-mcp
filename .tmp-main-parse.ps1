$e1=$null;$t1=$null
[System.Management.Automation.Language.Parser]::ParseFile('D:\MyProjects\Antigravity-mash\install.ps1',[ref]$t1,[ref]$e1)|Out-Null
$e2=$null;$t2=$null
[System.Management.Automation.Language.Parser]::ParseFile('D:\MyProjects\Antigravity-mash\install-gui.ps1',[ref]$t2,[ref]$e2)|Out-Null
Write-Host ('main install.ps1 errors=' + $e1.Count + ', gui errors=' + $e2.Count)
$e1 | Select-Object -First 3 | ForEach-Object { Write-Host ('  ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }
Write-Host '--- admin/container feature surface ---'
Select-String -Path 'D:\MyProjects\Antigravity-mash\install.ps1' -Pattern 'AdminService|ProgramData|rhm-service' | Measure-Object | Select-Object -ExpandProperty Count