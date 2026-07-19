"""PAdif 后端服务（MVP：Python 标准库 http.server，零依赖）。

说明：原计划使用 FastAPI，但当前运行环境无法安装第三方包（OpenSSL/网络限制），
故 MVP 先用标准库实现，保证「最小实现即可运行」。路由与业务逻辑已与框架解耦，
后续可平滑迁移到 FastAPI / uvicorn（仅替换本文件的传输层，store/differ/version 不变）。

提供两类端点：
  - /api/*  ：机器可读 JSON（见 DEVELOPMENT.md §5）
  - /frag/* ：htmx 使用的 HTML 片段（服务端渲染高亮）
  - /        ：单页应用入口；/web/* ：静态资源
"""

from __future__ import annotations

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import store
import version
import watcher as watcher_mod
import threading
import time
import subprocess
import platform
from differ import diff_sentences, build_stats, summarize, to_dict

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"

store.init_db()

# ---------- 文件监听（Phase 3 / 防误提交增强） ----------
WATCHER = watcher_mod.WatchRegistry()
_WATCH_INTERVAL = 2.0       # 轮询间隔（秒）
_WATCH_DEBOUNCE = float(os.environ.get("PADIF_WATCH_DEBOUNCE", "30.0"))        # 稳定窗口（秒）：文件连续 N 秒无变化才视为「写完了」
_WATCH_MIN_INTERVAL = float(os.environ.get("PADIF_WATCH_MIN_INTERVAL", "180.0"))  # 最小提交周期（秒）：两次自动提交至少间隔 N 秒
_WATCH_CLOSE_PROC = os.environ.get("PADIF_WATCH_CLOSE_PROC", "Obsidian.exe")   # 可选：监听该进程退出即视为「写完关闭」，强制 flush；置空则禁用


def auto_commit(resolved_path: str, content: str) -> dict:
    """被监听文件发生变化时自动提交一个版本。

    - 若该文章此前无版本（首次被监听到变化），以 major「初始快照」起步；
    - 否则按 patch 自动递增，message 标注「自动保存（检测到文件变更）」。
    - 复用与手动提交完全相同的 store / version 路径，保证版本语义一致。
    """
    title = Path(resolved_path).stem
    aid = store.save_article(resolved_path, title)
    latest = store.get_latest_version(aid)
    if latest:
        prev_content = latest["content"]
        kind = "patch"
        message = "自动保存（文件稳定后）"
    else:
        prev_content = ""
        kind = "major"
        message = "开始监听（初始快照）"
    stats = build_stats(prev_content, content)
    ver = version.bump(latest["version"] if latest else None, kind)
    vid = store.save_version(aid, content, message, ver, kind, stats)
    return {"article_id": aid, "version_id": vid, "version": ver, "kind": kind}


def _editor_present(proc_name: str) -> bool | None:
    """检测某进程是否正在运行。无法判定时返回 None（不触发关闭逻辑）。

    - Windows：tasklist 按 IMAGENAME 过滤（注意中文 Windows 下 tasklist 输出为
      GBK，故用字节捕获 + errors="replace" 解码，避免 UTF-8 解码崩溃）；
    - 其他平台：pgrep -f。
    返回 True/False，失败时 None（避免误判导致误提交）。
    """
    if not proc_name:
        return None
    try:
        if platform.system().lower().startswith("win"):
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/NH", "/FO", "CSV"],
                capture_output=True, timeout=5,
            )
            out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
            return proc_name.lower() in out.lower()
        else:
            r = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True, timeout=5,
            )
            return bool(r.stdout.decode("utf-8", "replace").strip())
    except Exception:
        return None


