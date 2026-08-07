#!/usr/bin/env python3
"""
步骤 11：为每一集算出它需要哪些角色资产与场景资产。**不调 API。**

分集表定了「哪几章是一集」之后，这一步回答制作上最实际的问题：

  - 这一集要画几个人、几个景？
  - 哪些是**首次出现**（必须先做资产），哪些是复用？
  - 哪一集的新资产负担最重（排产时要提前动手）？

这张表是阶段四（资产深化）的工作队列，也是阶段六排产的依据。

## 为什么要单独算「首现」

角色和场景的提示词只需要做一次，之后全片复用——这正是一致性的来源。
所以真正的工作量不是「这一集有多少人」，而是「这一集有多少人是**新的**」。
按首现排队，才能在开拍前把该做的资产做完，而不是拍到一半临时补。

用法：
    python pipeline/s11_episode_assets.py
    python pipeline/s11_episode_assets.py --min-chapters 3   # 只统计出场≥3章的角色
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import read_json, write_json


def main() -> int:
    ap = argparse.ArgumentParser(description="每集的角色/场景资产需求")
    ap.add_argument("--episodes", type=Path, default=paths.PLOT_DIR / "episodes.json")
    ap.add_argument("--min-chapters", type=int, default=2,
                    help="只统计出场≥N章的角色。1 会把一次性龙套全算进来")
    ap.add_argument("--out", type=Path, default=paths.PLOT_DIR / "episode_assets.json")
    args = ap.parse_args()

    if not args.episodes.exists():
        raise SystemExit(f"先跑 s10_episode_plan.py 生成 {args.episodes}")
    eps = read_json(args.episodes)["episodes"]

    cidx = read_json(paths.CHARACTERS_DIR / "index.json")["characters"]
    amap, keep = {}, set()
    for c in cidx:
        canon = c["canonical_name"]
        if c["chapter_count"] >= args.min_chapters:
            keep.add(canon)
        amap[canon] = canon
        for a in c.get("aliases") or []:
            amap[a] = canon

    # 逐章取人物与场景
    per_ch: dict[int, tuple[set, set]] = {}
    for p in sorted(paths.CHAPTERS_DIR.glob("ch*.json")):
        d = read_json(p)
        a = d.get("analysis") or {}
        chars = set()
        for c in a.get("characters") or []:
            if not isinstance(c, dict) or c.get("mentioned_only"):
                continue
            n = amap.get((c.get("name") or "").strip(), (c.get("name") or "").strip())
            if n in keep:
                chars.add(n)
        scenes = {s.get("name") for s in a.get("scenes") or []
                  if isinstance(s, dict) and s.get("name")}
        per_ch[d["seq"]] = (chars, scenes)

    seen_c: set[str] = set()
    seen_s: set[str] = set()
    rows = []
    for e in eps:
        chars, scenes = set(), set()
        for s in range(e["seq_start"], e["seq_end"] + 1):
            c, sc = per_ch.get(s, (set(), set()))
            chars |= c
            scenes |= sc
        new_c = sorted(chars - seen_c)
        new_s = sorted(scenes - seen_s)
        seen_c |= chars
        seen_s |= scenes
        rows.append({**e,
                     "characters": sorted(chars), "scenes": sorted(scenes),
                     "new_characters": new_c, "new_scenes": new_s,
                     "asset_load": len(new_c) * 2 + len(new_s)})

    write_json(args.out, {"meta": {"episodes": len(rows),
                                   "min_chapters": args.min_chapters},
                          "episodes": rows})

    load = [r["asset_load"] for r in rows]
    print(f"{len(rows)} 集｜累计角色 {len(seen_c)} 个、场景 {len(seen_s)} 个")
    print(f"每集平均 {sum(len(r['characters']) for r in rows) / len(rows):.1f} 角色、"
          f"{sum(len(r['scenes']) for r in rows) / len(rows):.1f} 场景")
    print(f"新资产负担：均值 {sum(load) / len(load):.1f}，最重 {max(load)}")

    print("\n新资产负担最重的 12 集（排产时要提前动手）：")
    for r in sorted(rows, key=lambda r: -r["asset_load"])[:12]:
        print(f"  {r['code']} seq{r['seq_start']}~{r['seq_end']}"
              f"  新角色 {len(r['new_characters']):>2} 新场景 {len(r['new_scenes']):>2}"
              f"  {'、'.join(r['new_characters'][:5])}")

    print("\n前 10 集的资产需求：")
    for r in rows[:10]:
        print(f"  {r['code']} seq{r['seq_start']}~{r['seq_end']} {r['minutes']:>5.1f}分"
              f"  角色{len(r['characters']):>2} 场景{len(r['scenes']):>2}"
              f"  新增 {len(r['new_characters'])}人/{len(r['new_scenes'])}景")
    print(f"\n{args.out.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
