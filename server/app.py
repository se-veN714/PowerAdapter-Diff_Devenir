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
from differ import diff_sentences, build_stats, to_dict

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"

store.init_db()


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
        f'<li><a href="#" hx-get="/frag/articles/{a["id"]}/versions" '
        f'hx-target="#versions" hx-swap="innerHTML">'
        f'{_esc(a["title"])} <span class="tag">{_esc(a["current_version"] or "—")}</span></a></li>'
        for a in arts
    )
    return f'<ul class="art-list">{items}</ul>'


def frag_versions(article_id: int) -> str:
    vs = store.get_versions(article_id)
    if not vs:
        return "<p class=\"muted\">该文章暂无版本。</p>"
    opts = "".join(
        f'<option value="{v["id"]}">{_esc(v["version"])} · {_esc(v["version_kind"])} · {_esc(v["commit_message"])}</option>'
        for v in vs
    )
    rows = "".join(
        f'<li><span class="tag">{_esc(v["version"])}</span> '
        f'[{_esc(v["version_kind"])}] {_esc(v["commit_message"])} '
        f'<span class="muted">{_esc(v["created_at"][:19])}</span></li>'
        for v in vs
    )
    return f"""
    <div class="diff-controls">
      <select id="from">{opts}</select>
      <span>对比</span>
      <select id="to">{opts}</select>
      <button hx-get="/frag/articles/{article_id}/diff"
              hx-include="#from,#to" hx-target="#diff" hx-swap="innerHTML">查看差异</button>
    </div>
    <ul class="ver-list">{rows}</ul>
    <div id="diff" class="diff-view"></div>
    """


def frag_diff(article_id: int, from_id: str, to_id: str) -> str:
    a = store.get_version(int(from_id)) if from_id else None
    b = store.get_version(int(to_id)) if to_id else None
    if not a or not b:
        return '<p class="muted">请选择两个版本。</p>'
    ops = diff_sentences(a["content"], b["content"])
    parts = []
    for o in ops:
        if o.op == "equal":
            parts.append(f'<span class="eq">{_esc(o.text)}</span>')
        elif o.op == "insert":
            parts.append(f'<span class="ins">{_esc(o.text)}</span>')
        elif o.op == "delete":
            parts.append(f'<span class="del">{_esc(o.text)}</span>')
        elif o.op == "replace":
            inner = "".join(
                f'<span class="{ "ins" if x.op=="insert" else "del" if x.op=="delete" else "eq"}">{_esc(x.text)}</span>'
                for x in o.inner
            )
            parts.append(f'<span class="rep">{inner}</span>')
    stats = build_stats(a["content"], b["content"])
    stat_line = (
        f'<p class="stats">句+{stats["sentences_added"]} / 句-{stats["sentences_removed"]} '
        f'/ 字+{stats["chars_added"]} / 字-{stats["chars_removed"]}</p>'
    )
    return stat_line + '<div class="diff-body">' + "".join(parts) + "</div>"


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
            return _send_html(self, frag_diff(aid, f, t))

        self.send_error(404)

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def log_message(self, *args):
        pass  # 静默


def main():
    port = int(os.environ.get("PADIF_PORT", "18887"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PAdif 运行中： http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
