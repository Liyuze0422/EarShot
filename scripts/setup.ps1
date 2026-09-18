<#
  EarShot（顺风耳）· 一键安装脚本（Windows）

  幂等：重复运行不会破坏已有环境 —— 已存在的 .venv / 模型 / 配置文件一律跳过。

  维护提醒：本文件必须保存成「UTF-8 带 BOM + CRLF」。
  Windows PowerShell 5.1 会把无 BOM 的 .ps1 当 GBK 读：中文全乱之外，字符串里的引号
  还会被双字节序列吞掉，脚本直接语法报错（实测 8 处）。改完本文件请确认 BOM 还在：
    [System.IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding($true)))

  它做的事（任何一步失败就停下来并给出修复提示）：
    0. 检查仓库路径是否纯英文（含中文时 ASR 模型必然加载失败，先拦下来）
    1. 找 Python 3.11 / 3.12（64 位）
    2. 建 .venv
    3. 装依赖（优先用 requirements.lock.txt 强制校验每个包的 sha256；国内可加 -Mirror）
    4. 下载 SenseVoiceSmall ONNX int8 模型（约 230MB，ModelScope）
    5. 生成 config 配置模板与 knowledge 示例材料
    6. 跑 tools/preflight.py 全链路自检

  用法：
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Mirror
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -SkipModel -Fast

  参数：
    -Mirror            用清华 PyPI 镜像装依赖（国内网络推荐）
    -IndexUrl <url>    自定义 PyPI 源
    -SkipModel         跳过模型下载（已经下过、或想自己放模型）
    -SkipDeps          跳过依赖安装（你自己有办法管依赖时）
    -SkipPreflight     跳过最后的自检
    -Fast              自检用 --fast（跳过 ASR 识别与浮窗自检，约 20 秒）
    -NoLock            不用锁定文件（跳过哈希校验，退回 requirements.txt）
    -PythonExe <path>  指定解释器（默认自动找 py -3.12 / py -3.11 / python）
