/* PAdif 前端交互（JS 驱动 import / commit，htmx 驱动列表与 diff 片段） */

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
  document.getElementById("versions").innerHTML = await res.text();
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
  // 刷新版本列表与 diff
  const r2 = await fetch(`/frag/articles/${currentAid}/versions`);
  document.getElementById("versions").innerHTML = await r2.text();
  document.getElementById("new-content").value = "";
  document.getElementById("commit-msg").value = "";
}

// 文章列表点击委托
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[hx-get^="/frag/articles/"]');
  if (a) {
    e.preventDefault();
    const m = a.getAttribute("hx-get").match(/\/frag\/articles\/(\d+)\/versions/);
    if (m) selectArticle(parseInt(m[1], 10));
  }
});
