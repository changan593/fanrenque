#!/usr/bin/env python3
"""
步骤 3：全量体检章节分析结果。**不调用 API**，纯程序校验，随便跑。

跑完给三样东西：
  1. 终端摘要（缺哪些章、分数分布、哪些章不达标）
  2. data/plot/quality_report.json（完整报告，可编程消费）
  3. 需要重跑的 seq 列表（可直接喂给 s2 的 --range）

用法：
    python pipeline/s3_validate_chapters.py
    python pipeline/s3_validate_chapters.py --rerun-list   # 只输出待重跑的 seq
"""
import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths, verbatim
from common.jsonio import read_json, write_json
from common.novel import load_novel

REQUIRED_ANALYSIS_FIELDS = ["synopsis", "characters", "scenes", "dialogues",
                            "monologues", "narration", "beats"]


def check_one(doc: dict) -> dict:
    """对单章做程序校验，返回问题清单。审查分数是模型给的，这里重算客观项。"""
    problems = []
    a = doc.get("analysis") or {}
    paras = doc.get("paragraphs") or []

    for f in REQUIRED_ANALYSIS_FIELDS:
        if f not in a:
            problems.append(f"缺字段 analysis.{f}")
        elif f != "synopsis" and not isinstance(a[f], list):
            problems.append(f"analysis.{f} 不是数组")

    syn = a.get("synopsis", "")
    if len(syn) < 80:
        problems.append(f"简介过短（{len(syn)}字）")

    # 重算逐字命中，不信任落盘时记的值
    vb = verbatim.check_analysis(a, paras)
    if vb["counts"]["miss"]:
        problems.append(f"{vb['counts']['miss']} 条引用原文找不到（臆造）")
    if vb["counts"]["near"]:
        problems.append(f"{vb['counts']['near']} 条引用被改写")
    if vb["verbatim_rate"] < config.VERBATIM_PASS_RATE:
        problems.append(f"逐字命中率 {vb['verbatim_rate']:.1%} 低于门槛")

    cov = verbatim.coverage_report(a, paras)
    if cov["dialogue_coverage"] < 0.9:
        problems.append(f"台词覆盖率仅 {cov['dialogue_coverage']:.1%}，疑似漏台词")

    # 人物重复 / 场景人物对不上
    names = [c.get("name") for c in (a.get("characters") or []) if isinstance(c, dict)]
    dup = [n for n, c in Counter(names).items() if c > 1]
    if dup:
        problems.append(f"人物条目重复：{dup}")
    known = set(names)
    for s in (a.get("scenes") or []):
        if not isinstance(s, dict):
            continue
        unknown = [p for p in (s.get("present_characters") or []) if p not in known]
        if unknown:
            problems.append(f"场景『{s.get('name')}』出现未登记人物：{unknown}")
    speakers = {d.get("speaker") for d in (a.get("dialogues") or []) if isinstance(d, dict)}
    ghost = [s for s in speakers if s and s not in known and s != "未明说"]
    if ghost:
        problems.append(f"说话人不在人物名单：{ghost}")

    # 审查留痕必须齐全（用户要求：每章至少三次调用、审查记录带分数和详细分析）
    stages = [r.get("stage") for r in (doc.get("reviews") or [])]
    for need in ("structure_review", "fidelity_review"):
        if need not in stages:
            problems.append(f"缺审查记录：{need}")
    for r in (doc.get("reviews") or []):
        if r.get("stage", "").endswith("_review"):
            if r.get("score") is None:
                problems.append(f"{r['stage']} 缺 score")
            if len(r.get("analysis") or "") < 100:
                problems.append(f"{r['stage']} 的详细分析过短")
    if (doc.get("run") or {}).get("llm_calls", 0) < 3:
        problems.append("模型调用次数不足 3 次")

    return {"seq": doc.get("seq"), "chapter_id": doc.get("chapter_id"),
            "problems": problems,
            "structure_score": (doc.get("quality") or {}).get("structure_score"),
            "fidelity_score": (doc.get("quality") or {}).get("fidelity_score"),
            "verbatim_rate": vb["verbatim_rate"],
            "dialogue_coverage": cov["dialogue_coverage"],
            "repair_rounds": (doc.get("quality") or {}).get("repair_rounds", 0)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun-list", action="store_true", help="只打印需要重跑的 seq")
    args = ap.parse_args()

    total = load_novel()["meta"]["unit_count"]
    results, missing = [], []
    for seq in range(1, total + 1):
        p = paths.chapter_json_path(seq)
        if not p.exists():
            missing.append(seq)
            continue
        try:
            results.append(check_one(read_json(p)))
        except Exception as e:
            results.append({"seq": seq, "chapter_id": f"ch{seq:04d}",
                            "problems": [f"文件损坏：{type(e).__name__}: {e}"],
                            "structure_score": None, "fidelity_score": None,
                            "verbatim_rate": 0.0, "dialogue_coverage": 0.0,
                            "repair_rounds": 0})

    bad = [r for r in results if r["problems"]]
    rerun = sorted(missing + [r["seq"] for r in bad])

    if args.rerun_list:
        print(" ".join(map(str, rerun)))
        return

    print(f"章节总数 {total} | 已分析 {len(results)} | 未分析 {len(missing)}")
    if missing:
        print(f"  未分析 seq：{missing[:30]}{' ...' if len(missing) > 30 else ''}")

    def dist(key: str) -> str:
        vals = [r[key] for r in results if isinstance(r[key], (int, float))]
        if not vals:
            return "无数据"
        return (f"均值 {statistics.mean(vals):.1f} | 中位 {statistics.median(vals):.1f} | "
                f"最低 {min(vals):.2f} | <门槛 {sum(1 for v in vals if v < 0.95 or v < 85)} 章")

    if results:
        print(f"\n结构审查分   {dist('structure_score')}")
        print(f"一致性审查分 {dist('fidelity_score')}")
        vr = [r["verbatim_rate"] for r in results]
        print(f"逐字命中率   均值 {statistics.mean(vr):.2%} | 最低 {min(vr):.2%} | "
              f"未达 {config.VERBATIM_PASS_RATE:.0%} 的有 "
              f"{sum(1 for v in vr if v < config.VERBATIM_PASS_RATE)} 章")
        dc = [r["dialogue_coverage"] for r in results]
        print(f"台词覆盖率   均值 {statistics.mean(dc):.2%} | 最低 {min(dc):.2%}")
        print(f"触发过修订   {sum(1 for r in results if r['repair_rounds'])} 章")

    print(f"\n有问题的章节 {len(bad)} 个")
    for r in bad[:25]:
        print(f"  {r['chapter_id']}: " + "；".join(r["problems"][:3]))
    if len(bad) > 25:
        print(f"  ...另有 {len(bad) - 25} 章，详见报告文件")

    counter = Counter(p.split("（")[0].split("：")[0][:14]
                      for r in bad for p in r["problems"])
    if counter:
        print("\n问题类型分布：")
        for k, v in counter.most_common(12):
            print(f"  {v:5d}  {k}")

    out = paths.PLOT_DIR / "quality_report.json"
    write_json(out, {"total_chapters": total, "analyzed": len(results),
                     "missing": missing, "problem_count": len(bad),
                     "rerun_seqs": rerun, "details": results})
    print(f"\n完整报告：{out.relative_to(paths.ROOT)}")
    if rerun:
        print(f"重跑命令：python pipeline/s2_analyze_chapters.py --force --range "
              f"{min(rerun)} {max(rerun)}")


if __name__ == "__main__":
    main()
