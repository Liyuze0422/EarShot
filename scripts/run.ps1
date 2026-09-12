<#
  EarShot（顺风耳）· 启动脚本

  做三件事：定位仓库根 -> 激活 .venv -> 用 venv 的解释器跑 tools/launch.py。
  参数原样透传给 launch.py：

    .\scripts\run.ps1              # 起后端 + 浮窗（幂等：已在跑就不会再起第二个）
    .\scripts\run.ps1 --check      # 只看当前状态，不启动
    .\scripts\run.ps1 --no-ui      # 只起后端（调试用）
    .\scripts\run.ps1 --stop       # 停掉所有提词器进程

  注意：--check 在“后端没在跑”时退出码是 1，这是 launch.py 的正常约定。

  维护提醒：本文件必须保存成「UTF-8 带 BOM + CRLF」；无 BOM 时 PowerShell 5.1 会按
  GBK 读，中文乱码并报语法错误。
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvDir  = Join-Path $RepoRoot '.venv'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'
$Launcher = Join-Path $RepoRoot 'tools\launch.py'
$Activate = Join-Path $VenvDir 'Scripts\Activate.ps1'

if (-not (Test-Path $VenvPy)) {
    Write-Host ''
    Write-Host "还没装好：找不到 $VenvPy" -ForegroundColor Red
    Write-Host '先跑一次安装脚本：' -ForegroundColor Yellow
    Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1'
    Write-Host ''
    exit 1
}
if (-not (Test-Path $Launcher)) {
    Write-Host "找不到启动器：$Launcher" -ForegroundColor Red
    Write-Host '仓库不完整？请重新解压/克隆一份。' -ForegroundColor Yellow
    exit 1
}

# 激活 venv（让子进程继承 PATH 与 VIRTUAL_ENV）。执行策略不允许时不影响后面的显式调用。
if (Test-Path $Activate) {
    try { . $Activate } catch { Write-Host ("提示：激活 venv 失败，" + $_.Exception.Message + "（不影响启动）") -ForegroundColor DarkGray }
}

Set-Location $RepoRoot
& $VenvPy -X utf8 $Launcher @Rest
$rc = $LASTEXITCODE

if ($null -eq $rc) { $rc = 0 }
if ($rc -ne 0) {
    Write-Host ''
    Write-Host "启动器退出码 $rc。日志在 logs\ 下：" -ForegroundColor Yellow
    Write-Host '    logs\launch.log            启动器自己的记录（含“已经有一个后端在跑”这类判断）'
    Write-Host '    logs\backend_console.log   后端的标准输出/错误（模型加载、uvicorn 启动）'
    Write-Host '    logs\ui_console.log        浮窗的标准输出/错误'
    Write-Host '    logs\backend_error.log     后端线程里未捕获的异常'
    Write-Host '排查步骤见 docs\故障排查.md' -ForegroundColor Gray
    Write-Host ''
}

exit $rc
