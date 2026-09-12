# -*- coding: utf-8 -*-
"""仓库卫生检查：防止个人路径、密钥、私人材料被误提交到公开仓库。

CI 里单独跑（见 .github/workflows/ci.yml 的 hygiene job）。

注意：本文件自身的字面量会被自己扫到，所以凡是「本文件里要出现的敏感词」
都拆成两段拼接（'项目' + '备用'），这样源码里不存在那个连续字符串，
测试既能扫全仓库、又不会自命中。
"""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# _realdata：真实面试录音/转写的工作目录（.gitignore 已挡，见 test_private_conversation_data_is_ignored）。
# 这两条测试走的是**文件系统**而不是 git，所以本地一旦放了录音，扫描就会把
# 920MB 的音频和带本机路径的临时脚本一起算进来，误报成"要提交的东西"。
# 直接跳过整个目录，同时用 test_private_conversation_data_is_ignored 保证它确实被忽略。
SKIP_DIRS = {'.git', '.venv', 'models', 'logs', '.tmp', '.pytest_cache', '__pycache__',
             'node_modules', 'knowledge', '_realdata'}
TEXT_EXT = {'.py', '.ps1', '.md', '.json', '.txt', '.yml', '.yaml', '.bat', '.js', '.html', '.toml'}

# 本机出现过的私人路径与密钥特征。公开仓库里一个都不允许出现。
FORBIDDEN = [
    # 正斜杠、反斜杠两种写法都要挡（Windows 上两种都常见，踩过一次漏检）
    (r'G:[\\/][^\r\n]{0,24}' + '项目' + '备用', '私人工作目录路径'),
    (r'G:[\\/][^\r\n]{0,24}interview-asr', '本机模型目录'),
    (r'G:[\\/][^\r\n]{0,24}deepseek-harness', '本机 harness 路径'),
    (r'F:[\\/][^\r\n]{0,24}python_tool', '本机 Python 安装路径'),
    ('deepseek' + '密钥', '个人密钥文件名'),
    (r'sk-[A-Za-z0-9]{20,}', '疑似真实 API Key'),
]


