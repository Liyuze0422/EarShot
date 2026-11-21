# EarShot 路径自检：安装目录里只要有一个非 ASCII 字符，EarShot 就起不来。
#
# 为什么要有这一步：PyInstaller 6.x 的 bootloader 在 Python 初始化之前，用 ANSI 路径
# 打开自己内嵌的 PKG 归档。安装目录含中文 / 日文 / 带重音的字母时它直接退出
# （Could not load PyInstaller's embedded PKG archive），既没有弹窗也没有日志 ——
# 用户看到的就是「双击没反应」。这一步在 Python 之外，没法从 Python 侧修，
# 所以只能在启动前拦住，并把原因和改法说清楚。
#
# 退出码：0 = 路径合格，可以继续启动；9 = 路径含非 ASCII 字符。
#
# 本文件必须保存为 UTF-8 带 BOM + CRLF：Windows PowerShell 5.1 会把无 BOM 的脚本
# 按本机 ANSI(GBK) 读，中文提示会变成乱码，甚至语法报错。
param(
    [string]$Path = ''
)

# 不给参数时，默认检查脚本自己所在的目录（check_path.ps1 就放在安装目录根）。
if (-not $Path) {
    $Path = $PSScriptRoot
}

# cmd 传 "%~dp0" 过来时结尾是反斜杠，powershell.exe 会把结尾的 \" 当成转义引号，
# 于是 $Path 末尾可能多出一个引号 —— 先去掉再判断。
$Path = $Path.TrimEnd('"')

if ($Path -match '[^\x20-\x7E]') {
    Write-Host ''
    Write-Host '路径含非 ASCII 字符，程序无法在这个位置启动。请把整个文件夹移到纯英文路径（例如 C:\EarShot）后，再双击 启动-EarShot.bat。' -ForegroundColor Yellow
    Write-Host ('当前路径：' + $Path.TrimEnd('\'))
    Write-Host ''
    exit 9
}

exit 0
