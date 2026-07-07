/* PAdif 前端交互：article / version / diff 均以 fetch 消费后端 /frag/* HTML 片段。
   关键：用 innerHTML 注入片段后，浏览器不会自动让 htmx 重新扫描，
   必须显式调用 htmx.process(node) 才能让片段内的 hx-* 生效（如「查看差异」按钮）。 */

let currentAid = null;

async function loadArticles() {
  const res = await fetch("/frag/articles");
  document.getElementById("articles").innerHTML = await res.text();
}

async function importMd() {
  const path = document.getElementById("md-path").value.trim();
  if (!path) { alert("请填写 .md 文件路径"); return; }
  const res = await fetch("/api/articles/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  await loadArticles();
}

async function selectArticle(aid) {
  currentAid = aid;
  const res = await fetch(`/frag/articles/${aid}/versions`);
  const box = document.getElementById("versions");
  box.innerHTML = await res.text();
  // 让新注入片段里的 hx-*（查看差异按钮）被 htmx 绑定
  if (window.htmx && htmx.process) htmx.process(box);
  document.getElementById("commit-form").style.display = "block";
}

async function commitVersion() {
  if (!currentAid) return;
  const content = document.getElementById("new-content").value;
  const message = document.getElementById("commit-msg").value.trim();
  const kind = document.getElementById("commit-kind").value;
  if (!message) { alert("commit message 不能为空"); return; }
  const res = await fetch(`/api/articles/${currentAid}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, commit_message: message, version_kind: kind }),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  if (data.gentle_warn) {
    if (!confirm("提示：这一版与上一版似乎只有标点/空格差异，仍要提交吗？")) return;
  }
  // 刷新版本列表与 diff 控件，并重新绑定 htmx
  const r2 = await fetch(`/frag/articles/${currentAid}/versions`);
  const box = document.getElementById("versions");
  box.innerHTML = await r2.text();
  if (window.htmx && htmx.process) htmx.process(box);
  document.getElementById("new-content").value = "";
  document.getElementById("commit-msg").value = "";
}

// ---------- 文件监听（Phase 3） ----------
async function watchMd() {
  const path = document.getElementById("watch-path").value.trim();
  if (!path) { alert("请填写要监听的文件路径"); return; }
  const res = await fetch("/api/watch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  await loadWatch();
}

async function loadWatch() {
  const res = await fetch("/api/watch");
  const data = await res.json();
  const list = data.watched || [];
  const el = document.getElementById("watch-list");
  if (!list.length) { el.innerHTML = '<p class="muted">暂无监听。</p>'; return; }
  el.innerHTML = '<ul class="ver-list">' + list.map(p =>
    `<li><span class="wpath">${escapeHtml(p)}</span> ` +
    `<button class="wstop" data-path="${escapeAttr(p)}">停止</button></li>`
  ).join("") + "</ul>";
  el.querySelectorAll("button.wstop").forEach(b => {
    b.addEventListener("click", () => removeWatch(b.dataset.path));
  });
}

async function removeWatch(path) {
  const res = await fetch("/api/watch?path=" + encodeURIComponent(path), { method: "DELETE" });
  await res.json();
  await loadWatch();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s).replace(/"/g, "&quot;"); }

// 页面加载即拉取文章列表与监听列表
document.addEventListener("DOMContentLoaded", () => { loadArticles(); loadWatch(); });
