#!/usr/bin/env python3
"""
步骤 12：从数据里检测卷段（story arc）边界。**不调 API。**

## 为什么要有这一步

分集器需要「断点约束」才能让每集收在剧情的自然停顿处。
断点来自卷段划分，而卷段划分原本打算靠人读 1200 章摘要来做——
但那既慢又贵，而且**人读到第 800 章时对第 100 章的判断标准早就漂了**。

这里换一个思路：卷段的本质是「**一批人在一个地方办一件事**」，
那么卷段的边界就是**人和地同时换掉的位置**。这件事可以直接算。

## 怎么算

对每一章 i，比较它前后各 W 章的「人物集合」与「场景集合」：

    换人分 = 1 - Jaccard(前W章的人, 后W章的人)
    换景分 = 1 - Jaccard(前W章的景, 后W章的景)
    变更分 = 0.65 × 换人分 + 0.35 × 换景分

人权重更高，因为**观众跟的是人不是地**——同一批人换个地方还是同一段戏，
换一批人则必然是新的一段。

在变更分曲线上取局部极大值，加上「卷段长度 10~50 章」的约束，
用动态规划挑出一组全局最优的切点。

## 硬断与软断

**硬断只认一件事：有主要角色从此不再出现。** 这是客观事实，不依赖判断。
其余边界一律软断——只给分集器一个倾向，不给硬约束。

这个尺度是量出来的，不是拍的。把「变更分排前 15%」也算硬断时，
分集器被迫在那些点收尾，时长舒适区从 79% 掉到 70%；而那些高分点
经人工回读并不都靠谱（seq53 老拐子之死与 seq58 他的下葬被切成两段，
那是同一场戏）。改成只认角色退场后，代价降到 1 个百分点，
仍有 42/268 集能收在检测到的边界上。

## 这个检测器的能力边界

它找的是「**核心阵容换人的位置**」，不是「一个故事讲完的位置」。
两者高度相关但不等同。人工回读第一季的 10 个边界，约六成站得住，
其余属于「说得过去但不精彩」。所以它的产物只做软约束，
**不能替代人读一遍**。真要做精，还是得逐段读摘要。

用法：
    python pipeline/s12_detect_arcs.py
    python pipeline/s12_detect_arcs.py --window 8 --verify   # 打印待人工确认的边界
"""
import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths
from common.jsonio import read_json, write_json

MIN_ARC, MAX_ARC = config.ARC_CHAPTERS


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 1.0


def _core(items: list[list[str]], min_hits: int) -> set:
    """窗口内反复出现的才算「核心」，只露一次脸的不算。"""
    c = Counter(x for row in items for x in row)
    return {k for k, v in c.items() if v >= min_hits}


def change_curve(tl: list[dict], w: int, min_hits: int = 3) -> list[float]:
    """
    每一章的「变更分」：它前后各 w 章的**核心阵容**有多不一样。

    关键是「核心」二字。全书每章平均只有 3.8 个人物、1.8 个场景，
    直接拿集合做 Jaccard 会被噪声主导——同一段戏里换个配角，
    分数就跳一大截。实测裸集合法的信噪比只有 0.17，
    改成「窗口内出现 ≥min_hits 次才算数」之后提到 0.33，翻了一倍。
    """
    ch = [t["characters"] for t in tl]
    sc = [t["scenes"] for t in tl]
    out = [0.0] * len(tl)
    for i in range(len(tl)):
        lo, hi = max(0, i - w + 1), min(len(tl), i + 1 + w)
        if i + 1 >= hi or lo >= i + 1:
            continue
        pc, nc = _core(ch[lo:i + 1], min_hits), _core(ch[i + 1:hi], min_hits)
        ps, ns = _core(sc[lo:i + 1], 2), _core(sc[i + 1:hi], 2)
        # 人的权重高于景：观众跟的是人。同一批人换个地方还是同一段戏。
        cw = config.ARC_CHAR_WEIGHT
        out[i] = cw * (1 - jaccard(pc, nc)) + (1 - cw) * (1 - jaccard(ps, ns))
    return out


def pick_cuts(score: list[float], lo: int, hi: int, cut_cost: float) -> list[int]:
    """
    在 [lo, hi] 里挑一组切点，最大化 Σ(切点变更分 − 每刀成本)。动态规划，全局最优。

    **每刀必须有固定成本**。只把变更分求和的话，多切一刀总是不亏，
    于是最优解就退化成「全部按最短长度切」——实测直接切出 115 个 10 章的卷段，
    那不是卷段，那是把书剁碎。成本设在变更分的均值附近，
    等于说「只有比平均更明显的更替才配当一个卷段边界」。
    """
    n = hi - lo + 1
    NEG = float("-inf")
    best = [NEG] * (n + 1)
    prev = [-1] * (n + 1)
    best[0] = 0.0
    for i in range(n):
        if best[i] == NEG:
            continue
        for k in range(MIN_ARC, MAX_ARC + 1):
            j = i + k
            if j > n:
                break
            gain = score[lo - 1 + j - 1] - cut_cost
            if j == n:                      # 季末必切，不收成本
                gain = 0.0
            if best[i] + gain > best[j]:
                best[j] = best[i] + gain
                prev[j] = i
    if best[n] == NEG:
        return []
    cuts, j = [], n
    while j > 0:
        cuts.append(lo + j - 1)
        j = prev[j]
    return sorted(cuts)


