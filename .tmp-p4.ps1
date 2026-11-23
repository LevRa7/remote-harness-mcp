foreach ($f in @('D:\MyProjects\rhm-funnel\install.ps1','D:\MyProjects\rhm-funnel\install-gui.ps1')) {
  $e=$null;$t=$null
  [System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e)|Out-Null
  Write-Host ($f.Split('\')[-1] + ' errors=' + $e.Count)
  $e | Select-Object -First 3 | ForEach-Object { Write-Host ('  ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }
}