"""PAdif 句子级 diff 引擎。

核心思想：散文不分行的特性，使「按行 diff」几乎无用。
本模块以「句/标点」为最小单位切分文本，再基于 difflib 做序列对比，
从而精确呈现「这一版到底改了哪几句、句内改了哪几个字」。

分段边界：
  - 句末：。 ！ ？ . ! ? … 以及段落换行（空行）
  - 句中（次级对齐，暂不单独切分，仅作未来扩展点）：， ； 、 , ; :
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List

# 句末标点（中文 + 英文 + 省略号）。注意英文句号 "." 需配合「后接空格或结尾」才断句，
# 以免误切小数、缩写等。
SENT_ENDERS = set("。！？!?…")

# 段落分隔：一个或多个空行
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def segment(text: str) -> List[str]:
    """将正文切分为句子列表（保留句末标点，去除首尾空白）。

    段落边界天然成为句子边界；空段落被忽略。
    """
    if not text:
        return []
    text = text.replace("\r\n", "\n")
    sentences: List[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = para.strip()
        if not para:
            continue
        buf: List[str] = []
        for i, ch in enumerate(para):
            buf.append(ch)
            if ch in SENT_ENDERS:
                _flush(buf, sentences)
                buf = []
            elif ch == "." and (i + 1 == len(para) or para[i + 1] == " "):
                # 英文句末：句号后接空格或段尾
                _flush(buf, sentences)
                buf = []
        # 段落内剩余（无句末标点的尾句，如英文无标点结尾）
        if buf:
            _flush(buf, sentences)
    return sentences


def _flush(buf: List[str], out: List[str]) -> None:
    seg = "".join(buf).strip()
    if seg:
        out.append(seg)


@dataclass
class DiffOp:
    """一个 diff 操作。

    op: equal | insert | delete | replace
    - equal/insert/delete 的 text 为整句（或句内片段）
    - replace 的 inner 为句内字符级 diff（list[DiffOp]），text 为原句整体
    """

    op: str
    text: str
    inner: List["DiffOp"] = field(default_factory=list)


def _char_diff(a: str, b: str) -> List[DiffOp]:
    """句内字符级 diff，用于 replace 时的词/字级高亮。"""
    sm = SequenceMatcher(None, list(a), list(b), autojunk=False)
    out: List[DiffOp] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(DiffOp("equal", a[i1:i2]))
        elif tag == "delete":
            out.append(DiffOp("delete", a[i1:i2]))
        elif tag == "insert":
            out.append(DiffOp("insert", b[j1:j2]))
        elif tag == "replace":
            out.append(DiffOp("delete", a[i1:i2]))
            out.append(DiffOp("insert", b[j1:j2]))
    return out


def diff_sentences(a: str, b: str) -> List[DiffOp]:
    """对比两个版本的正文，返回句子级操作序列。

    替换（replace）类操作会额外携带 inner（句内字符级 diff）以便前端高亮。
    """
    sa, sb = segment(a), segment(b)
    sm = SequenceMatcher(None, sa, sb, autojunk=False)
    ops: List[DiffOp] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for s in sa[i1:i2]:
                ops.append(DiffOp("equal", s))
        elif tag == "delete":
            for s in sa[i1:i2]:
                ops.append(DiffOp("delete", s))
        elif tag == "insert":
            for s in sb[j1:j2]:
                ops.append(DiffOp("insert", s))
        elif tag == "replace":
            # 整句替换：句级标红/标绿，同时给出句内字符级对齐
            for s_old in sa[i1:i2]:
                for s_new in sb[j1:j2]:
                    ops.append(DiffOp("replace", s_old, _char_diff(s_old, s_new)))
    return ops


def build_stats(a: str, b: str) -> dict:
    """统计两版间的增删规模，供版本记录与统计摘要使用。"""
    sa, sb = segment(a), segment(b)
    chars_added = sum(len(s) for s in sb)
    chars_removed = sum(len(s) for s in sa)
    sm = SequenceMatcher(None, sa, sb, autojunk=False)
    sentences_added = sentences_removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            sentences_added += j2 - j1
        elif tag == "delete":
            sentences_removed += i2 - i1
        elif tag == "replace":
            sentences_removed += i2 - i1
            sentences_added += j2 - j1
    return {
        "chars_added": chars_added,
        "chars_removed": chars_removed,
        "sentences_added": sentences_added,
        "sentences_removed": sentences_removed,
    }


def summarize(text: str) -> dict:
    """绝对指标，用于统计摘要视图（字数 / 句数 / 段数 / 行数）。

    与 build_stats（增量）互补：前者描述两版「差多少」，本函数描述每版「有多少」。
    """
    if not text:
        return {"chars": 0, "sentences": 0, "paragraphs": 0, "lines": 0}
    text = text.replace("\r\n", "\n")
    paras = [p for p in _PARAGRAPH_RE.split(text) if p.strip()]
    # 字数：去除所有空白字符后的字符数，更接近中文「字数」直觉
    chars = len(re.sub(r"\s", "", text))
    return {
        "chars": chars,
        "sentences": len(segment(text)),
        "paragraphs": len(paras),
        "lines": text.count("\n") + 1,
    }


def to_dict(ops: List[DiffOp]) -> list:
    """将 DiffOp 序列序列化为 JSON 友好的 dict 列表。"""

    def conv(op: DiffOp) -> dict:
        d = {"op": op.op, "text": op.text}
        if op.inner:
            d["inner"] = [conv(x) for x in op.inner]
        return d

    return [conv(o) for o in ops]