def main() -> int:
    ap = argparse.ArgumentParser(description="从人物与场景的更替中检测卷段边界")
    ap.add_argument("--window", type=int, default=8, help="前后各看几章")
    ap.add_argument("--min-hits", type=int, default=3,
                    help="角色在窗口内出现几次才算核心阵容")
    ap.add_argument("--seasons", type=Path, default=paths.SEASONS_JSON)
    ap.add_argument("--out", type=Path, default=paths.ARCS_JSON)
    ap.add_argument("--cut-cost", type=float, default=None,
                    help="每刀的固定成本。不给就自动定在变更分的均值上")
    ap.add_argument("--verify", action="store_true", help="打印每个边界处的章节标题供人工确认")
    args = ap.parse_args()

    for f in (paths.TIMELINE_JSON, paths.CHAR_INDEX):
        if not f.exists():
            raise SystemExit(f"缺 {f.relative_to(paths.ROOT)}，先跑 s9 / s4")
    tl = read_json(paths.TIMELINE_JSON)
    score = change_curve(tl, args.window, args.min_hits)

    # 主要角色的末现——这一章之后他再也不出现了，是最硬的断点信号
    idx = read_json(paths.CHAR_INDEX)["characters"]
    last_seen = {}
    for c in idx:
        if c["chapter_count"] >= config.ARC_EXIT_MIN_CHAPTERS:
            last_seen.setdefault(c["last_seq"], []).append(c["canonical_name"])

    seasons = (read_json(args.seasons) if args.seasons.exists()
               else [{"name": "全书", "seq_start": 1, "seq_end": len(tl)}])

    cut_cost = (args.cut_cost if args.cut_cost is not None
                else statistics.mean(score))
    arcs = []
    for si, s in enumerate(seasons, 1):
        cuts = pick_cuts(score, s["seq_start"], s["seq_end"], cut_cost)
        start = s["seq_start"]
        for c in cuts:
            exits = last_seen.get(c, [])
            # 硬断的判据**只认「有主要角色从此不再出现」**。
            #
            # 一开始还把「变更分排前 15%」也算硬断，实测代价太大：
            # 分集器被迫在这些点收尾，时长舒适区从 79% 掉到 70%。
            # 而这些高分点经人工回读并不都靠谱——比如 seq53 老拐子之死
            # 与 seq58 他的下葬被切成了两段，那是同一场戏。
            # 角色退场则是客观事实，不依赖任何判断，所以只信它。
            # 其余边界一律作软断：只给倾向，不给约束，代价仅 1 个百分点。
            hard = bool(exits)
            arcs.append({"season": si, "season_name": s.get("name", ""),
                         "seq_start": start, "seq_end": c,
                         "chapters": c - start + 1,
                         "score": round(score[c - 1], 3),
                         "cut": "硬断" if hard else "软断",
                         "exits": exits,
                         "title_at_cut": tl[c - 1]["title"]})
            start = c + 1

    # 硬断只认「有主要角色从此不再出现」，不看变更分高低——
    # 所以 meta 里不再写 hard_threshold（早先写了一个 0.964，读者会误以为它参与了判定）。
    write_json(args.out, {"meta": {"window": args.window, "min_hits": args.min_hits,
                                   "cut_cost": round(cut_cost, 3),
                                   "exit_min_chapters": config.ARC_EXIT_MIN_CHAPTERS,
                                   "arc_count": len(arcs)}, "arcs": arcs})

    lens = [a["chapters"] for a in arcs]
    hard = sum(1 for a in arcs if a["cut"] == "硬断")
    print(f"检测到 {len(arcs)} 个卷段｜硬断 {hard} 软断 {len(arcs) - hard}"
          f"｜每刀成本 {cut_cost:.3f}")
    print(f"卷段长度 均值 {statistics.mean(lens):.1f} 章｜"
          f"中位 {statistics.median(lens)}｜{min(lens)}~{max(lens)}")
    for si, s in enumerate(seasons, 1):
        n = sum(1 for a in arcs if a["season"] == si)
        print(f"  S{si:02d} {s.get('name', ''):<8} {n} 个卷段")

    if args.verify:
        print("\n各卷段边界处的章节（人工确认用）：")
        for a in arcs:
            mark = "硬" if a["cut"] == "硬断" else "软"
            ex = f"　退场：{'、'.join(a['exits'])}" if a["exits"] else ""
            print(f"  [{mark}] seq{a['seq_start']:>4}~{a['seq_end']:<4}"
                  f" {a['chapters']:>2}章 分{a['score']:.2f}"
                  f"  {a['title_at_cut'][:24]}{ex}")
    print(f"\n{args.out.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
