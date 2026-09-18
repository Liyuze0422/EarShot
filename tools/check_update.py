# -*- coding: utf-8 -*-
r"""查 EarShot 有没有新版本。

    python tools\check_update.py           # 走缓存（6 小时内不重复请求）
    python tools\check_update.py --force   # 强制重新查

只发一个 GET 请求，不改任何文件。网络不通会告诉你「查不到」，不报错 —— 那是常态。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server'))
sys.stdout.reconfigure(encoding='utf-8')

import update  # noqa: E402

if __name__ == '__main__':
    r = update.check(force='--force' in sys.argv)
    print(update.summary(r))
    if r.get('has_update'):
        print('-' * 64)
        print(r.get('notes', '')[:1200])
        print('-' * 64)
        print('更新说明与下载：%s' % r['url'])
    elif r.get('ok'):
        print('本地版本 %s，远端最新 %s（%s 发布）' % (r['current'], r['latest'], r.get('published') or '?'))