#>
[CmdletBinding()]
param(
    [switch]$Mirror,
    [string]$IndexUrl = '',
    [switch]$SkipModel,
    [switch]$SkipDeps,
    [switch]$SkipPreflight,
    [switch]$Fast,
    [switch]$NoLock,
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$NL = [Environment]::NewLine

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvDir  = Join-Path $RepoRoot '.venv'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'
$ModelDir = Join-Path $RepoRoot 'models\SenseVoiceSmall-onnx'

# 模型目录应当包含的文件。前 5 个由 tools\download_model.py 下载；
# 最后一个 bpe 分词模型在另一个 ModelScope 仓库里，见第 4 步。
$ModelFiles = @('model_quant.onnx', 'tokens.json', 'am.mvn', 'config.yaml', 'configuration.json')
$BpeFile    = 'chn_jpn_yue_eng_ko_spectok.bpe.model'
$BpeUrl     = 'https://www.modelscope.cn/api/v1/models/iic/SenseVoiceSmall/repo?Revision=master&FilePath=' + $BpeFile

# ── 输出小工具 ────────────────────────────────────────────────────────
function Step($n, $total, $text) {
    Write-Host ''
    Write-Host ("[{0}/{1}] {2}" -f $n, $total, $text) -ForegroundColor Cyan
}
function Ok($text)   { Write-Host ("      OK   " + $text) -ForegroundColor Green }
function Note($text) { Write-Host ("      ..   " + $text) -ForegroundColor Gray }
function Warn($text) { Write-Host ("      !!   " + $text) -ForegroundColor Yellow }
function Die($text, $hint) {
    Write-Host ''
    Write-Host ("      FAIL " + $text) -ForegroundColor Red
    if ($hint) {
        Write-Host ''
        Write-Host '      怎么修：' -ForegroundColor Yellow
        foreach ($line in ($hint -split "\r?\n")) { Write-Host ("        " + $line) -ForegroundColor Yellow }
    }
    Write-Host ''
    Write-Host '安装已停止。修好上面的问题后重新运行本脚本即可（已完成的步骤会自动跳过）。' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '====================================================================' -ForegroundColor White
Write-Host '  EarShot（顺风耳）· 一键安装' -ForegroundColor White
Write-Host ("  仓库根: " + $RepoRoot) -ForegroundColor White
Write-Host '====================================================================' -ForegroundColor White

$Total = 6

# ── 0. 路径必须纯 ASCII ───────────────────────────────────────────────
Step 0 $Total '检查仓库路径（ASR 模型要求纯 ASCII 路径）'
$badChars = @($RepoRoot.ToCharArray() | Where-Object { [int]$_ -gt 127 })
if ($badChars.Count -gt 0) {
    $bad = ($badChars | Select-Object -Unique) -join ''
    Die "仓库路径含非 ASCII 字符（$bad）：$RepoRoot" @"
ASR 模型自带的 sentencepiece 是 C++ 实现，遇到中文路径会在 C++ 层直接报 NOT_FOUND，
而 Python 侧的 os.path.exists() 却认为文件存在 —— 报错完全指不到真正的原因。

任选一条改：
  1. 把整个仓库移到纯英文路径，例如  C:\dev\EarShot  （最省事）
  2. 只把模型放纯英文目录，然后设环境变量：
     setx TP_MODEL_DIR D:\models\SenseVoiceSmall-onnx
  3. 在 config\settings.json 里把 model_dir 指到纯英文路径
"@
}
Ok '路径纯 ASCII，可以继续'

# ── 1. Python 版本 ────────────────────────────────────────────────────
Step 1 $Total '检查 Python 解释器（需要 3.11 / 3.12，64 位）'
$probe = "import sys,struct;print('%d.%d.%d' % sys.version_info[:3]);print(struct.calcsize('P')*8)"

function Test-Python($exe, $pre) {
    $cmd = Get-Command $exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    $out = $null
    try { $out = & $exe @pre '-c' $probe 2>$null } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $lines = @($out | Where-Object { $_ })
    if ($lines.Count -lt 2) { return $null }
    $ver  = [string]$lines[0]
    $bits = 0
    try { $bits = [int]$lines[1] } catch { return $null }
    $mm = $ver -split '\.'
    return [pscustomobject]@{
        Exe = $exe; Pre = $pre; Version = $ver
        Major = [int]$mm[0]; Minor = [int]$mm[1]; Bits = $bits; Path = $cmd.Source
    }
}

$py = $null
$tried = @()
if ($PythonExe) {
    $py = Test-Python $PythonExe @()
    if (-not $py) { $tried += $PythonExe }
} else {
    foreach ($cand in @(@('py', @('-3.12')), @('py', @('-3.11')), @('python', @()), @('python3', @()))) {
        $py = Test-Python $cand[0] $cand[1]
        if ($py) { break }
        $tried += (($cand[0] + ' ' + ($cand[1] -join ' '))).Trim()
    }
}
if (-not $py) {
    Die "没找到可用的 Python（试过：$($tried -join ' / ')）" @"
装一个 64 位的 Python 3.12 就好：
  · 官网 https://www.python.org/downloads/windows/  （安装时勾选 Add python.exe to PATH）
  · 或一行命令：  winget install Python.Python.3.12
装完重开一个 PowerShell 窗口再跑本脚本。
"@
}
if ($py.Major -ne 3 -or $py.Minor -lt 11 -or $py.Minor -gt 12) {
    Die "Python 版本不支持：$($py.Version)（需要 3.11 或 3.12）" @"
当前解释器：$($py.Path)
3.13 及以上没有在本项目上验证过，请装 3.11 或 3.12 后用 -PythonExe 指定：
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -PythonExe 'C:\Python312\python.exe'
"@
}
if ($py.Bits -ne 64) {
    Die "Python 是 32 位的（$($py.Version) / $($py.Bits) 位），依赖装不上" @"
请换成 64 位 Python 3.12（官网下载页选 Windows installer (64-bit)）。
当前解释器：$($py.Path)
"@
}
Ok "Python $($py.Version)  $($py.Bits) 位  $($py.Path)"

# ── 2. 虚拟环境 ───────────────────────────────────────────────────────
Step 2 $Total '准备虚拟环境 .venv'
if (Test-Path $VenvPy) {
    Ok '.venv 已存在，跳过创建'
} else {
    Note '创建 .venv ...'
    & $py.Exe @($py.Pre) '-m' 'venv' $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) {
        Die "创建虚拟环境失败（$VenvDir）" @"
常见原因：
  · 仓库目录没有写权限（换到 C:\dev\EarShot 这类自己的目录）
  · 杀毒软件拦住了 python.exe 建目录
删掉残留的 .venv 目录后重试。
"@
    }
    Ok "已创建 $VenvDir"
}

# ── 3. 依赖 ───────────────────────────────────────────────────────────
Step 3 $Total '安装依赖'
if ($SkipDeps) {
    Note '-SkipDeps：跳过依赖安装（自己管依赖时）'
} else {
    $req = Join-Path $RepoRoot 'requirements.txt'
    if ($Mirror -and -not $IndexUrl) { $IndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple' }
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check')
    if ($IndexUrl) { $pipArgs += @('-i', $IndexUrl) }

    Note '升级 pip ...'
    & $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip') | Out-Null

    $lock = Join-Path $RepoRoot 'requirements.lock.txt'
    if ((Test-Path $lock) -and -not $NoLock) {
        # 优先用带哈希的锁定文件：每个包都对着 PyPI 官方的 sha256 校验。
        # 镜像被投毒或上游换了包，安装会当场失败，而不是"装个差不多的版本"糊过去。
        # 想跳过校验（例如自己要换版本）：加 -NoLock
        if ($IndexUrl) { Note "pip install -r requirements.lock.txt  (强制哈希校验；源: $IndexUrl)" }
        else { Note 'pip install -r requirements.lock.txt  (强制哈希校验；默认源)' }
        & $VenvPy @pipArgs @('-r', $lock)
        if ($LASTEXITCODE -ne 0) {
            Die '锁定依赖安装失败（哈希校验没过，或网络中断）' @"
两种可能：
  · 下载到的包和锁文件里记的 sha256 对不上 —— 换一个源重试，仍然对不上就别装（见 docs 的供应链说明）
  · 只是网络/镜像不稳 —— 加 -Mirror 或 -IndexUrl 换源重试
确实想跳过校验（自己管依赖）：同一条命令加 -NoLock
"@
        }
    } elseif (Test-Path $req) {
        if ($IndexUrl) { Note "pip install -r requirements.txt  (源: $IndexUrl)" }
        else { Note 'pip install -r requirements.txt  (默认源)' }
        & $VenvPy @pipArgs @('-r', $req)
    } else {
        # requirements.txt 是仓库里应该有的文件；万一缺失就用等价的显式清单兜住，
        # 免得第一次装的人卡在一句「文件不存在」上。
        Warn 'requirements.txt 不存在，改用内置依赖清单安装（仓库正常时应该有这个文件）'
        $pkgs = @('funasr-onnx', 'onnxruntime', 'soundcard', 'soundfile', 'numpy', 'librosa',
                  'numba', 'jieba', 'sentencepiece', 'PyQt6', 'fastapi', 'uvicorn', 'websockets', 'openai')
        Note ($pkgs -join ' ')
        & $VenvPy @pipArgs @pkgs
    }
    if ($LASTEXITCODE -ne 0) {
    Die '依赖安装失败' @"
国内网络常见解法（任选）：
  · 加镜像：  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Mirror
  · 指定源：  ... -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
  · 走代理：  先设环境变量 HTTPS_PROXY=http://127.0.0.1:7890 再重跑
其他原因：磁盘空间不足（需要约 1.5GB）、杀毒软件拦截写 site-packages。
pip 真正的原因就打印在上面几行，先看那段。
"@
    }
    Ok '依赖装好了'
}

# ── 4. ASR 模型 ───────────────────────────────────────────────────────
Step 4 $Total '下载 ASR 模型（SenseVoiceSmall ONNX int8，约 230MB）'
$onnx = Join-Path $ModelDir 'model_quant.onnx'
if ($SkipModel) {
    Note '-SkipModel：跳过模型下载'
} elseif ((Test-Path $onnx) -and ((Get-Item $onnx).Length -gt 100MB)) {
    Ok '模型主体已存在，跳过下载'
} else {
    Note '从 ModelScope 下载，视网速约 1~10 分钟 ...'
    & $VenvPy @('-X', 'utf8', (Join-Path $RepoRoot 'tools\download_model.py'))
    if (-not (Test-Path $onnx) -or (Get-Item $onnx).Length -lt 100MB) {
        Die "模型没下全（缺 $onnx 或大小不对）" @"
  · 网络不通：到 https://www.modelscope.cn/models/iic/SenseVoiceSmall-onnx 手动下载
    model_quant.onnx / tokens.json / am.mvn / config.yaml / configuration.json，
    放进 $ModelDir
  · 磁盘空间不足：模型 230MB，至少留 1GB 余量
下完重跑本脚本，已下载的文件会自动跳过。
"@
    }
    Ok '模型主体下载完成'
}

if (-not $SkipModel) {
    # 分词模型不在上面那个仓库里；缺了它，加载模型会在 sentencepiece 那一步失败。
    $bpe = Join-Path $ModelDir $BpeFile
    if ((Test-Path $bpe) -and ((Get-Item $bpe).Length -gt 1000)) {
        Ok '分词模型已在（跳过）'
    } else {
        Note '补下载分词模型 chn_jpn_yue_eng_ko_spectok.bpe.model（约 368KB）...'
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            if (-not (Test-Path $ModelDir)) { New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null }
            $wc = New-Object System.Net.WebClient
            $wc.Headers.Add('User-Agent', 'Mozilla/5.0')
            $wc.DownloadFile($BpeUrl, $bpe)
            $wc.Dispose()
        } catch {
            Warn "分词模型下载失败：$($_.Exception.Message)"
        }
        if ((Test-Path $bpe) -and ((Get-Item $bpe).Length -gt 1000)) {
            Ok '分词模型已补齐'
        } else {
            Warn "仍缺 $BpeFile —— 用浏览器打开下面这个地址下载，放到 $ModelDir"
            Warn $BpeUrl
        }
    }
    Write-Host ''
    Write-Host '      模型目录现状：' -ForegroundColor Gray
    $missing = @()
    foreach ($f in ($ModelFiles + $BpeFile)) {
        $p = Join-Path $ModelDir $f
        if (Test-Path $p) {
            Write-Host ("        {0,9:N1} MB  {1}" -f ((Get-Item $p).Length / 1MB), $f) -ForegroundColor Gray
        } else {
            Write-Host ("              缺失  " + $f) -ForegroundColor Yellow
            $missing += $f
        }
    }
    if ($missing.Count -gt 0) {
        Warn ("还缺 $($missing.Count) 个文件：" + ($missing -join ', '))
    } else {
        Ok '6 个文件齐全'
    }
}

# ── 5. 配置与示例材料 ─────────────────────────────────────────────────
Step 5 $Total '生成配置文件与示例材料'
$cfgDir = Join-Path $RepoRoot 'config'
if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null }

