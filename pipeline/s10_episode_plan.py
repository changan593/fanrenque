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

在「每集 2~5 章」的范围内枚举切法，最小化：

    Σ 时长偏离惩罚 + Σ 断点惩罚

- **时长偏离**：离目标时长越远罚越重，超出硬区间罚得极重
- **断点**：一集应当收在剧情的自然停顿处。卷段的硬断点最好，
  软断点次之，从卷段中间劈开最差——观众会觉得「这集怎么没讲完就完了」

用最短路解（每章一个节点，边是「这一集包含哪几章」），全局最优，
不是贪心。1200 章规模下毫秒级。

## 硬约束

- 一集**不跨季**
- 一集**不跨卷段的硬断点**（硬断点是人物死亡、地点彻底更换、大矛盾了结）

⚠ 它会**改写** data/plot/episodes.json。第一季 47 集剧本都建立在现有边界上；
   已写剧本的集（production/sNN/ENN/剧本.md 存在）边界锁定不重排；其余集改了
   seasons.json / arcs.json / 时长参数 / 章节数据后边界会变，
   跑之前先确认你真的想重算。只想看分布用 --dry-run。

用法：
    python pipeline/s10_episode_plan.py --dry-run       # 只看时长分布，不落盘
    python pipeline/s10_episode_plan.py                 # 按 data/manual/seasons.json 排六季
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths
from common.jsonio import read_json, write_json
from common.novel import load_index

# 参数全部在 config.py「分集求解（s10）」一节；这里只取别名，便于阅读。
# 时长模型的系数 1.20 由 E01/E02 两集人工试点标定（doc/08 第五节）。
# 这些时长只是**排分集边界用的估值**，doc/00 已决定不对成片时长做预设。
CHARS_PER_SEC = config.CHARS_PER_SEC
PAUSE_FACTOR = config.PAUSE_FACTOR
TARGET_MIN = config.EPISODE_TARGET_MIN
SOFT_LO, SOFT_HI = config.EPISODE_SOFT_RANGE
HARD_LO, HARD_HI = config.EPISODE_HARD_RANGE
MIN_CH, MAX_CH = config.EPISODE_CHAPTERS


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
                c += config.CUT_PENALTY_SOFT
            else:
                c += config.CUT_PENALTY_NONE
            # 一集内部不许跨硬断点——那等于把一个了结的矛盾拖进下一集
            if any(s in hard_cuts for s in range(lo + i, end_seq)):
                continue
            if cost[i] + c < cost[j]:
                cost[j] = cost[i] + c
                prev[j] = i
    if cost[n] == INF:
        # 约束太紧解不出来（比如两个硬断点只隔一章）。不能静默返回空列表——
        # 那会让这一季悄悄变成 0 集，下游 s11/s13/s14 全部对不上。
        raise SystemExit(f"seq {lo}~{hi} 在每集 {MIN_CH}~{MAX_CH} 章的约束下无解。"
                         f"检查该区间的硬断点（arcs.json 里 cut=硬断）是否挨得太近，"
                         f"或放宽 config.EPISODE_CHAPTERS。")
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


def plan_season(lo: int, hi: int, minutes: list[float], hard_cuts: set, soft_cuts: set,
                fixed: list[dict]) -> list[dict]:
    """
    排一季。`fixed` 是这一季里**已经写了剧本的集**（上一版分集表里的原条目），边界原样保留，
    求解器只排它们之间的空档。

    分集表是求解出来的：时长参数、卷段断点、章节数据任何一样变了，边界都会移动。
    但剧本是按上一版边界写的，每集的 seq 范围就是那份剧本的合同（s14 也按它对账）。
    实测这次重构后 timeline 变了，S01 有 16 集的边界跟着动了——而 S01 的 47 集剧本早已写完。
    所以有剧本的集一律锁死，只重排还没写剧本的部分。
    """
    fixed = sorted(fixed, key=lambda e: e["seq_start"])
    for f in fixed:
        if not lo <= f["seq_start"] <= f["seq_end"] <= hi:
            raise SystemExit(f"{f['code']}（seq {f['seq_start']}~{f['seq_end']}）不在本季范围 {lo}~{hi} 内，"
                             f"分季表改了？有剧本的集不能跨季移动。")
    eps, cursor = [], lo
    for f in fixed:
        if cursor < f["seq_start"]:
            eps += plan_range(cursor, f["seq_start"] - 1, minutes, hard_cuts, soft_cuts)
        e = {k: f[k] for k in ("seq_start", "seq_end", "cut") if k in f}
        e["chapters"] = f["seq_end"] - f["seq_start"] + 1
        e["minutes"] = round(sum(minutes[f["seq_start"] - 1:f["seq_end"]]), 1)
        e["locked"] = True
        eps.append(e)
        cursor = f["seq_end"] + 1
    if cursor <= hi:
        eps += plan_range(cursor, hi, minutes, hard_cuts, soft_cuts)
    return eps


def main() -> int:
    ap = argparse.ArgumentParser(description="分集规划（时长/断点约束求解）")
    ap.add_argument("--seasons", type=Path, default=paths.SEASONS_JSON,
                    help="分季定义 json：[{name, seq_start, seq_end}, ...]。"
                         f"默认 {paths.SEASONS_JSON.relative_to(paths.ROOT)}；传 none 把全书当一季排")
    ap.add_argument("--arcs", type=Path, default=paths.ARCS_JSON,
                    help="卷段定义 json，含 hard_cuts / soft_cuts")
    ap.add_argument("--out", type=Path, default=paths.EPISODES_JSON)
    ap.add_argument("--dry-run", action="store_true", help="只报时长分布，不排集")
    args = ap.parse_args()

    total = load_index()["meta"]["unit_count"]
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

    if str(args.seasons).lower() != "none" and args.seasons.exists():
        seasons = read_json(args.seasons)
    else:
        seasons = [{"name": "全书（未分季）", "seq_start": 1, "seq_end": total}]
        print("⚠ 未给分季定义，按整本排，仅供看总集数")

    # 已写剧本的集从上一版分集表里原样搬过来，边界不动（见 plan_season）
    locked: dict[int, list[dict]] = {}
    if args.out.exists():
        for e in (read_json(args.out).get("episodes") or []):
            if paths.script_path(e["code"]).exists():
                locked.setdefault(e["season"], []).append(e)
    if locked:
        print(f"已有剧本的集：{sum(map(len, locked.values()))} 集，边界锁定不重排")

    all_eps, rows = [], []
    for si, s in enumerate(seasons, 1):
        fixed = locked.get(si, [])
        eps = plan_season(s["seq_start"], s["seq_end"], minutes, hard_cuts, soft_cuts, fixed)
        for ei, e in enumerate(eps, 1):
            e["season"] = si
            e["season_name"] = s.get("name", "")
            e["episode"] = ei
            e["code"] = f"S{si:02d}E{ei:02d}"
        renamed = [(f["code"], e["code"]) for f in fixed for e in eps
                   if e.get("locked") and e["seq_start"] == f["seq_start"] and e["code"] != f["code"]]
        if renamed:
            raise SystemExit(f"第 {si} 季有剧本的集会被重新编号：{renamed[:5]}——"
                             f"剧本目录按集号命名，重编号会对不上。先处理前面空档的排法。")
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
