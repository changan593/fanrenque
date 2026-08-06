#!/usr/bin/env python3
"""
步骤 0：纯原文结构探查。**不调 API，不依赖逐章分析结果。**

用途有两个：
  1. 在逐章分析跑完之前，先拿到一份可用的全书结构画像（主要角色池、
     出场时间线、篇幅与对话密度曲线、候选卷段边界），供分季分集预研。
  2. 逐章分析跑完后，拿它当**独立交叉校验源**——如果模型抽出的主要角色
     和纯文本统计出来的对不上，说明分析有问题。

方法全是统计和正则，没有模型参与，所以结论稳定可复现，但也**只是启发式**：
角色名靠「引号后紧跟的字串 + 后接字多样性」挖出来，会有少量误报
（如「老人」「那人」这类泛称），正式角色资产仍以逐章分析为准。

用法：
    python pipeline/s0_probe_text.py
    python pipeline/s0_probe_text.py --top 60
"""
import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import write_json
from common.novel import chapter_text, load_novel

# 引号闭合后紧跟的字串，中文小说里绝大多数是说话人
AFTER_QUOTE = re.compile(r'[”"]\s*([一-龥]{2,6})')
SPEECH = re.compile(r'[“"]([^”"]{2,})[”"]')


def mine_names(chapters: list[dict], min_hits: int, min_variety: int,
               min_chapters: int) -> list[dict]:
    """
    挖候选角色名。

    关键技巧是「后接字多样性」：真名后面能跟各种各样的动词（唐真看、唐真道、
    唐真笑…），而「唐真看着」这种词组后面接的字很集中。用多样性把名字从
    固定搭配里切出来。
    """
    hits = Counter()
    next_chars = defaultdict(set)
    in_chapters = defaultdict(set)
    for c in chapters:
        for s in AFTER_QUOTE.findall(chapter_text(c)):
            for size in (2, 3, 4):
                if len(s) > size:
                    p = s[:size]
                    hits[p] += 1
                    next_chars[p].add(s[size])
                    in_chapters[p].add(c["seq"])

    cand = [{"name": p, "hits": k, "variety": len(next_chars[p]),
             "chapters": sorted(in_chapters[p])}
            for p, k in hits.items()
            if k >= min_hits and len(next_chars[p]) >= min_variety
            and len(in_chapters[p]) >= min_chapters]

    # 去掉被更长候选覆盖的前缀：同为候选时保留后接更多样的那个
    by_name = {c["name"]: c for c in cand}
    keep = []
    for c in sorted(cand, key=lambda x: -x["hits"]):
        covered = any(q != c["name"] and c["name"].startswith(q)
                      and by_name[q]["variety"] >= c["variety"] for q in by_name)
        if not covered:
            keep.append(c)
    for c in keep:
        c["first_seq"] = c["chapters"][0]
        c["last_seq"] = c["chapters"][-1]
        c["chapter_count"] = len(c["chapters"])
        c["chapters"] = c["chapters"][:200]      # 别把文件撑爆
    return keep


def chapter_metrics(chapters: list[dict]) -> list[dict]:
    """逐章的篇幅、对话密度、段落数——分集时用来估时长和节奏。"""
    out = []
    for c in chapters:
        t = chapter_text(c)
        speech = SPEECH.findall(t)
        spoken = sum(len(s) for s in speech)
        out.append({
            "seq": c["seq"],
            "declared_num": c["declared_num"],
            "title": c["title"],
            "chars": c["char_count"],
            "paragraphs": c["paragraph_count"],
            "speech_lines": len(speech),
            "speech_chars": spoken,
            "dialogue_ratio": round(spoken / c["char_count"], 3) if c["char_count"] else 0,
            "is_merged": c["is_merged"],
        })
    return out


def cast_timeline(names: list[dict], total: int, bucket: int = 25) -> list[dict]:
    """
    按每 bucket 章一段，统计「本段首次出现的主要角色数」。
    新角色扎堆登场的地方，通常就是新卷/新篇章的开头——分季边界的候选点。
    """
    firsts = Counter(n["first_seq"] for n in names)
    out = []
    for start in range(1, total + 1, bucket):
        end = min(start + bucket - 1, total)
        newcomers = [n["name"] for n in names if start <= n["first_seq"] <= end]
        out.append({"range": [start, end], "new_characters": len(newcomers),
                    "names": newcomers})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="纯原文结构探查（不调 API）")
    ap.add_argument("--top", type=int, default=40, help="终端里展示多少个角色")
    ap.add_argument("--min-hits", type=int, default=100)
    ap.add_argument("--min-variety", type=int, default=25)
    ap.add_argument("--min-chapters", type=int, default=20)
    args = ap.parse_args()

    paths.ensure_dirs()
    novel = load_novel()
    chapters = novel["chapters"]
    total = len(chapters)

    names = mine_names(chapters, args.min_hits, args.min_variety, args.min_chapters)
    names.sort(key=lambda x: -x["hits"])
    metrics = chapter_metrics(chapters)
    timeline = cast_timeline(names, total)

    chars = [m["chars"] for m in metrics]
    ratios = [m["dialogue_ratio"] for m in metrics]
    print(f"全书 {total} 个分析单元 | 正文 {sum(chars):,} 字")
    print(f"单章篇幅  中位 {statistics.median(chars):.0f} 字 | "
          f"最短 {min(chars)} | 最长 {max(chars)}")
    print(f"对话占比  中位 {statistics.median(ratios):.1%} | "
          f"最低 {min(ratios):.1%} | 最高 {max(ratios):.1%}")

    print(f"\n候选主要角色 {len(names)} 个（≥{args.min_hits}次提及、"
          f"后接字≥{args.min_variety}种、跨≥{args.min_chapters}章）：")
    for i in range(0, min(args.top, len(names)), 3):
        print("  " + "".join(
            f"{n['name']} {n['hits']}次/{n['chapter_count']}章".ljust(24)
            for n in names[i:i + 3]))
    print("  注：含少量泛称误报（老人/那人/女人等），正式角色资产以逐章分析为准")

    print("\n新角色密集登场的区段（分季边界候选）：")
    hot = sorted(timeline, key=lambda x: -x["new_characters"])[:8]
    for b in sorted(hot, key=lambda x: x["range"][0]):
        print(f"  seq {b['range'][0]:>4}~{b['range'][1]:<4} "
              f"新增 {b['new_characters']} 人：{'、'.join(b['names'][:6])}")

    out = paths.PLOT_DIR / "text_probe.json"
    write_json(out, {
        "source": "纯原文统计，未使用模型",
        "method": "引号后字串 + 后接字多样性挖角色名；正则统计篇幅与对话密度",
        "caveat": "启发式结果，含少量泛称误报，正式资产以逐章分析为准",
        "params": {"min_hits": args.min_hits, "min_variety": args.min_variety,
                   "min_chapters": args.min_chapters},
        "totals": {"units": total, "chars": sum(chars),
                   "median_chars": statistics.median(chars),
                   "median_dialogue_ratio": statistics.median(ratios)},
        "characters": names,
        "chapter_metrics": metrics,
        "cast_timeline": timeline,
    })
    print(f"\n已写出 {out.relative_to(paths.ROOT)}")


if __name__ == "__main__":
    main()
