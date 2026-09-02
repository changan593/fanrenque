"""
原文引用的记法与核对：`seqN[i]` 段号引用、`【原】「…」` 引文。

全项目的取证文档（角色卡、场景卡、幕文档、剧本）都用同一套记法，
以前只有 s17 一处解析它，而且解析得不全：

  seq2[1][18]        一行挂两段——正则只吃到 [1]，于是落在第 18 段的引文被报成「找不到」，
                     落在相邻段的还会被报成「差一位」。实测 s17 报的 56 处里 45 处是这个假阳性。
  seq3[18-19]        区间
  「前半句……后半句」  省略号代表「这里跳过了原文」，整串拿去比对必然对不上；
                     以前只比省略号之前那一段，且要求 ≥6 字，「满脸严肃……小大人的表情」就漏判了。

现在这些都收在这里，s17 只管遍历文件和打印报告。

段号一律 **1 基**——与 s2 喂给模型的 `[n]` 编号、chapter json 的 `para`、
s8 卷宗的输出全部一致（见 doc/02 第二节）。
"""
from __future__ import annotations

import re
from typing import Iterable

# seqN 后面跟一个或多个 [i] / [i-j]；把整串抓下来再拆
CITE_RE = re.compile(r"seq(\d+)((?:\[\d+(?:[-–]\d+)?\])+)")
_BRACKET_RE = re.compile(r"\[(\d+)(?:[-–](\d+))?\]")
QUOTE_RE = re.compile(r"「([^」]+)」")
ELLIPSIS_RE = re.compile(r"…+|\.{3,}|。{3,}")

# 归一化：去空白与标点，全角半角等价。与 doc/02 第二节同一套判据（只统一形态，不删内容字）。
_PUNCT_TABLE = str.maketrans("", "", "　 \t\n．，、。！？；：（）()《》〈〉「」『』…—-·\"'“”‘’.,!?;:")


def norm(s: str) -> str:
    return (s or "").translate(_PUNCT_TABLE)


def parse_cites(line: str) -> list[tuple[int, int, int]]:
    """一行里的全部段号引用，展开成 (seq, 起段, 止段)，含两端。

    >>> parse_cites("seq2[1][18] 与 seq3[18-19]")
    [(2, 1, 1), (2, 18, 18), (3, 18, 19)]
    """
    out = []
    for seq_s, brackets in CITE_RE.findall(line):
        seq = int(seq_s)
        for i_s, j_s in _BRACKET_RE.findall(brackets):
            i = int(i_s)
            out.append((seq, i, int(j_s) if j_s else i))
    return out


def quotes_after_marker(line: str, marker: str = "【原】") -> list[str]:
    """一行里**被标成原文**的引文：只认 `marker` 之后的 `「…」`。

    全项目的取证表都用 `【原】`（逐字引用）与 `【补】`（推导补全）区分，
    所以它是唯一可靠的判据。不这么收窄的话，负面清单词（「多余手指」）、
    文档自己的说明（「整条正文就是另一条旁白」）都会被当成引文去核对。

    引文里的 `**`（加粗）与 `★` 是文档标注，不算内容，先剥掉。
    """
    if marker not in line:
        return []
    tail = line.split(marker, 1)[1]
    out = []
    for q in QUOTE_RE.findall(tail):
        q = q.replace("**", "").replace("★", "").strip()
        if len(norm(q)) >= 6:
            out.append(q)
    return out


def quote_parts(quote: str) -> list[str]:
    """按省略号拆成若干片段，每片都得在原文里、且按顺序出现。
    没有省略号就是一片。太短的片段（归一化后 <2 字）不参与比对。"""
    parts = [norm(p) for p in ELLIPSIS_RE.split(quote)]
    return [p for p in parts if len(p) >= 2] or [norm(quote)]


def quote_in(quote: str, paragraph: str) -> bool:
    """引文是否逐字落在这一段里（容忍省略号跳过的部分，片段须按顺序出现）。"""
    hay = norm(paragraph)
    pos = 0
    for part in quote_parts(quote):
        k = hay.find(part, pos)
        if k < 0:
            return False
        pos = k + len(part)
    return True


def locate(paragraphs: list[str], quote: str, idx1: int, window: int = 0) -> int | None:
    """在第 idx1 段（1 基）附近找这句引文，返回命中的 1 基段号；找不到返回 None。
    window=0 只查该段；window=k 时按 idx1, idx1±1, idx1±2 … 的顺序查。"""
    order = [idx1] + [idx1 + d for k in range(1, window + 1) for d in (-k, k)]
    for i in order:
        if 1 <= i <= len(paragraphs) and quote_in(quote, paragraphs[i - 1]):
            return i
    return None


def find_anywhere(quote: str, chapters: Iterable[tuple[int, list[str]]]) -> list[tuple[int, int]]:
    """全书范围内这句引文落在哪些 (seq, 段号)。用于「全书唯一落点」的重定位。"""
    hits = []
    for seq, paras in chapters:
        for i, p in enumerate(paras, 1):
            if quote_in(quote, p):
                hits.append((seq, i))
    return hits
