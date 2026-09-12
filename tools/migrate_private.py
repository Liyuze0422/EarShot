# -*- coding: utf-8 -*-
"""把「藏在私人副本代码里」的配置搬到 config/ 下，让仓库版能直接用。

背景：这个项目最早是自用副本，语料路径、领域词表、话题词表都是**硬编码在
server/knowledge.py 里**的。公开仓库那份只能放示例占位词 —— 于是就有两个真相：
私人副本能跑但对不上上游，仓库版对得上上游但检索是空的。

搬完之后：算法在仓库版（可以一直升级），你的词表在 config/（.gitignore 挡着，
不会进 GitHub）。两边不再分叉。

默认**只报告不动手**，确认了再加 --write。

用法：
    python tools/migrate_private.py --from <私人副本>/server
    python tools/migrate_private.py --from <私人副本>/server --write
"""
import argparse
import ast
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, 'config')
WANT = ('CORPUS_GLOBS', 'DOMAIN_WORDS', 'TOPIC_TERMS')


def read_constants(path, want=WANT):
    """用 AST 读常量，不 import —— 不执行对方的代码，副作用为零。"""
    if not os.path.exists(path):
        return {}
    tree = ast.parse(open(path, encoding='utf-8').read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in want:
                    try:
                        out[t.id] = ast.literal_eval(node.value)
                    except Exception:
                        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='src', required=True,
                    help='私人副本的 server 目录（里面应有 knowledge.py）')
    ap.add_argument('--write', action='store_true', help='真的写文件（默认只报告）')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的 config 文件')
    ap.add_argument('--knowledge-dir', default=os.path.join(ROOT, 'knowledge'),
                    help='默认资料包根目录（_base 会建在这里）')
    a = ap.parse_args()

    src = os.path.join(a.src, 'knowledge.py')
    if not os.path.exists(src):
        print('找不到 %s' % src)
        return 1
    c = read_constants(src)
    # 模型路径在 main.py、密钥路径在 answer.py，各读各的
    c.update(read_constants(os.path.join(a.src, 'main.py'), ('MODEL_DIR',)))
    c.update(read_constants(os.path.join(a.src, 'answer.py'), ('KEY_FILE',)))

    globs = c.get('CORPUS_GLOBS') or []
    words = c.get('DOMAIN_WORDS') or []
    terms = c.get('TOPIC_TERMS') or {}
    model = c.get('MODEL_DIR') or ''
    keyf = c.get('KEY_FILE') or ''

    print('从 %s 读到：' % src)
    print('  语料 glob      %d 条' % len(globs))
    for g in globs:
        print('      %s' % g)
    print('  领域词          %d 个' % len(words))
    print('      %s' % '、'.join(words[:12]) + ('…' if len(words) > 12 else ''))
    print('  话题词表        %d 个项目：%s' % (len(terms), '、'.join(terms)))
    if model:
        print('  ASR 模型目录    %s%s' % (model, '' if os.path.isdir(model) else '   ⚠️ 目录不存在'))
    if keyf:
        print('  DeepSeek 密钥   %s%s' % (keyf, '' if os.path.isfile(keyf) else '   ⚠️ 文件不存在'))
    print()

    if not a.write:
        print('（只是报告。确认无误后加 --write 落盘）')
        print()
        print('会写这几个文件（都在 config/ 下，已被 .gitignore 挡住）：')
        print('  config/settings.json      corpus_globs' + (' / model_dir' if model else ''))
        if keyf and os.path.isfile(keyf):
            print('  config/api_key.txt        从 %s 复制一份（省得两边跑）' % keyf)
        print('  config/domain_words.txt   领域词表')
        print('  config/topic_terms.json   话题词表')
        print('  config/*.md               个人配置：我会但材料没写的技术、绝不说的话术、公司信息')
        print('另外会建 %s\\_base\\ 并提示你把材料挪进去（不挪也能先跑，见下）。' % a.knowledge_dir)
        return 0

    # ---- settings.json：只补 corpus_globs，其余原样保留 ----
    sp = os.path.join(CFG, 'settings.json')
    cur = {}
    if os.path.exists(sp):
        try:
            cur = json.load(open(sp, encoding='utf-8-sig'))
        except Exception as e:
            print('config/settings.json 解析失败，为安全起见不覆盖：%s' % e)
            return 1
    chg = []
    if globs and (a.force or not cur.get('corpus_globs')):
        cur['corpus_globs'] = globs
        chg.append('corpus_globs')
    elif globs:
        print('settings.json 已有 corpus_globs，跳过（要覆盖加 --force）')
    if model and os.path.isdir(model) and (a.force or not cur.get('model_dir')):
        cur['model_dir'] = model
        chg.append('model_dir')

    # 密钥：复制一份到 config/api_key.txt，别让两个副本共用一个外部文件
    # （官方默认位置就是它，且已被 .gitignore 挡住）
    ap_dst = os.path.join(CFG, 'api_key.txt')
    if keyf and os.path.isfile(keyf) and not os.path.exists(ap_dst):
        try:
            t = open(keyf, encoding='utf-8').read().strip()
            if t:
                with open(ap_dst, 'w', encoding='utf-8') as f:
                    f.write(t + '\n')
                chg.append('api_key.txt（已复制，不打印内容）')
        except Exception as e:
            print('复制密钥失败（不影响其他项）：%s' % e)

    if chg:
        with open(sp, 'w', encoding='utf-8') as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        print('已写 config/settings.json：%s' % '、'.join(chg))

    if words:
        p = os.path.join(CFG, 'domain_words.txt')
        if _ok(p, a):
            with open(p, 'w', encoding='utf-8') as f:
                f.write('# 领域词表：jieba 会切碎、但面试里是关键词的专有词。一行一个。\n')
                f.write('# 由 tools/migrate_private.py 从私人副本搬过来。\n')
                f.write('\n'.join(words) + '\n')
            print('已写 config/domain_words.txt（%d 个词）' % len(words))

    if terms:
        p = os.path.join(CFG, 'topic_terms.json')
        if _ok(p, a):
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(terms, f, ensure_ascii=False, indent=2)
            print('已写 config/topic_terms.json（%d 个项目）' % len(terms))

    # ---- 个人配置 md：不搬的话「专名词表体检」会显示 0 个 ----
    # 2026-09-13 补：原先只搬了 settings / domain_words / topic_terms / 密钥，
    # 漏了这几个。症状很隐蔽 —— 工具照常启动，只是会前补的「我会、但材料里没写
    # 的技术」全丢，于是把明明会的名词判成「没接触过」。
    PERSONAL = ('company.md', 'known_terms.md', 'never_used.md', 'terms_review.md')
    cfg_src = os.path.join(os.path.dirname(os.path.abspath(a.src)), 'config')
    for name in PERSONAL:
        s_ = os.path.join(cfg_src, name)
        if not os.path.isfile(s_):
            continue
        d_ = os.path.join(CFG, name)
        if not _ok(d_, a):
            continue
        shutil.copyfile(s_, d_)
        print('已复制 config/%s（%d 字节）' % (name, os.path.getsize(d_)))

    # ---- 回归数据 + 依赖私人样本的检查脚本 ----
    # 这些也全在 .gitignore 里。不搬过来的话，迁移完 tools/regress.py 会报
    # 「没有可判的项」—— 那就等于没法验证迁移前后质量一致，白白丢掉安全感。
    root_src = os.path.dirname(os.path.abspath(a.src))     # server 的上一级 = 旧副本根
    copied = []
    for rel in ('知识库/题库.json',
                'tests/real_questions.json', 'tests/synthetic_questions.json',
                'tests/regress_expect.json', 'tests/quality_sample.json',
                'tools/test_gap_fp.py', 'tools/test_answer_check.py', 'tools/test_faults.py'):
        s = os.path.join(root_src, rel)
        d = os.path.join(ROOT, rel)
        if os.path.exists(s) and not os.path.exists(d):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            copied.append(rel)
    if copied:
        print()
        print('已搬回归数据 %d 份（都在 .gitignore 里，不会提交）：' % len(copied))
        for rel in copied:
            print('  %s' % rel)
        print('现在可以跑 python tools/regress.py 验证质量没变。')

    # ---- 提示材料要不要挪进 _base ----
    base = os.path.join(a.knowledge_dir, '_base')
    os.makedirs(base, exist_ok=True)
    inside = _inside_globs(globs, base)
    print()
    if inside:
        print('语料 glob 已经指向 %s，可以直接跑。' % a.knowledge_dir)
    else:
        print('语料 glob 指向的还是老位置（%s 之外）。两种走法：' % a.knowledge_dir)
        print('  A) 先就这么跑 —— 默认 corpus_profile 为空，行为和你现在完全一样。')
        print('  B) 想用「按公司分包」：把通用材料拷/移到 %s，' % base)
        print('     再在 config/settings.json 里填 "corpus_profile": "<公司名>"。')
        print('     （别在原位置留副本，否则会被当成两份材料。）')
    print()
    print('验证：python tools/preflight.py')
    return 0


def _ok(path, a):
    if os.path.exists(path) and not a.force:
        print('%s 已存在，跳过（要覆盖加 --force）' % path)
        return False
    return True


def _inside_globs(globs, base):
    r"""语料是不是真的来自 knowledge/ 下。

    只看"glob 的字面前缀是不是落在 knowledge 里"——不能反过来判断
    （G:\材料\*.md 的前缀是 G:\材料，它也"包含"knowledge，但那不代表语料来自 knowledge）。
    """
    b = os.path.normcase(os.path.abspath(base))
    for g in globs:
        prefix = g.split('*')[0].rstrip('\\/')
        head = os.path.normcase(os.path.abspath(prefix or '.'))
        if head == b or head.startswith(b + os.sep):
            return True
    return False


if __name__ == '__main__':
    sys.exit(main())
