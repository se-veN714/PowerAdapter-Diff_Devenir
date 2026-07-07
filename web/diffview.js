/* PAdif 前端差异视图增强（服务端已渲染高亮，这里做轻量客户端增强） */

// diff 片段加载完成后，给句内替换块加一个细边框提示，便于区分「整句替换」
document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target && e.detail.target.id === "diff") {
    const reps = e.detail.target.querySelectorAll(".rep");
    reps.forEach((el) => {
      el.title = "此句被整体改写（内部已做字级高亮）";
    });
  }
});

// 复制差异文本（去除高亮标签，保留纯文本）
function copyDiffPlain() {
  const el = document.getElementById("diff");
  if (!el) return;
  const text = el.innerText;
  if (navigator.clipboard) navigator.clipboard.writeText(text);
}
