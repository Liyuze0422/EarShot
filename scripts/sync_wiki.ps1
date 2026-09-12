# 同步 docs/ 到 wiki/（GitHub Wiki 的内容源）
#
# 用法：  powershell -ExecutionPolicy Bypass -File scripts\sync_wiki.ps1
#
# docs/ 是唯一事实来源；本脚本把 docs/*.md 拷进 wiki/，并把仓库内相对链接
# 改写成 Wiki 链接（[[页面名]]），这样两处不会各写一份、慢慢跑偏。
# Home.md / _Sidebar.md / _Footer.md 是手写的，脚本不会覆盖它们。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$docs = Join-Path $root 'docs'
$wiki = Join-Path $root 'wiki'

if (-not (Test-Path $docs)) { throw "找不到 docs 目录: $docs" }
New-Item -ItemType Directory -Force -Path $wiki | Out-Null

# 不进 Wiki 的页面（偏仓库维护流程，放在仓库里读更合适）
$skip = @('发布到GitHub.md')

$n = 0
Get-ChildItem $docs -Filter *.md -File | ForEach-Object {
    if ($skip -contains $_.Name) { return }
    $text = Get-Content $_.FullName -Raw -Encoding UTF8
    # [标题](安装教程.md) / [标题](docs/安装教程.md)  ->  [[安装教程]]
    $text = [regex]::Replace($text, '\[([^\]]+)\]\((?:docs/)?([^)/]+\.md)\)', '[[$2]]')
    $text = $text -replace '\[\[([^\]]+)\.md\]\]', '[[$1]]'
    # 指向仓库内其它文件的相对链接，在 Wiki 里点不开，改成提示
    $text = [regex]::Replace($text, '\[([^\]]+)\]\((?!https?:|#)([^)]+)\)', '`$1`（见仓库文件 `$2`）')
    $out = Join-Path $wiki $_.Name
    Set-Content -Path $out -Value $text -Encoding UTF8 -NoNewline
    Write-Host ("  {0,-28} -> wiki\{0}" -f $_.Name)
    $n++
}

# 图片也一起同步（Wiki 里用相对路径 images/xxx.png 引用）
$imgSrc = Join-Path $docs "images"
if (Test-Path $imgSrc) {
    $imgDst = Join-Path $wiki "images"
    New-Item -ItemType Directory -Force -Path $imgDst | Out-Null
    Copy-Item (Join-Path $imgSrc "*") $imgDst -Force
    Write-Host ("  {0,-28} -> wiki\images\" -f "images/*")
}

Write-Host ""
Write-Host "已同步 $n 个页面到 wiki\"
Write-Host "推送：见 docs\发布到GitHub.md 第 5 节（git clone <repo>.wiki.git 后拷入本目录）"