def _watcher_loop() -> None:
    """后台守护线程：周期性 poll + 三道闸门，贴合「创作者掌控节奏」。

    写文章是**间断、不可预测**的——长思考停顿、多次短促修改交错。
    自动提交只是便利，真正的思路回顾依赖创作者**手动提交（带 message）**。
    故设三道闸门：

    1) 稳定窗口（_WATCH_DEBOUNCE）：文件连续 N 秒无变化才视为「写完了」。
       自动保存的连续写入会被折叠为「停顿即提交」，不刷屏。
    2) 最小提交周期（_WATCH_MIN_INTERVAL）：两次自动提交至少间隔 N 秒，
       避免一段长写作中多次停顿被拆成多个版本。
    3) 关闭即提交（_WATCH_CLOSE_PROC）：若配置了该编辑器进程，
       当它退出（你「写完并关闭 Obsidian」），立即 flush 当前所有脏文件，
       不受上面两道闸门限制——这是最明确的「写完」信号。
    """
    last_change: dict[str, float] = {}   # resolved_path -> 最近一次内容变化时间
    last_commit: dict[str, float] = {}    # resolved_path -> 最近一次自动提交时间
    was_up: bool | None = _editor_present(_WATCH_CLOSE_PROC) if _WATCH_CLOSE_PROC else None

    while True:
        try:
            # 1) 检测变化（仅内容哈希变才记录，避免 mtime 抖动）
            for rp, _content in WATCHER.poll():
                last_change[rp] = time.time()

            now = time.time()

            # 3) 关闭即提交：编辑器进程由在→不在，强制 flush 所有脏文件
            if _WATCH_CLOSE_PROC:
                up = _editor_present(_WATCH_CLOSE_PROC)
                if up is not None and was_up is True and up is False:
                    for rp in list(last_change.keys()):
                        try:
                            auto_commit(rp, Path(rp).read_text(encoding="utf-8"))
                        except Exception as e:
                            print(f"[watcher] 关闭 flush 失败 {rp}: {e}")
                        last_commit[rp] = now
                        last_change.pop(rp, None)
                if up is not None:
                    was_up = up

            # 2) 常规闸门：稳定窗口 且 满足最小提交周期 才提交
            for rp, ts in list(last_change.items()):
                if now - ts < _WATCH_DEBOUNCE:
                    continue  # 仍在变化中，等稳定
                if now - last_commit.get(rp, 0) < _WATCH_MIN_INTERVAL:
                    continue  # 距上次自动提交太近，等周期
                try:
                    auto_commit(rp, Path(rp).read_text(encoding="utf-8"))
                except Exception as e:  # 单文件失败不应拖垮整个循环
                    print(f"[watcher] auto-commit 失败 {rp}: {e}")
                last_commit[rp] = now
                last_change.pop(rp, None)
        except Exception as e:
            print(f"[watcher] loop 异常: {e}")
        time.sleep(_WATCH_INTERVAL)


# ---------- 响应辅助 ----------
def _send_json(handler, obj, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler, html_text: str, status: int = 200) -> None:
    body = html_text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if not length:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _static(handler, rel_path: str) -> None:
    fp = (WEB / rel_path).resolve()
    if not str(fp).startswith(str(WEB.resolve())) or not fp.exists():
        handler.send_error(404)
        return
    ctype = "text/html" if fp.suffix == ".html" else (
        "application/javascript" if fp.suffix == ".js" else "text/plain")
    handler.send_response(200)
    handler.send_header("Content-Type", ctype + "; charset=utf-8")
    handler.send_header("Content-Length", str(fp.stat().st_size))
    handler.end_headers()
    handler.wfile.write(fp.read_bytes())