$example  = Join-Path $cfgDir 'settings.example.json'
$settings = Join-Path $cfgDir 'settings.json'
if (Test-Path $settings) {
    Ok 'config\settings.json 已存在，保留不动'
} elseif (Test-Path $example) {
    Copy-Item $example $settings
    Ok '已从 settings.example.json 生成 config\settings.json（默认值，可自己改）'
} else {
    Warn '没找到 config\settings.example.json，跳过 —— 用内置默认值也能跑'
}

# 密钥文件：留一份带说明的文件。settings.py 会跳过 # 开头的注释行。
$keyFile = Join-Path $cfgDir 'api_key.txt'
if (Test-Path $keyFile) {
    Ok 'config\api_key.txt 已存在，保留不动'
} else {
    $keyText = @(
        '# 把 DeepSeek API Key 写在下面（只放密钥本身，不要引号、不要分号）。',
        '# 申请地址：https://platform.deepseek.com   ->   API keys',
        '# 本文件已在 .gitignore 里，不会被提交到 GitHub。',
        '# 也可以用环境变量代替：TP_API_KEY'
    ) -join $NL
    [System.IO.File]::WriteAllText($keyFile, $keyText + $NL, [System.Text.UTF8Encoding]::new($false))
    Ok '已生成 config\api_key.txt（还没填密钥，见文件里的注释）'
}

