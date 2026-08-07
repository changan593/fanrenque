#!/usr/bin/env python3
"""
步骤 10：分集规划。**不调 API，是个约束求解器。**

## 为什么不能固定「4 章一集」

按试点定下的时长模型（语音字数 ÷ 4.5 字每秒 × 停顿系数）实测全书：

    单章纯语音   均值 5.1 分，最短 0.5 分，最长 17.6 分
    固定 4 章/集 均值 20.4 分，**区间 9~42 分**

9 分钟和 42 分钟摆在同一季里，观感是断裂的。所以章数必须可变，
按**时长**打包，让每集落在目标区间内。

## 目标函数

在「每集 3~5 章」的范围内枚举切法，最小化：

    Σ 时长偏离惩罚 + Σ 断点惩罚

- **时长偏离**：离目标时长越远罚越重，超出硬区间罚得极重
- **断点**：一集应当收在剧情的自然停顿处。卷段的硬断点最好，
  软断点次之，从卷段中间劈开最差——观众会觉得「这集怎么没讲完就完了」

用最短路解（每章一个节点，边是「这一集包含哪几章」），全局最优，
不是贪心。1200 章规模下毫秒级。

## 硬约束

- 一集**不跨季**
- 一集**不跨卷段的硬断点**（硬断点是人物死亡、地点彻底更换、大矛盾了结）

用法：
    python pipeline/s10_episode_plan.py --dry-run       # 只看时长分布
    python pipeline/s10_episode_plan.py --seasons data/plot/seasons.json
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import read_json, write_json

# 试点标定：E01 语音 6342 字实测成片约 28 分、E02 语音 6901 字约 31 分，
# 折算停顿/动作/转场系数 1.19~1.21，取 1.20。
CHARS_PER_SEC = 4.5
PAUSE_FACTOR = 1.20

TARGET_MIN = 28.0        # 目标时长（分钟）
SOFT_LO, SOFT_HI = 25.0, 32.0    # 舒适区间，用户已定「质量优先，超时无妨」
HARD_LO, HARD_HI = 18.0, 42.0    # 越过这条罚到几乎不可选
MIN_CH, MAX_CH = 3, 5            # 每集章数范围


def chapter_minutes(seq: int) -> float:
    a = read_json(paths.chapter_json_path(seq)).get("analysis") or {}
    n = sum(len(x.get("text") or "")
            for f in ("dialogues", "monologues", "narration")
            for x in a.get(f) or [] if isinstance(x, dict))
    return n / CHARS_PER_SEC / 60 * PAUSE_FACTOR


def load_minutes(total: int) -> list[float]:
    return [chapter_minutes(s) for s in range(1, total + 1)]


def duration_penalty(m: float) -> float:
    if m < HARD_LO or m > HARD_HI:
        return 1e5 + abs(m - TARGET_MIN) * 100      # 越界，几乎不可选
    if SOFT_LO <= m <= SOFT_HI:
        return (m - TARGET_MIN) ** 2 * 0.5          # 舒适区内，轻罚
    return (m - TARGET_MIN) ** 2 * 3                # 出了舒适区但没越界


def plan_range(lo: int, hi: int, minutes: list[float],
               hard_cuts: set[int], soft_cuts: set[int]) -> list[dict]:
    """
    对 [lo, hi]（含两端，1-based seq）求最优分集。最短路，全局最优。
    hard_cuts / soft_cuts 里放的是「这一章之后是断点」的 seq。
    """
    n = hi - lo + 1
    INF = float("inf")
    cost = [INF] * (n + 1)
    prev = [-1] * (n + 1)
    cost[0] = 0.0
    for i in range(n):
        if cost[i] == INF:
            continue
        for k in range(MIN_CH, MAX_CH + 1):
            j = i + k
            if j > n:
                break
            m = sum(minutes[lo - 1 + i:lo - 1 + j])
            end_seq = lo + j - 1
            c = duration_penalty(m)
            # 断点惩罚：收在硬断点最好，软断点次之，从卷段中间劈开最差
            if end_seq in hard_cuts or end_seq == hi:
                pass
            elif end_seq in soft_cuts:
                c += 8
            else:
                c += 25
            # 一集内部不许跨硬断点——那等于把一个了结的矛盾拖进下一集
            if any(s in hard_cuts for s in range(lo + i, end_seq)):
                continue
            if cost[i] + c < cost[j]:
                cost[j] = cost[i] + c
                prev[j] = i
    if cost[n] == INF:                 # 约束太紧解不出来，放宽章数范围重来
        return []
    cuts, j = [], n
    while j > 0:
        cuts.append((prev[j], j))
        j = prev[j]
    cuts.reverse()
    out = []
    for a, b in cuts:
        s0, s1 = lo + a, lo + b - 1
        m = sum(minutes[s0 - 1:s1])
        out.append({"seq_start": s0, "seq_end": s1, "chapters": s1 - s0 + 1,
                    "minutes": round(m, 1),
                    "cut": ("硬断" if s1 in hard_cuts else
                            "软断" if s1 in soft_cuts else "卷段中")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="分集规划（时长/断点约束求解）")
    ap.add_argument("--seasons", type=Path,
                    help="分季定义 json：[{name, seq_start, seq_end}, ...]。"
                         "不给就把全书当一季排，用于先看时长分布")
    ap.add_argument("--arcs", type=Path, default=paths.PLOT_DIR / "arcs.json",
                    help="卷段定义 json，含 hard_cuts / soft_cuts")
    ap.add_argument("--out", type=Path, default=paths.PLOT_DIR / "episodes.json")
    ap.add_argument("--dry-run", action="store_true", help="只报时长分布，不排集")
    args = ap.parse_args()

    total = read_json(paths.NOVEL_INDEX)["meta"]["unit_count"]
    minutes = load_minutes(total)

    print(f"全书 {total} 章｜纯语音 {sum(minutes) / PAUSE_FACTOR / 60:.0f} 小时"
          f"｜含停顿 {sum(minutes) / 60:.0f} 小时")
    print(f"单章时长 均值 {statistics.mean(minutes):.1f} 分"
          f"｜中位 {statistics.median(minutes):.1f}"
          f"｜最短 {min(minutes):.1f}｜最长 {max(minutes):.1f}")
    if args.dry_run:
        return 0

    hard_cuts, soft_cuts = set(), set()
    if args.arcs.exists():
        arcs = read_json(args.arcs)
        for a in arcs.get("arcs") or []:
            (hard_cuts if a.get("cut") == "硬断" else soft_cuts).add(a["seq_end"])
        print(f"卷段断点：硬断 {len(hard_cuts)} 处，软断 {len(soft_cuts)} 处")
    else:
        print(f"⚠ 未找到 {args.arcs}，本次不带断点约束，只按时长排")

    if args.seasons and args.seasons.exists():
        seasons = read_json(args.seasons)
    else:
        seasons = [{"name": "全书（未分季）", "seq_start": 1, "seq_end": total}]
        print("⚠ 未给分季定义，按整本排，仅供看总集数")

    all_eps, rows = [], []
    for si, s in enumerate(seasons, 1):
        eps = plan_range(s["seq_start"], s["seq_end"], minutes, hard_cuts, soft_cuts)
        for ei, e in enumerate(eps, 1):
            e["season"] = si
            e["season_name"] = s.get("name", "")
            e["episode"] = ei
            e["code"] = f"S{si:02d}E{ei:02d}"
        all_eps += eps
        ms = [e["minutes"] for e in eps]
        rows.append((si, s.get("name", ""), len(eps),
                     statistics.mean(ms) if ms else 0, min(ms) if ms else 0,
                     max(ms) if ms else 0))

    print(f"\n{'季':>3} {'名称':<22} {'集数':>4} {'均时长':>7} {'最短':>6} {'最长':>6}")
    for si, nm, n, avg, lo, hi in rows:
        print(f"{si:>3} {nm[:22]:<22} {n:>4} {avg:>6.1f}分 {lo:>5.1f} {hi:>5.1f}")

    ms = [e["minutes"] for e in all_eps]
    cuts = {}
    for e in all_eps:
        cuts[e["cut"]] = cuts.get(e["cut"], 0) + 1
    print(f"\n合计 {len(all_eps)} 集｜均 {statistics.mean(ms):.1f} 分"
          f"｜{sum(1 for m in ms if SOFT_LO <= m <= SOFT_HI) / len(ms):.0%} 落在 "
          f"{SOFT_LO:.0f}~{SOFT_HI:.0f} 分舒适区")
    print(f"收尾断点分布：{cuts}")

    write_json(args.out, {"meta": {"chars_per_sec": CHARS_PER_SEC,
                                   "pause_factor": PAUSE_FACTOR,
                                   "target_min": TARGET_MIN,
                                   "episode_count": len(all_eps)},
                          "episodes": all_eps})
    print(f"\n分集表：{args.out.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