# ---------- 业务逻辑 ----------
def import_markdown(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    content = p.read_text(encoding="utf-8")
    title = p.stem
    aid = store.save_article(str(p.resolve()), title)
    ver = version.bump(None, "major")
    vid = store.save_version(aid, content, "初始导入", ver, "major", None)
    return {"article_id": aid, "version": ver, "version_id": vid}


def commit_version(article_id: int, payload: dict) -> dict:
    content = payload.get("content", "")
    message = (payload.get("commit_message") or "").strip()
    if not message:
        raise ValueError("commit_message 不能为空")
    kind = payload.get("version_kind") or "patch"
    if kind not in ("major", "minor", "patch"):
        kind = "patch"
    latest = store.get_latest_version(article_id)
    prev_version = latest["version"] if latest else None
    prev_content = latest["content"] if latest else ""
    stats = build_stats(prev_content, content)
    ver = version.bump(prev_version, kind)
    vid = store.save_version(article_id, content, message, ver, kind, stats)
    warn = version.gentle_warn(prev_content, content)
    return {"version_id": vid, "version": ver, "version_kind": kind,
            "diff_stats": stats, "gentle_warn": warn}


# ---------- HTML 片段（htmx） ----------
def _esc(s: str) -> str:
    return html.escape(s)


def frag_articles() -> str:
    arts = store.get_articles()
    if not arts:
        return '<p class="muted">暂无文章。先在下方导入一个 .md 文件。</p>'
    items = "".join(
        f'<li><a href="#" onclick="selectArticle({a["id"]})">'
        f'{_esc(a["title"])} <span class="tag">{_esc(a["current_version"] or "—")}</span></a></li>'
        for a in arts
    )
    return f'<ul class="art-list">{items}</ul>'


def frag_versions(article_id: int) -> str:
    vs = store.get_versions(article_id)
    if not vs:
        return "<p class=\"muted\">该文章暂无版本。</p>"
    opts_from = "".join(
        f'<option value="{v["id"]}"{" selected" if i == 0 else ""}>{_esc(v["version"])} · {_esc(v["version_kind"])} · {_esc(v["commit_message"])}</option>'
        for i, v in enumerate(vs)
    )
    opts_to = "".join(
        f'<option value="{v["id"]}"{" selected" if i == len(vs) - 1 else ""}>{_esc(v["version"])} · {_esc(v["version_kind"])} · {_esc(v["commit_message"])}</option>'
        for i, v in enumerate(vs)
    )
    rows = "".join(
        f'<li><span class="tag">{_esc(v["version"])}</span> '
        f'[{_esc(v["version_kind"])}] {_esc(v["commit_message"])} '
        f'<span class="muted">{_esc(v["created_at"][:19])}</span></li>'
        for v in vs
    )
    return f"""
    <div class="diff-controls">
      <select id="from" name="from">{opts_from}</select>
      <span>对比</span>
      <select id="to" name="to">{opts_to}</select>
      <select id="diff-mode" name="mode">
        <option value="inline">行内</option>
        <option value="split">并排</option>
        <option value="stats">统计</option>
      </select>
      <button hx-get="/frag/articles/{article_id}/diff"
              hx-include="#from,#to,#diff-mode" hx-target="#diff" hx-swap="innerHTML">查看差异</button>
    </div>
    <p class="legend">
      <span class="sw sw-ins">新增</span>
      <span class="sw sw-del">删除</span>
      <span class="sw sw-mv">移动（仅位置变化）</span>
      <span class="sw sw-eq">未变</span>
    </p>
    <ul class="ver-list">{rows}</ul>
    <div id="diff" class="diff-view"></div>
    """


def frag_diff(article_id: int, from_id: str, to_id: str, mode: str = "inline") -> str:
    if from_id and to_id and int(from_id) == int(to_id):
        return '<p class="muted">请选择两个不同的版本进行对比。</p>'
    a = store.get_version(int(from_id)) if from_id else None
    b = store.get_version(int(to_id)) if to_id else None
    if not a or not b:
        return '<p class="muted">请选择两个版本。</p>'
    ops = diff_sentences(a["content"], b["content"])
    stats = build_stats(a["content"], b["content"])
    stat_line = (
        f'<p class="stats">句+{stats["sentences_added"]} / 句-{stats["sentences_removed"]} '
        f'/ 字+{stats["chars_added"]} / 字-{stats["chars_removed"]}</p>'
    )
    if mode == "split":
        return stat_line + _render_split(ops, a["version"], b["version"])
    if mode == "stats":
        return _render_stats(a, b)
    parts = []
    for o in ops:
        if o.op == "equal":
            parts.append(f'<span class="eq">{_esc(o.text)}</span>')
        elif o.op == "insert":
            parts.append(f'<span class="ins">{_esc(o.text)}</span>')
        elif o.op == "delete":
            parts.append(f'<span class="del">{_esc(o.text)}</span>')
        elif o.op == "moved":
            parts.append(f'<span class="mv" title="该句在另一版本中存在，仅位置移动">{_esc(o.text)}</span>')
        elif o.op == "replace":
            inner = "".join(
                f'<span class="{ "ins" if x.op=="insert" else "del" if x.op=="delete" else "eq"}">{_esc(x.text)}</span>'
                for x in o.inner
            )
            parts.append(f'<span class="rep">{inner}</span>')
    return stat_line + '<div class="diff-body">' + "".join(parts) + "</div>"


def _render_split(ops, ver_a: str, ver_b: str) -> str:
    """并排双栏：左栏为 from 版（删除标红、新增留空），右栏为 to 版（新增标绿、删除留空）。

    对 replace 句内差异：左栏只渲染 from 侧内容（eq + delete），右栏只渲染 to 侧内容
    （eq + insert），避免把对方的字用灰色混进来导致「左右看起来一样」的歧义。
    """
    left, right = [], []
    for o in ops:
        if o.op == "equal":
            t = f'<span class="eq">{_esc(o.text)}</span>'
            left.append(t); right.append(t)
        elif o.op == "delete":
            left.append(f'<span class="del">{_esc(o.text)}</span>')
            right.append('<span class="ph">　</span>')
        elif o.op == "insert":
            left.append('<span class="ph">　</span>')
            right.append(f'<span class="ins">{_esc(o.text)}</span>')
        elif o.op == "moved":
            t = f'<span class="mv" title="该句在另一版本中存在，仅位置移动">{_esc(o.text)}</span>'
            left.append(t); right.append(t)
        elif o.op == "replace":
            linner = "".join(
                f'<span class="del">{_esc(x.text)}</span>' if x.op == "delete"
                else f'<span class="eq">{_esc(x.text)}</span>' if x.op == "equal"
                else '<span class="ph" title="to 版新增">…</span>'
                for x in o.inner
            )
            rinner = "".join(
                f'<span class="ins">{_esc(x.text)}</span>' if x.op == "insert"
                else f'<span class="eq">{_esc(x.text)}</span>' if x.op == "equal"
                else '<span class="ph" title="from 版删除">…</span>'
                for x in o.inner
            )
            left.append(f'<span class="rep">{linner}</span>')
            right.append(f'<span class="rep">{rinner}</span>')
    return (
        '<div class="diff-split">'
        f'<div class="pane pane-l"><div class="pane-h">v{_esc(ver_a)}（左）</div>'
        f'<div class="pane-body">{"".join(left)}</div></div>'
        f'<div class="pane pane-r"><div class="pane-h">v{_esc(ver_b)}（右）</div>'
        f'<div class="pane-body">{"".join(right)}</div></div>'
        '</div>'
    )


def _render_stats(ver_a: dict, ver_b: dict) -> str:
    """统计摘要：两版绝对指标（字数/句数/段数/行数）+ 变化量。

    用途：先概览「差了多少」，再决定是否深入看行内/并排细节。
    颜色沿用应用内 diff 配色：增长=绿（s-up），减少=红（s-down）。
    """
    sa = summarize(ver_a["content"])
    sb = summarize(ver_b["content"])
    metrics = [
        ("字数（去空白）", "chars"),
        ("句数", "sentences"),
        ("段数", "paragraphs"),
        ("行数", "lines"),
    ]
    rows = ""
    for label, key in metrics:
        va, vb = sa[key], sb[key]
        delta = vb - va
        if delta > 0:
            dcls, dsign = "s-up", f"+{delta}"
        elif delta < 0:
            dcls, dsign = "s-down", f"{delta}"
        else:
            dcls, dsign = "s-eq", "±0"
        rows += (
            f"<tr><td>{label}</td><td>{va}</td><td>{vb}</td>"
            f'<td class="s-delta {dcls}">{dsign}</td></tr>'
        )
    return (
        '<table class="stat-table">'
        f'<thead><tr><th>指标</th><th>v{_esc(ver_a["version"])}</th>'
        f'<th>v{_esc(ver_b["version"])}</th><th>变化</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


# ---------- 路由 ----------
class Handler(BaseHTTPRequestHandler):
    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        method = self.command

        # 单页入口
        if method == "GET" and path in ("/", "/index.html"):
            return _static(self, "index.html")
        # 静态资源
        if method == "GET" and path.startswith("/web/"):
            return _static(self, path[len("/web/"):])

        # ---- JSON API ----
        if method == "GET" and path == "/api/articles":
            return _send_json(self, store.get_articles())
        if method == "POST" and path == "/api/articles/import":
            body = _read_json_body(self)
            try:
                return _send_json(self, import_markdown(body.get("path", "")))
            except FileNotFoundError as e:
                return _send_json(self, {"error": str(e)}, 400)
        if method == "GET" and path.startswith("/api/articles/") and path.endswith("/versions"):
            aid = int(path.split("/")[3])
            return _send_json(self, store.get_versions(aid))
        if method == "POST" and path.startswith("/api/articles/") and path.endswith("/versions"):
            aid = int(path.split("/")[3])
            body = _read_json_body(self)
            try:
                return _send_json(self, commit_version(aid, body))
            except ValueError as e:
                return _send_json(self, {"error": str(e)}, 400)
        if method == "GET" and path.startswith("/api/articles/") and path.endswith("/diff"):
            aid = int(path.split("/")[3])
            f = qs.get("from", [""])[0]
            t = qs.get("to", [""])[0]
            a = store.get_version(int(f)) if f else None
            b = store.get_version(int(t)) if t else None
            if not a or not b:
                return _send_json(self, {"error": "版本不存在"}, 400)
            return _send_json(self, {"ops": to_dict(diff_sentences(a["content"], b["content"]))})

        # ---- 文件监听（Phase 3） ----
        if method == "POST" and path == "/api/watch":
            body = _read_json_body(self)
            p = (body.get("path") or "").strip()
            if not p:
                return _send_json(self, {"error": "path 必填"}, 400)
            try:
                added = WATCHER.register(p)
            except Exception as e:
                return _send_json(self, {"error": str(e)}, 400)
            return _send_json(self, {"ok": True, "added": added, "watched": WATCHER.list()})
        if method == "GET" and path == "/api/watch":
            return _send_json(self, {"watched": WATCHER.list()})
        if method == "DELETE" and path == "/api/watch":
            p = (qs.get("path", [""])[0] or "").strip()
            if not p:
                return _send_json(self, {"error": "path 必填"}, 400)
            removed = WATCHER.unregister(p)
            return _send_json(self, {"ok": True, "removed": removed, "watched": WATCHER.list()})

        # ---- htmx 片段 ----
        if method == "GET" and path == "/frag/articles":
            return _send_html(self, frag_articles())
        if method == "GET" and path.startswith("/frag/articles/") and path.endswith("/versions"):
            aid = int(path.split("/")[3])
            return _send_html(self, frag_versions(aid))
        if method == "GET" and path.startswith("/frag/articles/") and path.endswith("/diff"):
            aid = int(path.split("/")[3])
            f = qs.get("from", [""])[0]
            t = qs.get("to", [""])[0]
            mode = qs.get("mode", ["inline"])[0]
            return _send_html(self, frag_diff(aid, f, t, mode))

        self.send_error(404)

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def log_message(self, *args):
        pass  # 静默


def main():
    port = int(os.environ.get("PADIF_PORT", "18887"))
    threading.Thread(target=_watcher_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PAdif 运行中： http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