# 三份会前配置：优先用仓库模板，没有就生成一份带说明的骨架
$mdBody = @{}
$mdBody['company.md'] = @(
    '# 目标公司背景（会前填，每题都会带进上下文）',
    '',
    '## 公司名 / 业务',
    '',
    '（例：某某科技，做电商推荐与搜索，主要客户是垂直电商）',
    '',
    '## 岗位 JD 关键要求',
    '',
    '（例：Agent Engineer，要求大模型应用落地、检索与排序、RAG）',
    '',
    '## 技术栈线索',
    '',
    '（例：Python、LangGraph、Qwen、私有化部署、K8s）',
    '',
    '## 面试官是谁 / 什么轮次',
    '',
    '（例：技术二面，面试官是 Agent 组 leader）',
    '',
    '## 我特别想强调的匹配点',
    '',
    '（例：我做过推荐系统的召回与粗排，直接对口）'
) -join $NL
$mdBody['known_terms.md'] = @(
    '# 我会、但材料里没写的技术（一行一个，空格/逗号/顿号分隔，# 后面是注释）',
    '',
    '> 提词器用「材料里查无此词」判断面试官问的是不是你准备过的名词。',
    '> 有些东西你明明会，只是简历和项目报告里没写 —— 写在这里，就不会被误判成「没接触过」。',
    '',
    'docker kubernetes k8s nginx linux ubuntu git github gitlab',
    'python java go typescript nodejs react vue html css',
    'mysql postgres redis kafka elasticsearch sqlite mongodb',
    'pytorch transformers langchain langgraph rag rerank jieba bm25',
    'onnx onnxruntime tensorrt vllm ollama whisper sensevoice funasr'
) -join $NL
$mdBody['never_used.md'] = @(
    '# 材料里出现过、但我其实没做过的东西（一行一个，# 后面是注释）',
    '',
    '> 词表只能回答「我材料里有没有」，回答不了「材料里写了但我到底做没做过」。',
    '> 报告里被顺带提过一次的技术，不代表你会用 —— 写在下面，命中就走「坦诚」那套。',
    '',
    '# 例（按需删掉开头的 #）',
    '# kubernetes',
    '# 联邦学习'
) -join $NL