def _git_ignored(paths):
    """用 git 自己的忽略规则过滤掉"本来就不会提交"的文件。

    为什么必须问 git、而不是再往 SKIP_DIRS 里加一条：这些检查走的是**文件系统**，
    看不到 .gitignore。历史上已经栽过两次 —— 先是 _realdata/（真实录音），
    再是 config/settings.json（含本机材料路径）。手写名单永远会漏下一个。
    """
    rel = [os.path.relpath(p, ROOT).replace('\\', '/') for p in paths]
    if not rel:
        return set()
    try:
        r = subprocess.run(['git', 'check-ignore', '-z', '--stdin'], cwd=ROOT,
                           input='\0'.join(rel) + '\0',
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return set()
    return {os.path.normcase(os.path.join(ROOT, x.replace('/', os.sep)))
            for x in r.stdout.split('\0') if x}


def _iter_text_files():
    cands = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXT or name.endswith('.example') or name in ('LICENSE', '.gitignore', '.gitattributes'):
                cands.append(os.path.join(dirpath, name))
    skip = _git_ignored(cands)
    for p in cands:
        if os.path.normcase(p) not in skip:
            yield p


def test_no_personal_paths_or_secrets():
    hits = []
    for path in _iter_text_files():
        try:
            text = open(path, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        for pattern, label in FORBIDDEN:
            for m in re.finditer(pattern, text):
                line = text[:m.start()].count(chr(10)) + 1
                hits.append('%s:%d 命中「%s」: %s' % (os.path.relpath(path, ROOT), line, label, m.group(0)))
    assert not hits, '发现不应提交到公开仓库的内容：' + chr(10) + chr(10).join(hits)


def test_no_large_files_committed():
    limit = 20 * 1024 * 1024
    big = []
    cands = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            cands.append(os.path.join(dirpath, name))
    skip = _git_ignored(cands)
    for p in cands:
        if os.path.normcase(p) in skip:
            continue
        if os.path.getsize(p) > limit:
            big.append('%s (%.1f MB)' % (os.path.relpath(p, ROOT), os.path.getsize(p) / 2 ** 20))
    assert not big, '仓库里混进了大文件（模型请用 tools/download_model.py 下载）：' + ', '.join(big)


def test_required_files_exist():
    required = (
        'README.md', 'README.en.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md',
        'requirements.txt', 'pyproject.toml', '.gitignore',
        'config/settings.example.json', 'config/api_key.example.txt',
        'server/main.py', 'server/settings.py', 'ui/app.py',
        'scripts/setup.ps1', 'scripts/run.ps1',
        'docs/安装教程.md', 'docs/使用说明书.md', 'docs/配置手册.md',
        'wiki/Home.md',
    )
    missing = [rel for rel in required if not os.path.exists(os.path.join(ROOT, rel))]
    assert not missing, '缺少文件：' + ', '.join(missing)


def test_personal_config_files_are_not_shipped_as_templates():
    """config/ 下的个人文件必须只以 .example.* 形式随仓库发布。"""
    personal = ('company.md', 'known_terms.md', 'never_used.md', 'settings.json', 'api_key.txt')
    for name in personal:
        example = name.replace('.md', '.example.md').replace('.json', '.example.json').replace('.txt', '.example.txt')
        assert os.path.exists(os.path.join(ROOT, 'config', example)), '缺少模板 config/' + example

def test_private_conversation_data_is_ignored():
    """真实提问 / 回归期望 / 录音转写都必须留在本地。

    这些文件一旦被 git add -A 带上去，就是公开仓库里的面试原文事故。.gitignore 负责挡，
    这条测试负责保证「挡的规则没被谁手滑删掉」—— 加它的直接原因：准备用大量真实录音
    扩回归集之前，先确认数据放进来不会漏出去。
    """
    if not os.path.isdir(os.path.join(ROOT, '.git')):
        return          # 不是 git 工作副本（下载 ZIP 的那种），无从检查也无需检查
    must_ignore = (
        'tests/real_questions.json',
        'tests/synthetic_questions.json',
        'tests/regress_expect.json',
        'tests/questions_extra.json',       # 换个名字也要被 tests/*questions*.json 兜住
        '_realdata/transcript.txt',
        '_realdata/示例录音.m4a',                 # 整个目录（含真实录音）都必须在忽略名单里
        'logs/session_1.jsonl',
        '录音.srt',
    )
    leaked = []
    for rel in must_ignore:
        r = subprocess.run(['git', 'check-ignore', '-q', rel], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            leaked.append(rel)
    assert not leaked, ('这些路径没有被 .gitignore 忽略，一旦提交就是面试原文泄露：'
                        + ', '.join(leaked))


def test_generated_artifacts_from_private_material_are_ignored():
    """由你的材料**生成**的东西，和材料本身同级 —— 同样不能进公开仓库。

    这条是事故复盘加上的：`tools/build_bank.py` 会把材料跑成 380+ 条
    「口语问法 → 可直接照念的答案」，落在 `知识库/题库.json`。
    它当时不在 .gitignore 里，一次 `git add -A` 就把它带上了公开仓库
    （已强推抹掉，见 CHANGELOG 0.9.3）。**生成物比原始材料更容易被漏掉**，
    因为原始材料你知道要藏，生成物只觉得是"跑出来的中间文件"。
    """
    if not os.path.isdir(os.path.join(ROOT, '.git')):
        return
    must_ignore = (
        '知识库/题库.json',
        'bank.json',
        '另一个题库.json',
        'server/__pycache__/bank.cpython-312.pyc',
    )
    leaked = []
    for rel in must_ignore:
        r = subprocess.run(['git', 'check-ignore', '-q', rel], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            leaked.append(rel)
    assert not leaked, ('这些由私人材料生成的产物没有被忽略：' + ', '.join(leaked))
