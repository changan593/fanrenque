#!/usr/bin/env python3
"""
步骤 9：把 1200 份逐章分析压成剧情线分析的原材料。**不调 API。**

阶段三的 3.5（全书剧情线）之前必须先做这一步。1200 章分析文件加起来几十兆，
没法直接读；但真正用于划分卷段和分季的信息其实很少：
**每章讲了什么、谁在场、在哪、埋了什么伏笔**。

产出：

    data/plot/digest_all.md          全书摘要，一章一行，供人通读
    data/plot/digest_p01..p06.md     切成 6 块，每块 200 章，供分头分析
    data/plot/timeline.json          机器读：每章的人物/场景/伏笔/情绪

每行的信息密度是刻意压到最低的——分卷段靠的是**主要人物的进出**和
**场景的迁移**，不是细节。细节要用时回原文取，卷宗和章节 json 都在。

用法：
    python pipeline/s9_plot_digest.py
    python pipeline/s9_plot_digest.py --chunks 6
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import read_json, write_json


def load_alias_map() -> dict:
    """复用 s4 归并好的别名表，让摘要里的人名前后一致。"""
    p = paths.CHARACTERS_DIR / "index.json"
    if not p.exists():
        return {}
    idx = read_json(p)
    m = {}
    for c in idx.get("characters") or []:
        canon = c["canonical_name"]
        m[canon] = canon
        for a in c.get("aliases") or []:
            m[a] = canon
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description="把逐章分析压成剧情线原材料")
    ap.add_argument("--chunks", type=int, default=6, help="切成几块（默认 6，对应六季）")
    ap.add_argument("--top-chars", type=int, default=5, help="每章列几个主要人物")
    args = ap.parse_args()

    amap = load_alias_map()
    # 只有出场够多的角色才配进摘要——龙套进来会把主线淹掉
    major = {c["canonical_name"] for c in
             (read_json(paths.CHARACTERS_DIR / "index.json").get("characters") or [])
             if c["chapter_count"] >= 8}

    rows, timeline = [], []
    for p in sorted(paths.CHAPTERS_DIR.glob("ch*.json")):
        d = read_json(p)
        a = d.get("analysis") or {}
        seq = d.get("seq")

        names = []
        for c in a.get("characters") or []:
            if not isinstance(c, dict) or c.get("mentioned_only"):
                continue
            n = amap.get((c.get("name") or "").strip(), (c.get("name") or "").strip())
            if n and n in major and n not in names:
                names.append(n)
        scenes = [s.get("name") for s in a.get("scenes") or []
                  if isinstance(s, dict) and s.get("name")]
        cont = a.get("continuity") or {}
        fore = [x for x in (cont.get("foreshadows") or []) if isinstance(x, str)]
        reveal = [x for x in (cont.get("reveals") or []) if isinstance(x, str)]

        rows.append(
            f"[{seq}] {d.get('title', '')}\n"
            f"    {a.get('synopsis', '').strip()}\n"
            f"    人物：{'、'.join(names[:args.top_chars]) or '—'}"
            f"　｜场景：{'、'.join(dict.fromkeys(scenes))[:60] or '—'}"
            f"　｜基调：{a.get('emotional_tone', '') or '—'}"
            + (f"\n    埋：{'；'.join(fore)[:120]}" if fore else "")
            + (f"\n    收：{'；'.join(reveal)[:120]}" if reveal else ""))

        timeline.append({"seq": seq, "title": d.get("title", ""),
                         "synopsis": a.get("synopsis", ""),
                         "characters": names, "scenes": list(dict.fromkeys(scenes)),
                         "tone": a.get("emotional_tone", ""),
                         "foreshadows": fore, "reveals": reveal,
                         "beat_count": len(a.get("beats") or []),
                         "dialogue_count": len(a.get("dialogues") or []),
                         "char_count": d.get("char_count", 0)})

    paths.PLOT_DIR.mkdir(parents=True, exist_ok=True)
    head = ("# 全书章节摘要（剧情线分析原材料）\n\n"
            "格式：`[seq] 标题` / 简介 / 主要人物·场景·基调 / 伏笔埋收\n"
            "人名已按 s4 的别名表归一。只列出场 ≥8 章的角色，龙套不入。\n\n")
    (paths.PLOT_DIR / "digest_all.md").write_text(head + "\n".join(rows) + "\n",
                                                  encoding="utf-8")

    size = -(-len(rows) // args.chunks)
    for i in range(args.chunks):
        part = rows[i * size:(i + 1) * size]
        if not part:
            continue
        lo = i * size + 1
        hi = min((i + 1) * size, len(rows))
        (paths.PLOT_DIR / f"digest_p{i + 1:02d}.md").write_text(
            f"# 章节摘要 第 {i + 1}/{args.chunks} 块　seq {lo}~{hi}\n\n"
            + head.split("\n\n", 1)[1] + "\n".join(part) + "\n", encoding="utf-8")

    write_json(paths.PLOT_DIR / "timeline.json", timeline)

    tot = sum(len(r) for r in rows)
    print(f"摘要 {len(rows)} 章，共 {tot:,} 字，切成 {args.chunks} 块")
    print(f"  data/plot/digest_all.md         （{tot // 1000}K 字）")
    print(f"  data/plot/digest_p01..p{args.chunks:02d}.md  （每块约 {tot // args.chunks // 1000}K 字）")
    print(f"  data/plot/timeline.json")
    print(f"\n入选主要人物 {len(major)} 个（出场≥8章）")
    tone = Counter(t["tone"] for t in timeline if t["tone"])
    print("基调分布：", dict(tone.most_common(8)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
