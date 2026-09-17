# -*- coding: utf-8 -*-
"""一键切换资料包：换一家面试 = 一条命令。

为什么要有它
------------
原来换一场面试要动 6 个地方 + 跑 3 个脚本 + 重启，漏一步的症状是
**答案里混进上一家公司的材料**（薪资数字、城市、创始人称呼），当场很难发现。
实测 2026-09-16：切到新包之后，基础材料里还留着上一家的整段话术，
它们照样被检索命中、照样会被照念。

用法
----
    python tools/switch.py --list                # 有哪些包、当前用哪个
    python tools/switch.py <包名>                 # 一键切（配置 + 题库 + 扩展词 + 自检 + 重启）
    python tools/switch.py <包名> --check         # 预演，不写任何东西
    python tools/switch.py <包名> --fast          # 只切配置和题库指针，不重建
    python tools/switch.py <包名> --no-restart    # 不重启后端
    python tools/switch.py <包名> --take-company  # 让包里的 company.md 接管

按顺序做（每步都能单独失败并说清楚）：
  1. 校验包，数材料数/块数
  2. 串场扫描：在 knowledge/_base/ 里找**其它包**的痕迹（最容易被忽略的坑）
  3. 公司画像：config/company.md 会遮蔽包里的 company.md，检测到就提醒
  4. 改 config/settings.json 的 corpus_profile（原文件备份成 settings.json.bak）
  5. 题库：题库_<包名>.json 存在且不比材料旧 → 直接复用；否则重建
  6. 扩展词：kb_expand（增量，只给没见过的块生成）
  7. 自检：kb_audit 的硬切/丢弃 + tools/test_gap_fp.py
  8. 重启后端 + 浮窗

⚠ 本文件只写泛化说法（"包名""某公司"）。真实公司名只该出现在 knowledge/ 下（.gitignore 挡着）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
import knowledge        # noqa: E402
import settings         # noqa: E402

SETTINGS_PATH = os.path.join(ROOT, "config", "settings.json")
COMPANY_FILE = os.path.join(ROOT, "config", "company.md")
BANK_DIR = os.path.join(ROOT, "知识库")


def venv_python():
    """优先用仓库自带的解释器 —— 用系统 python 起后端会缺依赖。"""
    p = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return p if os.path.exists(p) else sys.executable


def run_tool(script, *args, timeout=2400, quiet=False):
    cmd = [venv_python(), "-X", "utf8", os.path.join(HERE, script)] + list(args)
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    if not quiet:
        print(out.rstrip()[-4000:])
    return p.returncode, out


def names():
    profs = knowledge.list_profiles()
    out = []
    for p in profs:
        out.append(p[0] if isinstance(p, (tuple, list)) else str(p))
    return out


def bank_file(prof):
    p = os.path.join(BANK_DIR, "题库_%s.json" % prof)
    return p if os.path.exists(p) else None


def bank_count(path):
    if not path or not os.path.exists(path):
        return 0
    try:
        d = json.load(open(path, encoding="utf-8"))
        return len(d.get("items", d if isinstance(d, list) else []))
    except Exception:
        return 0


def material_files(prof):
    """当前包实际会读的材料：knowledge/_base/ + knowledge/<包>/。"""
    root = settings.knowledge_root()
    out = []
    for sub in ("_base", prof):
        d = os.path.join(root, sub)
        for dirpath, _dirs, files in os.walk(d):
            out += [os.path.join(dirpath, f) for f in files if f.endswith(".md")]
    return out


def count_material(prof):
    old = knowledge.active_profile()
    try:
        knowledge.set_profile(prof)
        docs = knowledge.load_docs()
        n = 0
        for d in docs:
            n += len(knowledge.chunk(d[1], d[0]))
        return len(docs), n
    finally:
        knowledge.set_profile(old or None)


def newest_material_mtime(prof):
    newest = 0.0
    for f in material_files(prof):
        try:
            newest = max(newest, os.path.getmtime(f))
        except OSError:
            pass
    return newest


def lint_cross(target):
    """扫 _base 里有没有**其它包**的痕迹 —— 换场最容易翻车的地方。

    实测（2026-09-16）：换到新包之后，_base 里还留着上一家的整段话术
    （薪资区间、城市、创始人称呼），它们照样被检索命中、照样会被照念。
    这不是新包的问题，是旧场的残留在"抢排名"。
    """
    import glob as _glob
    words = {}
    for p in names():
        if p == target:
            continue
        ws = {p}
        # 包名前两个字往往就是简称（"某某科技" -> "某某"）
        if len(p) >= 2:
            ws.add(p[:2])
        words[p] = [w for w in ws if len(w) >= 2]
    if not words:
        return []
    base = os.path.join(settings.knowledge_root(), "_base")
    hits = []
    for f in sorted(_glob.glob(os.path.join(base, "**", "*.md"), recursive=True)):
        try:
            lines = open(f, encoding="utf-8", errors="ignore").read().splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            for prof, ws in words.items():
                for w in ws:
                    if w in ln:
                        hits.append((os.path.relpath(f, ROOT), i, prof, ln.strip()[:100]))
                        break
                else:
                    continue
                break
    return hits


def read_settings():
    if not os.path.exists(SETTINGS_PATH):
        return {}
    return json.load(open(SETTINGS_PATH, encoding="utf-8-sig"))


def write_settings(cfg, do_it):
    if not do_it:
        return
    if os.path.exists(SETTINGS_PATH):
        shutil.copy2(SETTINGS_PATH, SETTINGS_PATH + ".bak")
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def healthz():
    import urllib.request
    port = settings.port_list()[0]
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def restart_backend():
    ps = os.path.join(ROOT, "scripts", "run.ps1")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, "--stop"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    time.sleep(2)
    # --no-library：switch.py 自己已经把库切好了，不能再弹一次选库窗口把流程打断。
    cmd = ("Start-Process -FilePath '%s' -ArgumentList @('-X','utf8','tools\\launch.py','--no-library') "
           "-WorkingDirectory '%s' -WindowStyle Hidden" % (venv_python(), ROOT))
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    t0 = time.time()
    while time.time() - t0 < 90:
        h = healthz()
        if h and h.get("ready"):
            return h
        time.sleep(2)
    return None


def cmd_list():
    cur = knowledge.active_profile()
    allp = names()
    root = settings.knowledge_root()
    print("资料包目录: %s" % root)
    print("当前启用  : %s" % (cur or "(未启用，扫 knowledge/ 全量)"))
    print()
    print("%-16s %-6s %-8s %-10s %s" % ("包名", "材料", "块数", "题库", "备注"))
    print("-" * 62)
    for p in allp:
        try:
            nf, nc = count_material(p)
        except Exception as e:
            nf, nc = -1, -1
        b = bank_file(p)
        note = []
        if p == cur:
            note.append("← 当前")
        if not b:
            note.append("题库要重建")
        print("%-16s %-6s %-8s %-10s %s" % (
            p, nf, nc, ("%d 条" % bank_count(b)) if b else "无", "  ".join(note)))
    print()
    print("切换: python tools/switch.py <包名>")


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("profile", nargs="?", help="目标资料包名")
    ap.add_argument("--list", action="store_true", help="列出所有资料包")
    ap.add_argument("--check", action="store_true", help="预演，不写任何东西")
    ap.add_argument("--fast", action="store_true", help="只切配置和题库指针，不重建/不自检")
    ap.add_argument("--clean", action="store_true",
                    help="题库覆盖重来（默认 --merge：把旧题库并进来，不缩水）")
    ap.add_argument("--no-restart", action="store_true", help="不重启后端")
    ap.add_argument("--take-company", action="store_true",
                    help="让包里的 company.md 接管（把 config/company.md 改名成 .bak）")
    args = ap.parse_args()

    if args.list or not args.profile:
        cmd_list()
        return 0

    target = args.profile.strip()
    allp = names()
    if target not in allp:
        print("没有这个资料包：%s" % target)
        print("现有：" + (", ".join(allp) if allp else "(一个都没有)"))
        print("新建一个：在 knowledge/ 下建 <包名>/ 目录，放 .md 材料 + company.md")
        return 2

    cur = knowledge.active_profile()
    dry = args.check
    print("=" * 62)
    print("%s → %s%s" % (cur or "(全量)", target, "   [预演，不写文件]" if dry else ""))
    print("=" * 62)

    nf, nc = count_material(target)
    print("\n[1/8] 材料：%d 份 / %d 块" % (nf, nc))

    print("\n[2/8] 串场扫描（knowledge/_base/ 里有没有别的包）")
    cross = lint_cross(target)
    if cross:
        print("  ⚠ 发现 %d 处 —— 这些内容会被检索到并照念，换场前应该清掉：" % len(cross))
        for f, i, prof, ln in cross[:20]:
            print("     %s:%d  [%s]  %s" % (f, i, prof, ln))
        if len(cross) > 20:
            print("     …还有 %d 处" % (len(cross) - 20))
        print("     处理办法：改写成通用说法，或把整份文件改成 _开头.md（下划线开头不参与检索）")
    else:
        print("  ✓ 干净")

    print("\n[3/8] 公司画像")
    pkg_company = os.path.join(settings.knowledge_root(), target, "company.md")
    if os.path.exists(COMPANY_FILE):
        if args.take_company:
            if dry:
                print("  （预演）会把 config/company.md 改名成 company.md.bak，让包里的接管")
            else:
                shutil.move(COMPANY_FILE, COMPANY_FILE + ".bak")
                print("  ✓ config/company.md → company.md.bak，改由 knowledge/%s/company.md 接管" % target)
        else:
            print("  ⚠ config/company.md 存在，它**遮蔽**包里的 company.md（answer._company_path 的优先级）")
            print("    也就是说：换了包，公司介绍还是旧的。")
            print("    包里有 company.md 就加 --take-company 让包接管；否则手改 config/company.md")
            if os.path.exists(pkg_company):
                a = open(COMPANY_FILE, encoding="utf-8").read().strip()
                b = open(pkg_company, encoding="utf-8").read().strip()
                print("    两份内容%s" % ("相同" if a == b else "**不同**"))
    elif os.path.exists(pkg_company):
        print("  ✓ 用包里的 knowledge/%s/company.md（config/company.md 不存在）" % target)
    else:
        print("  ⚠ 两边都没有公司介绍 —— 每题都不会带公司背景")

    print("\n[4/8] 写 config/settings.json 的 corpus_profile")
    cfg = read_settings()
    old_prof = cfg.get("corpus_profile")
    cfg["corpus_profile"] = target
    if dry:
        print("  （预演）corpus_profile: %r → %r" % (old_prof, target))
    else:
        write_settings(cfg, True)
        print("  ✓ corpus_profile: %r → %r（原文件备份为 settings.json.bak）" % (old_prof, target))

    print("\n[5/8] 题库")
    bpath = bank_file(target)
    stale = False
    if bpath:
        nm = newest_material_mtime(target)
        stale = os.path.getmtime(bpath) < nm - 60
    if bpath and not stale:
        print("  ✓ 复用 知识库/%s（%d 条），不比材料旧" % (os.path.basename(bpath), bank_count(bpath)))
    elif args.fast:
        print("  --fast：跳过重建（题库会缺这个包的内容）")
    elif dry:
        print("  （预演）需要重建：题库_%s.json %s" % (target, "比材料旧" if bpath else "不存在"))
    else:
        # 默认 --merge，**不要**用 --force：覆盖式重建会让题库变小（LLM 每次生成的问法不同，
        # 实测 502 → 487），而题库一小，追问预案就哑了（它要求同一章节命中 ≥2 条），
        # 症状看着像代码坏了、其实只是题库缩水 —— 见 tools/build_bank.py 里的原注。
        mode = "--force" if args.clean else "--merge"
        print("  %s，重建中（约 1 分钟，%s）…" % (
            "题库比材料旧" if bpath else "没有这个包的题库",
            "覆盖重来" if args.clean else "并入旧题库、只去重不删"))
        rc, out = run_tool("build_bank.py", mode, quiet=True)
        tail = [l for l in out.splitlines() if l.startswith("生成 ") or l.startswith("已写")]
        print("  " + ("\n  ".join(tail) if tail else out.strip()[-600:]))
        if rc != 0:
            print("  ⚠ build_bank 返回 %d，题库可能不完整" % rc)

    print("\n[6/8] 扩展词（增量）")
    if args.fast:
        print("  --fast：跳过")
    elif dry:
        print("  （预演）会跑 kb_expand.py，只给没见过的块生成")
    else:
        rc, out = run_tool("kb_expand.py", quiet=True)
        tail = [l for l in out.splitlines() if "完成" in l or "块" in l][-2:]
        print("  " + ("\n  ".join(tail) if tail else out.strip()[-400:]))

    print("\n[7/8] 自检")
    if args.fast or dry:
        print("  （跳过）")
    else:
        rc, out = run_tool("kb_audit.py", quiet=True)
        keep = [l.strip() for l in out.splitlines()
                if "切块" in l or "硬切" in l or "丢弃" in l or "中位" in l or "重合的块对" in l]
        for l in keep[:6]:
            print("  " + l)
        rc2, out2 = run_tool("test_gap_fp.py", quiet=True)
        g = [l.strip() for l in out2.splitlines() if l.startswith("gap 判据")]
        print("  " + (g[0] if g else ("test_gap_fp 返回 %d" % rc2)))

    print("\n[8/8] 重启后端")
    if args.fast or dry or args.no_restart:
        print("  （跳过）—— 配置要生效请手跑 scripts/run.ps1 --stop 再起 启动提词器.bat")
    else:
        print("  停后端 + 浮窗，重新拉起…")
        h = restart_backend()
        if h:
            dev = h.get("device")
            print("  ✓ 后端就绪：端口 %s%s" % (
                h.get("port"), ("，采集设备 " + dev) if dev else "（采集设备还在枚举）"))
        else:
            print("  ⚠ 90 秒内没等到 healthz —— 看 logs/launch.log")

    print("\n完成。切回上一家：python tools/switch.py %s" % (cur or "<包名>"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
