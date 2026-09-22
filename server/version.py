# -*- coding: utf-8 -*-
"""版本号 —— 全项目只此一处。

装成 exe 之后用户看不到 git log，出问题时报一句「我装的是哪一版」是最基本的事，
所以版本号必须是程序能回答的事实，而不是只写在 CHANGELOG 里。

`tests/test_repo_hygiene.py` 会检查这里和 CHANGELOG.md 顶部的小节标题对得上，
避免两处各写一个数字然后慢慢漂移。

**发版动作**：改这里的数字 → 在 CHANGELOG.md 顶部加同名小节 →（如果要出包）重新打包。
"""

__version__ = '0.9.22'
