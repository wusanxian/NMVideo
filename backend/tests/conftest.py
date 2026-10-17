"""pytest 共享配置：隔离数据库 fixture。

业务模块（storyboard/assets/tracks）通过 `from .db import db` 持有单例引用，
fixture 需要把各模块的引用一起换成临时库实例，避免测试写坏真实数据库。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# 模块级单例指向临时库（真实 aivideo.db 永不被测试触碰）
os.environ.setdefault(
    "AIVIDEO_DB", str(Path(tempfile.mkdtemp(prefix="aivideo-test-singleton-")) / "singleton.db")
)

import pytest  # noqa: E402

from app.db import Database  # noqa: E402

_PATCHED = ("app.storyboard", "app.assets", "app.tracks")


@pytest.fixture()
def fresh_db(tmp_path):
    """每个测试一个独立 SQLite 文件；storyboard/assets/tracks 全部指向它。"""
    import app.db as db_module
    import app.assets  # noqa: F401
    import app.storyboard  # noqa: F401
    import app.tracks  # noqa: F401

    target = Database(tmp_path / "test.db")
    saved = {m: getattr(sys.modules[m], "db") for m in _PATCHED}
    saved_singleton = db_module.db
    db_module.db = target
    for m in _PATCHED:
        setattr(sys.modules[m], "db", target)
    try:
        yield target
    finally:
        db_module.db = saved_singleton
        for m, ref in saved.items():
            setattr(sys.modules[m], "db", ref)