foreach ($name in @('company.md', 'known_terms.md', 'never_used.md')) {
    $p   = Join-Path $cfgDir $name
    $tpl = Join-Path $cfgDir ($name -replace '\.md$', '.example.md')
    if (Test-Path $p) {
        Ok "config\$name 已存在，保留不动"
    } elseif (Test-Path $tpl) {
        Copy-Item $tpl $p
        Ok "config\$name 已从模板生成"
    } else {
        [System.IO.File]::WriteAllText($p, $mdBody[$name] + $NL, [System.Text.UTF8Encoding]::new($false))
        Ok "config\$name 已生成（带说明的骨架，按需改）"
    }
}

# 知识库空了就没法检索（preflight 必 FAIL），先拿示例材料把链路跑通
$knowDir = Join-Path $RepoRoot 'knowledge'
$exKnow  = Join-Path $RepoRoot 'examples\knowledge'
if (-not (Test-Path $knowDir)) { New-Item -ItemType Directory -Force -Path $knowDir | Out-Null }
$own = @(Get-ChildItem -Path $knowDir -Recurse -File -Include *.md, *.txt -ErrorAction SilentlyContinue |
         Where-Object { $_.Name -ne 'README.md' })
if ($own.Count -gt 0) {
    Ok "knowledge\ 里已有 $($own.Count) 份材料，保留不动"
} elseif (Test-Path $exKnow) {
    Copy-Item (Join-Path $exKnow '*') $knowDir -Recurse -Force
    Note 'knowledge\ 里还没有你自己的材料，已拷入示例材料（虚构的，只为验证链路）'
    Note '请把示例删掉，换成你的简历 / 项目报告（一个项目一个文件，文件名写全）'
} else {
    Warn 'knowledge\ 是空的，也没有 examples\knowledge 可拷 —— 检索会 0 命中（preflight 会报 FAIL）'
}

# ── 6. 自检 ───────────────────────────────────────────────────────────
Step 6 $Total '跑全链路自检 tools\preflight.py'
if ($SkipPreflight) {
    Note '-SkipPreflight：跳过。稍后自己跑： .\.venv\Scripts\python.exe -X utf8 tools\preflight.py'
    $rc = 0
} else {
    Note '首次运行要加载 ASR 模型并预热（约 8 秒），全量自检约 20~40 秒 ...'
    $pfArgs = @('-X', 'utf8', (Join-Path $RepoRoot 'tools\preflight.py'))
    if ($Fast) { $pfArgs += '--fast' }
    & $VenvPy @pfArgs
    $rc = $LASTEXITCODE
}

Write-Host ''
Write-Host '====================================================================' -ForegroundColor White
if ($rc -eq 0) {
    Write-Host '  安装完成，自检没有 FAIL。' -ForegroundColor Green
} else {
    Write-Host "  安装步骤都走完了，但自检有 FAIL（退出码 $rc）—— 按上面的提示补齐后再跑一次自检。" -ForegroundColor Yellow
}
Write-Host '====================================================================' -ForegroundColor White
Write-Host ''
Write-Host '接下来：'
Write-Host '  1. 填密钥    config\api_key.txt（一行，只放密钥本身）'
Write-Host '  2. 放材料    knowledge\ 下放你的简历 / 项目报告（.md / .txt）'
Write-Host '  3. 会前配置  config\company.md + known_terms.md + never_used.md'
Write-Host '  4. 复查自检  .\.venv\Scripts\python.exe -X utf8 tools\preflight.py --fast'
Write-Host '  5. 启动      .\scripts\run.ps1'
Write-Host ''
Write-Host '详细说明见 docs\安装教程.md；出问题见 docs\故障排查.md' -ForegroundColor Gray
Write-Host ''

exit $rc
