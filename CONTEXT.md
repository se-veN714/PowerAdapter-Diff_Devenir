# CONTEXT.md — PAdif 项目状态快照

> **本文件定位**：当下状态 + 决策日志 + 导航。是「入场券」，不是「参考书」。
> - 需求 / 设计决策 → 看 [`GUIDE-PAdif.md`](./GUIDE-PAdif.md)
> - 架构 / 编码约束 / API → 看 [`DEVELOPMENT.md`](./DEVELOPMENT.md)
> - 本文件只在「回到项目、想知道现在到哪了 / 为什么这么定 / 下一步干啥」时读。
> - 更新节奏：每完成一个 Phase 或关键决策时同步；不追求详尽，只求「一眼到位」。

---

## 1. 项目一句话

**PAdif** —— 给深度内容创作者的「git 式文章版本监管」轻量工具：导入 Markdown，手动提交带语义版本（`major.minor.patch`）的快照，句子级（非行级）对比两版差异。先网页 MVP，后 Obsidian 插件。

---

## 2. 当前状态（截至 2026-07-07）

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | 需求沉淀：GUIDE + DEVELOPMENT | ✅ 完成 |
| P1 | MVP：diff 引擎 / 存储 / 版本标注 / API / 前端（T1–T6） | ✅ 完成 |
| P2 | 增幅功能 | 🟡 进行中（并排双栏已做，统计摘要 / 降噪待做） |
| P3 | 文件自动检测提交 | ⬜ 未开始 |
| P4 | Obsidian 插件形态 | ⬜ 未开始 |
| P5 | T7 前端优化（收尾） | ⬜ 留待收尾 |

**已上线**：服务运行于 `http://127.0.0.1:18887/`，含「芬恩」示例文章（v1→v2，可看「行走→奔跑」句内字级差异）。

---

## 3. 决策日志（落定的关键选择，含「为什么」）

| 决策 | 结论 | 理由 / 备注 |
|------|------|------|
| 内容来源 | Markdown 文件 | 顺手 Obsidian 世界观库 |
| 提交触发 | 手动提交 + 语义版本 | 创作者自控，不过度自动化 |
| 版本号 | `major.minor.patch`，**只引导不拦截** | 用户明确：相信创作者的小巧思，工具仅辅助标注 |
| diff 粒度 | **句子级**（按 `。！？.!?` 及空行断句），句内字级高亮 | 普通行 diff 会把整段标红，看不出改了哪几个字 |
| 应用形态 | 先网页，后 Obsidian 插件 | 插件复用存储 + 引擎，只换 UI 层 |
| 前端耦合 | **htmx**（用户指定） | 轻量、无重构建链 |
| 数据库 | **SQLite**（用户认可） | 强结构化关系，单机单用户舒适区 |
| MongoDB | ❌ MVP 不采用 | 用户曾疑问，结论：文档型优势在分布式，本地属过度配置，且需 `mongod` 常驻，违「零安装」 |
| 后端框架 | **标准库 `http.server`**（原计划 FastAPI） | 本环境 managed Python 装不上第三方包（OpenSSL/网络限制）；路由已与传输层解耦，将来可平滑迁回 FastAPI |
| 端口 | **18887**（原 8000，用户要求避开常用端口） | 支持 `PADIF_PORT` 环境变量覆盖 |
| 代码版本管理 | `padif/` 内 `git init`（独立版本单元） | 父目录 Hy3 非 git 仓库；`.gitignore` 忽略 `data/*.db` 等运行时数据 |
| 前端优化 | T7，权重最低（3%），留收尾 | 用户要求：避免过早分散精力 |

---

## 4. 已实现的代码模块

```
server/differ.py   句子级 diff 引擎：segment / diff_sentences / build_stats
server/store.py    SQLite 存储层（唯一数据出入口）：Article/Version 表
server/version.py  语义版本：bump / suggest_kind / gentle_warn（仅温和提醒）
server/app.py      后端：/api/* (JSON) + /frag/* (htmx 片段)，端口 18887
web/index.html     单页骨架 + htmx 挂载点 + 双栏样式
web/app.js         htmx 配置、导入/提交 JSON 驱动
web/diffview.js    diff 高亮渲染
web/htmx.min.js    本地化 htmx（免 CDN）
```

---

## 5. 已知问题 / 技术债

- **句子移动噪声**：若把整段打散重排，diff 会产出 `replace` 噪声（句内改动如「行走→奔跑」则完全精确）。→ Phase 2 加「句子移动检测」降噪。
- **FastAPI→stdlib 偏差**：见决策日志；将来环境解禁可迁回，路由逻辑无需重写。
- **单文件导入**：当前仅单 `.md` 导入，无批量。

---

## 6. 下一步候选（待用户拍板）

1. **统计摘要增强**（Phase 2）：字数/段落增减等概览，先看统计再决定深入。
2. **句子移动检测降噪**（Phase 2）：消除重排噪声。
3. **文件自动检测提交**（Phase 3）：监听目录，保存即生成版本。
4. **Obsidian 插件**（Phase 4）：复用存储与引擎，换 UI 层。
5. **T7 前端优化**（Phase 5 收尾）：样式打磨、响应式、无障碍、性能。

---

## 7. 快速上手

```bash
cd D:/Work/Project/Hy3/padif
C:/Users/12442/.workbuddy/binaries/python/versions/3.12.13/python.exe server/app.py
# 默认 18887；换端口：PADIF_PORT=xxxx 同命令
```
浏览器打开 `http://127.0.0.1:18887/` → 导入框填 `C:/.../your.md` 绝对路径 → 提交选 major/minor/patch → 选两版「查看差异」，可切「行内 / 并排」。

---

*本文件随 Phase 推进更新；任何架构/接口变更以 DEVELOPMENT.md 为准，本文件只记「状态与为什么」。*
