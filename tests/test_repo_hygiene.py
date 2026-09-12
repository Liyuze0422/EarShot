# -*- coding: utf-8 -*-
"""仓库卫生检查：防止个人路径、密钥、私人材料被误提交到公开仓库。

CI 里单独跑（见 .github/workflows/ci.yml 的 hygiene job）。

注意：本文件自身的字面量会被自己扫到，所以凡是「本文件里要出现的敏感词」
都拆成两段拼接（'项目' + '备用'），这样源码里不存在那个连续字符串，
测试既能扫全仓库、又不会自命中。
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {'.git', '.venv', 'models', 'logs', '.tmp', '.pytest_cache', '__pycache__', 'node_modules', 'knowledge'}
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


def _iter_text_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXT or name.endswith('.example') or name in ('LICENSE', '.gitignore', '.gitattributes'):
                yield os.path.join(dirpath, name)


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
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = os.path.join(dirpath, name)
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