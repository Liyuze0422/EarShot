# -*- coding: utf-8 -*-
"""模型下载的收尾核对不许误报。

实测（2026-09-14，全新 clone 跑一遍 setup.ps1）：download_model.py 把
configuration.json 判成「还缺」，而它明明已经下好了 —— 因为阈值写死了 1000 字节，
而这个文件本身只有 56 字节。

后果不轻：**每一个装成功的用户都会看到「[!] 还缺 1 个文件」并拿到退出码 1**，
以为是自己装坏了；setup.ps1 也会因此把这次安装记成失败。
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'download_model', os.path.join(ROOT, 'tools', 'download_model.py'))
dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm)

# 下载成功后各文件的真实字节数（2026-09-14 实测）
REAL = {
    'model_quant.onnx': 241216270,
    'tokens.json': 352064,
    'am.mvn': 11203,
    'config.yaml': 1855,
    'configuration.json': 56,
    'chn_jpn_yue_eng_ko_spectok.bpe.model': 377341,
}


def test_every_threshold_is_below_the_real_file_size():
    """阈值必须小于真实大小，否则每个用户都会看到"还缺文件"。"""
    for f, n in REAL.items():
        lo = dm.MIN_BYTES.get(f)
        if lo is None:
            continue          # bpe 文件的阈值在 __main__ 里单独给，见下面的断言
        assert lo < n, '%s 的阈值 %d 不小于真实大小 %d 字节' % (f, lo, n)


def test_smallest_file_is_not_judged_by_a_kilobyte_threshold():
    """configuration.json 只有 56 字节 —— 绝不能按"小于 1KB 就算没下全"判它。"""
    assert dm.MIN_BYTES['configuration.json'] < 200


def test_all_required_files_have_a_threshold():
    """FILES 里每个文件都得有阈值，不能靠 .get(f, 1) 兜底成"存在即合格"。"""
    for f in dm.FILES:
        assert f in dm.MIN_BYTES, f

# ── 下到哪：数据根，不是代码目录（0.9.22）──────────────────────────────
# 打包成 exe 后两者不是一回事：__file__ 在 _internal\（onedir 的 _MEIPASS，代码目录），
# 而 server/settings.py 的 _split_roots() 找模型看的是**数据**目录（exe 那一层）。
# 照 __file__ 推的话，230MB 会下进 _internal\models\ —— 一个后端永远不看的地方，
# 用户下完仍然被告知「模型目录不存在」。启动器用 TP_DATA_ROOT 把数据根传下来。
def test_data_root_prefers_the_launcher_value(monkeypatch, tmp_path):
    monkeypatch.setenv('TP_DATA_ROOT', str(tmp_path))
    assert dm._data_root() == str(tmp_path)


def test_data_root_falls_back_to_repo_root_in_source_mode(monkeypatch):
    monkeypatch.delenv('TP_DATA_ROOT', raising=False)
    assert dm._data_root() == ROOT


def test_dest_lives_under_the_chosen_root():
    assert dm.DEST == os.path.join(dm.ROOT, 'models', 'SenseVoiceSmall-onnx')
    assert dm.ROOT == dm._data_root()
