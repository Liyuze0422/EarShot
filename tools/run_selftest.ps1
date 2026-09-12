# 端到端自测：TTS 朗读 -> 回环采集 -> VAD -> SenseVoice 识别，并记录进程内存峰值。
#
# 用法（在任意目录下执行都行）：
#   powershell -ExecutionPolicy Bypass -File tools/run_selftest.ps1
#   powershell -ExecutionPolicy Bypass -File tools/run_selftest.ps1 -Python D:\venv\Scripts\python.exe
#
# 不传 -Python 时：先找仓库里的 .venv\Scripts\python.exe，找不到就用 PATH 上的 python。
param([string]$Python = '')

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot 'selftest_v2.py'
$tmp = Join-Path $root '.tmp'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$outF = Join-Path $tmp 'selftest.out'
$errF = Join-Path $tmp 'selftest.err'

if (-not $Python) {
  $venv = Join-Path $root '.venv\Scripts\python.exe'
  $Python = if (Test-Path $venv) { $venv } else { 'python' }
}
Write-Output ('解释器: ' + $Python)

$p = Start-Process -FilePath $Python -ArgumentList @('-X','utf8',$script) -WorkingDirectory $root -NoNewWindow -PassThru -RedirectStandardOutput $outF -RedirectStandardError $errF
$peak = 0
while (-not $p.HasExited) {
  try { $w = (Get-Process -Id $p.Id -ErrorAction Stop).WorkingSet64/1MB; if ($w -gt $peak) { $peak = $w } } catch {}
  Start-Sleep -Milliseconds 200
}
Get-Content $outF -Raw -Encoding UTF8
$e = Get-Content $errF -Raw -Encoding UTF8
if ($e -and $e.Trim()) { Write-Output '--- STDERR ---'; Write-Output $e.Substring(0, [Math]::Min(1500, $e.Length)) }
Write-Output ''
Write-Output ('>>> 进程内存峰值: ' + [math]::Round($peak,0) + ' MB <<<')
