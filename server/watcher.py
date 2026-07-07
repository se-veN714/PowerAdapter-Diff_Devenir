"""PAdif 文件监听（Phase 3：文件自动检测提交）。

设计约束（见 DEVELOPMENT.md §6）：
- **零第三方依赖**：本环境 managed Python 无法装包，故用轮询（mtime + 内容哈希）
  而非 watchdog，避免引入无法安装的服务/库。
- **只做检测，不做提交**：本模块只负责「哪些被监听文件的内容变了」，
  真正的提交逻辑在 app.py 的 `auto_commit()`，保持关注点分离、易于单测。

判定策略：
- 注册时记录文件当前 (mtime, sha1)；
- 每次 `poll()` 重新探测，仅当**内容哈希变化**才视为改动
  （避免编辑器仅刷新 mtime 但内容未变时产生空提交）；
- 文件不存在则本周期跳过，待其出现后再检测。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path


class WatchRegistry:
    """被监听路径的注册表；线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # path(resolved) -> (mtime, sha1)
        self._state: dict[str, tuple[float, str]] = {}

    def register(self, path: str) -> bool:
        """加入监听；已存在则返回 False，否则记录当前快照并返回 True。"""
        rp = str(Path(path).resolve())
        with self._lock:
            if rp in self._state:
                return False
            self._state[rp] = self._probe_safe(rp)
            return True

    def unregister(self, path: str) -> bool:
        rp = str(Path(path).resolve())
        with self._lock:
            return self._state.pop(rp, None) is not None

    def list(self) -> list[str]:
        with self._lock:
            return list(self._state.keys())

    def poll(self) -> list[tuple[str, str]]:
        """返回自上次 poll 以来**内容发生变化**的 (resolved_path, content) 列表。"""
        changed: list[tuple[str, str]] = []
        with self._lock:
            items = list(self._state.items())
        for rp, _ in items:
            p = Path(rp)
            try:
                mtime, h = self._probe(p)
            except (FileNotFoundError, UnicodeDecodeError, OSError):
                continue  # 文件暂不存在 / 非 UTF-8 / 读取失败，跳过本周期（不拖垮整个循环）
            with self._lock:
                old = self._state.get(rp)
            if old is None:
                continue
            if h != old[1]:  # 仅当内容哈希变化
                try:
                    content = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                changed.append((rp, content))
                with self._lock:
                    if rp in self._state:
                        self._state[rp] = (mtime, h)
        return changed

    @staticmethod
    def _probe(p: Path) -> tuple[float, str]:
        st = p.stat()
        content = p.read_text(encoding="utf-8")
        return st.st_mtime, hashlib.sha1(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _probe_safe(rp: str) -> tuple[float, str]:
        try:
            return WatchRegistry._probe(Path(rp))
        except FileNotFoundError:
            return (0.0, "")
