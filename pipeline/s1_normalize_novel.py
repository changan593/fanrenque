#!/usr/bin/env python3
"""
步骤 1：原文 txt -> 标准化 novel.json

原文是 ixdzs8 抓取版（GB18030 编码），有三类结构问题，本脚本全部显式处理并留痕：

  A. 作者编号本身有重号/跳号（第548章出现两次、455/647-649 跳过等），
     所以「作者声明的章号」不能当主键。本脚本用「文件顺序 seq」当唯一主键，
     作者章号保留在 declared_num 里。

  B. 站点把连续 2-3 章合并在同一页，导致多个标题行连排、正文只跟在最后一个标题后面。
     这类连排标题合并成一个「分析单元」，额外标题进 merged_titles。

  C. 正文里混进了抓取垃圾，两类：重复的章节标题行（缩进的 "　　第787章..."），
     以及站点插入的翻页提示（"本小章还未完，请点击下一页..."，全书 88 处）。
     剔除后记录在 stripped_lines，不静默丢弃。

用法：
    python pipeline/s1_normalize_novel.py
    python pipeline/s1_normalize_novel.py --check   # 只体检不写文件
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import write_json

ENCODING = "gb18030"
BODY_INDENT = "　　"          # 正文段落的全角双空格缩进
TITLE_RE = re.compile(r"^第\s*(\d+)\s*章(.*)$")
BODY_START_MARK = "------章节内容开始-------"

# 站点插进正文的翻页提示，不是作者写的。不剔除会被当成旁白抽进章节分析里。
JUNK_RE = re.compile(r"请点击下一页|本小章还未完|小主，这个章节后面还有|爱下电子书|ixdzs")


def load_lines() -> list[str]:
    raw = paths.RAW_TXT.read_bytes()
    return raw.decode(ENCODING).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def parse_header(lines: list[str]) -> tuple[dict, int]:
    """解析文件头，返回 (meta, 正文起始行号)。"""
    meta = {"title": None, "author": None, "declared_status": None,
            "blurb": None, "source_note": None}
    start = 0
    blurb_parts, in_blurb = [], False
    for i, line in enumerate(lines[:40]):
        s = line.strip()
        if s == BODY_START_MARK:
            start = i + 1
            break
        if s.startswith("『") and "/作者:" in s:
            body = s.strip("『』")
            meta["title"], meta["author"] = body.split("/作者:", 1)
        elif s.startswith("『状态:"):
            meta["declared_status"] = s.strip("『』").replace("状态:", "")
        elif s.startswith("『内容简介:"):
            in_blurb = True
            blurb_parts.append(s.replace("『内容简介:", "").strip())
        elif in_blurb:
            if "ixdzs" in s:
                meta["source_note"] = s
                in_blurb = False
            else:
                blurb_parts.append(s)
    meta["blurb"] = "".join(p for p in blurb_parts if p).strip("』")
    return meta, start


def find_body_end(lines: list[str]) -> int:
    """返回正文结束行号（不含）。文件尾部是站点的推广行。"""
    end = len(lines)
    for i in range(len(lines) - 1, max(len(lines) - 20, 0), -1):
        s = lines[i].strip()
        if s.startswith("『还在连载中") or "ixdzs" in s or not s:
            end = i
        else:
            break
    return end


def is_title_line(line: str) -> re.Match | None:
    """标题行判定：必须顶格（不缩进）。缩进的同形行是正文里的抓取垃圾。"""
    if line.startswith("　") or line.startswith(" "):
        return None
    return TITLE_RE.match(line.strip())


def normalize() -> dict:
    lines = load_lines()
    meta, body_start = parse_header(lines)
    body_end = find_body_end(lines)

    # 1) 收集所有顶格标题行
    titles: list[dict] = []
    for i in range(body_start, body_end):
        m = is_title_line(lines[i])
        if m:
            titles.append({"line": i, "num": int(m.group(1)),
                           "title": m.group(2).strip(), "raw": lines[i].strip()})
    if not titles:
        raise SystemExit("没有识别到任何章节标题，请检查编码或标题格式")

    # 2) 连排标题合并成分析单元
    units: list[list[dict]] = []
    k = 0
    while k < len(titles):
        j = k
        while j + 1 < len(titles) and titles[j + 1]["line"] == titles[j]["line"] + 1:
            j += 1
        units.append(titles[k:j + 1])
        k = j + 1

    # 3) 逐单元取正文
    chapters = []
    for idx, unit in enumerate(units):
        head, tail = unit[0], unit[-1]
        seg_end = units[idx + 1][0]["line"] if idx + 1 < len(units) else body_end

        paragraphs, stripped = [], []
        for i in range(tail["line"] + 1, seg_end):
            s = lines[i].strip()
            if not s:
                continue
            if TITLE_RE.match(s):          # 正文里混入的重复标题 -> 剔除留痕
                stripped.append({"line": i, "kind": "重复标题", "text": s})
                continue
            if JUNK_RE.search(s):          # 站点翻页提示 -> 剔除留痕
                stripped.append({"line": i, "kind": "站点插入", "text": s})
                continue
            paragraphs.append(s)

        seq = idx + 1
        chapters.append({
            "seq": seq,
            "chapter_id": f"ch{seq:04d}",
            "declared_num": head["num"],
            "title": head["title"],
            "raw_title_line": head["raw"],
            # 被站点并进来的额外标题（正常章为空数组）
            "merged_titles": [{"declared_num": t["num"], "title": t["title"]} for t in unit[1:]],
            "is_merged": len(unit) > 1,
            "line_start": head["line"],
            "line_end": seg_end - 1,
            "paragraph_count": len(paragraphs),
            "char_count": sum(len(p) for p in paragraphs),
            "stripped_lines": stripped,
            # 正文只存 paragraphs 这一份（避免与 text 重复存储把文件撑大一倍）。
            # 需要整段文本时用 common.novel.chapter_text(c)。
            "paragraphs": paragraphs,
        })

    meta.update({
        "source_file": paths.RAW_TXT.name,
        "source_encoding": ENCODING,
        "title_line_count": len(titles),      # 书里真实存在的章节标题数
        "unit_count": len(chapters),          # 分析单元数（合并后）
        "merged_unit_count": sum(1 for c in chapters if c["is_merged"]),
        "declared_num_min": min(t["num"] for t in titles),
        "declared_num_max": max(t["num"] for t in titles),
        "total_chars": sum(c["char_count"] for c in chapters),
    })
    return {"meta": meta, "chapters": chapters}


def build_index(novel: dict) -> dict:
    """不含正文的轻量目录，供规划、检索、分集使用。"""
    return {
        "meta": novel["meta"],
        "chapters": [{k: c[k] for k in
                      ("seq", "chapter_id", "declared_num", "title",
                       "merged_titles", "is_merged", "char_count", "paragraph_count")}
                     for c in novel["chapters"]],
    }


def health_report(novel: dict) -> list[str]:
    """体检：把所有已知风险点打印出来，别让问题静默通过。"""
    ch = novel["chapters"]
    m = novel["meta"]
    out = [
        f"标题行总数      : {m['title_line_count']}",
        f"分析单元总数    : {m['unit_count']}（合并单元 {m['merged_unit_count']} 个）",
        f"作者章号范围    : 第{m['declared_num_min']}章 ~ 第{m['declared_num_max']}章",
        f"正文总字数      : {m['total_chars']:,}",
    ]
    nums = [c["declared_num"] for c in ch] + \
           [t["declared_num"] for c in ch for t in c["merged_titles"]]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    miss = [n for n in range(min(nums), max(nums) + 1) if n not in set(nums)]
    out.append(f"作者重号        : {len(dup)} 个 -> {dup}")
    out.append(f"作者跳号        : {len(miss)} 个 -> {miss}")

    short = [c for c in ch if c["char_count"] < 800]
    out.append(f"正文<800字的单元: {len(short)} 个" +
               (f" -> {[c['chapter_id'] for c in short]}" if short else ""))
    kinds = Counter(x["kind"] for c in ch for x in c["stripped_lines"])
    out.append("剔除的抓取垃圾  : " + (", ".join(f"{k} {v} 行" for k, v in kinds.items())
                                       or "无"))

    # 合并单元 = 内容可能缺失，必须显式列出
    merged = [c for c in ch if c["is_merged"]]
    if merged:
        out.append("")
        out.append(f"⚠ 合并单元 {len(merged)} 个（站点把多章塞进一页，正文量往往只有一章）：")
        for c in merged:
            n = 1 + len(c["merged_titles"])
            allt = [f"第{c['declared_num']}章{c['title']}"] + \
                   [f"第{t['declared_num']}章{t['title']}" for t in c["merged_titles"]]
            out.append(f"  {c['chapter_id']} {n}标题/{c['char_count']}字 : " + " | ".join(allt))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="原文 txt → 标准化 novel.json（只在原文更新时才需要重跑）")
    ap.add_argument("--check", action="store_true", help="只体检，不写文件")
    args = ap.parse_args()

    novel = normalize()
    print("\n".join(health_report(novel)))
    if args.check:
        return 0
    write_json(paths.NOVEL_JSON, novel)
    write_json(paths.NOVEL_INDEX, build_index(novel))
    print(f"\n已写出 {paths.NOVEL_JSON.relative_to(paths.ROOT)} "
          f"({paths.NOVEL_JSON.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"已写出 {paths.NOVEL_INDEX.relative_to(paths.ROOT)} "
          f"({paths.NOVEL_INDEX.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
