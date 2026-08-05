"""novel.json 的读取助手。别在各脚本里各写一遍。"""
from functools import lru_cache
from typing import Any

from . import paths
from .jsonio import read_json


def chapter_text(ch: dict) -> str:
    """分析单元的完整正文（段落用换行拼接）。"""
    return "\n".join(ch["paragraphs"])


def chapter_label(ch: dict) -> str:
    """人读的章节名，合并单元会带上被并进来的标题。"""
    label = f"第{ch['declared_num']}章 {ch['title']}"
    for t in ch["merged_titles"]:
        label += f" ＋ 第{t['declared_num']}章 {t['title']}"
    return label


@lru_cache(maxsize=1)
def load_novel() -> dict[str, Any]:
    return read_json(paths.NOVEL_JSON)


@lru_cache(maxsize=1)
def load_index() -> dict[str, Any]:
    return read_json(paths.NOVEL_INDEX)


def get_chapter(seq: int) -> dict:
    ch = load_novel()["chapters"]
    if not 1 <= seq <= len(ch):
        raise IndexError(f"seq {seq} 越界，有效范围 1~{len(ch)}")
    return ch[seq - 1]


def iter_chapters(start: int = 1, end: int | None = None):
    ch = load_novel()["chapters"]
    end = end or len(ch)
    for c in ch[start - 1:end]:
        yield c
