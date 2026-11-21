# 仓库路径自检（源码模式）：仓库根目录里只要有一个非 ASCII 字符，启动器就会在
# 报错之前先失败 —— 用户看到的是「双击没反应」或者一句看不懂的 No pyvenv.cfg file。
#
# 实测（0.9.22，仓库放在中文路径下）：
#   .\.venv\Scripts\python.exe -V            -> No pyvenv.cfg file
#   dist\EarShot\EarShot.exe --version         -> [PYI-...:ERROR] Could not load PyInstaller's embedded PKG archive
# 把同一份东西放到 G:\EarShot 这样的纯 ASCII 路径都恢复正常。
#
# 退出码：0 = 路径合格，可以继续启动；9 = 路径含非 ASCII 字符。
#
# 本文件必须保存为 UTF-8 带 BOM + CRLF：Windows PowerShell 5.1 会把无 BOM 的脚本
# 按本机 ANSI(GBK) 读，中文提示会变成乱码，甚至语法报错。
param(
    [string]$Path = ''
)

# 不给参数时检查仓库根（本脚本在 tools\ 下，上一级就是仓库根）。
if (-not $Path) {
    $Path = Split-Path -Parent $PSScriptRoot
}

# cmd 传 "%~dp0" 过来时结尾是反斜杠，powershell.exe 会把结尾的 \" 当成转义引号，
# 于是 $Path 末尾可能多出一个引号 —— 先去掉再判断。
$Path = $Path.TrimEnd('"')

if ($Path -match '[^\x20-\x7E]') {
    Write-Host ''
    Write-Host '路径含非 ASCII 字符，程序无法在这个位置启动。请把整个仓库移到纯英文路径（例如 C:\dev\EarShot）后，再双击 启动提词器.bat。' -ForegroundColor Yellow
    Write-Host ('当前路径：' + $Path.TrimEnd('\'))
    Write-Host ''
    exit 9
}

exit 0
