#!/usr/bin/env python3
"""
步骤 3：全量体检章节分析结果。**不调用 API**，纯程序校验，随便跑。

跑完给三样东西：
  1. 终端摘要（缺哪些章、分数分布、哪些章不达标、枚举外的字段值分布）
  2. .run/reports/quality_report.json（完整报告，可编程消费；每次重写，不入库）
  3. .run/reports/rerun_seqs.txt（需要重跑的 seq，按严重程度分三级；可直接喂 s2 的 --seqs）

退出码：有问题章 → 1，否则 0。README 的自检口径是「问题章 0」。

用法：
    python pipeline/s3_validate_chapters.py
    python pipeline/s3_validate_chapters.py --rerun-list   # 只输出 T1+T2 的 seq（空格分隔）
"""
import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import names, paths, quality
from common.jsonio import read_json, write_json
from common.novel import load_novel

REQUIRED_ANALYSIS_FIELDS = ["synopsis", "characters", "scenes", "dialogues",
                            "monologues", "narration", "beats"]

# schema 里给的是**建议值**：模型并不守枚举（narration.function 全书出现过 112 个值），
# 这里不判错，只统计分布，让人看得见口径漂到了哪里。
SUGGESTED_ENUMS = {
    "characters.role_in_chapter": ("主导", "参与", "提及"),
    "scenes.type": ("室内", "室外", "幻境梦境", "秘境", "其他"),
    "monologues.kind": ("心理活动", "回忆", "判断推理", "情绪感受"),
    "narration.function": ("世界观设定", "人物背景", "时间推移", "伏笔", "情节交代", "环境渲染"),
    "beats.beat_type": ("铺垫", "冲突", "转折", "情绪", "揭示", "收束"),
}


def check_one(doc: dict) -> dict:
    """对单章做程序校验，返回问题清单。审查分数是模型给的，这里重算客观项。"""
    problems = []
    a = doc.get("analysis") or {}
    paras = doc.get("paragraphs") or []
    n_paras = len(paras)

    for f in REQUIRED_ANALYSIS_FIELDS:
        if f not in a:
            problems.append(f"缺字段 analysis.{f}")
        elif f != "synopsis" and not isinstance(a[f], list):
            problems.append(f"analysis.{f} 不是数组")

    # 简介判两件事，且要分开判。
    # 只卡字数会误伤——58~79 字的简介多数内容完整，只是写得紧。
    # 真正的缺陷是**被截断**：话说到一半没了，末尾连句号都没有。
    syn = (a.get("synopsis") or "").strip()
    if len(syn) < config.SYNOPSIS_MIN_CHARS:
        problems.append(f"简介过短（{len(syn)}字）")
    elif syn and syn[-1] not in "。！？…”》」)）":
        problems.append(f"简介被截断（{len(syn)}字，末尾「{syn[-6:]}」）")

    # 重算逐字命中与台词覆盖，不信任落盘时记的值
    vb, cov = quality.measure(a, paras)
    if vb["counts"]["miss"]:
        problems.append(f"{vb['counts']['miss']} 条引用原文找不到（臆造）")
    if vb["counts"]["near"]:
        problems.append(f"{vb['counts']['near']} 条引用被改写")
    if vb["verbatim_rate"] < config.VERBATIM_PASS_RATE:
        problems.append(f"逐字命中率 {vb['verbatim_rate']:.1%} 低于门槛")
    if cov["dialogue_coverage"] < config.COVERAGE_PASS_RATE:
        problems.append(f"台词覆盖率仅 {cov['dialogue_coverage']:.1%}，疑似漏台词")

    # 审查分低于合格线的章，s2 记为 failed、等着 --redo-failed 重跑；体检不能装作没看见。
    # 以前这里只在「另有别的问题」时才看分数，于是 s2 说「31 章未达标」、s3 说「0 章有问题」。
    # 逐字 / 覆盖两项上面已单独报过，这里只补分数项，免得一件事报两遍。
    q = doc.get("quality") or {}
    manual = (doc.get("run") or {}).get("model") == "人工"
    _, gate_reasons = quality.gate(q.get("structure_score"), q.get("fidelity_score"), vb, cov, manual)
    for r in gate_reasons:
        if "结构分" in r or "一致性分" in r:
            problems.append(f"审查分不达标：{r}")

    # 段号越界：para 是 1 基，落在 1..段数之外说明定位错了（s14 会对不上账）
    bad_para = 0
    for field in ("dialogues", "monologues", "narration"):
        for it in a.get(field) or []:
            p = it.get("para") if isinstance(it, dict) else None
            if not isinstance(p, int) or not 1 <= p <= n_paras:
                bad_para += 1
    if bad_para:
        problems.append(f"{bad_para} 条引用的段号越界（应在 1~{n_paras}）")

    # 人物重复 / 场景人物对不上
    names_ = [c.get("name") for c in (a.get("characters") or []) if isinstance(c, dict)]
    dup = [n for n, c in Counter(names_).items() if c > 1]
    if dup:
        problems.append(f"人物条目重复：{dup}")
    known = set(names_)
    for s in (a.get("scenes") or []):
        if not isinstance(s, dict):
            continue
        unknown = [p for p in (s.get("present_characters") or []) if not names.is_known(p, known)]
        if unknown:
            problems.append(f"场景『{s.get('name')}』出现未登记人物：{unknown}")
    speakers = {d.get("speaker") for d in (a.get("dialogues") or []) if isinstance(d, dict)}
    ghost = [s for s in speakers if s and not names.is_known(s, known) and s != "未明说"]
    if ghost:
        problems.append(f"说话人不在人物名单：{ghost}")

    # 审查留痕必须齐全：两类审查记录都在、带分数、带详细分析。
    # 人工基准集例外：它的引用由程序直接从原文取，构造上不可能改写或臆造，
    # 保证比三次模型互审更强，只要求一条 fidelity_review 留痕。
    # 不再数「模型调用次数」：--phase review 只重跑审查，次数天然少于 3，
    # 但审查记录一样齐全——判据看记录，不看次数。
    stages = [r.get("stage") for r in (doc.get("reviews") or [])]
    needs = ("fidelity_review",) if manual else ("structure_review", "fidelity_review")
    for need in needs:
        if need not in stages:
            problems.append(f"缺审查记录：{need}")
    for r in (doc.get("reviews") or []):
        if r.get("stage", "").endswith("_review"):
            if r.get("score") is None:
                problems.append(f"{r['stage']} 缺 score")
            if len(r.get("analysis") or "") < config.REVIEW_ANALYSIS_MIN_CHARS:
                problems.append(f"{r['stage']} 的详细分析过短")

    return {"seq": doc.get("seq"), "chapter_id": doc.get("chapter_id"),
            "problems": problems,
            "structure_score": (doc.get("quality") or {}).get("structure_score"),
            "fidelity_score": (doc.get("quality") or {}).get("fidelity_score"),
            "verbatim_rate": vb["verbatim_rate"],
            "dialogue_coverage": cov["dialogue_coverage"],
            "repair_rounds": (doc.get("quality") or {}).get("repair_rounds", 0)}


def enum_drift(doc: dict, counter: dict[str, Counter]) -> None:
    """统计枚举外的取值。只记不判：口径漂移要看得见，但不该让 1200 章因此翻红。"""
    a = doc.get("analysis") or {}
    for key, allowed in SUGGESTED_ENUMS.items():
        field, sub = key.split(".")
        for it in a.get(field) or []:
            if isinstance(it, dict):
                v = it.get(sub)
                if v not in allowed:
                    counter[key][v] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="全量体检章节分析（不调 API）")
    ap.add_argument("--rerun-list", action="store_true",
                    help="只打印需要重跑的 seq（T1 臆造 + T2 不达标），空格分隔，可直接喂 s2 --seqs")
    args = ap.parse_args()

    total = load_novel()["meta"]["unit_count"]
    results, missing = [], []
    drift: dict[str, Counter] = {k: Counter() for k in SUGGESTED_ENUMS}
    for seq in range(1, total + 1):
        p = paths.chapter_json_path(seq)
        if not p.exists():
            missing.append(seq)
            continue
        try:
            doc = read_json(p)
            results.append(check_one(doc))
            enum_drift(doc, drift)
        except Exception as e:
            results.append({"seq": seq, "chapter_id": f"ch{seq:04d}",
                            "problems": [f"文件损坏：{type(e).__name__}: {e}"],
                            "structure_score": None, "fidelity_score": None,
                            "verbatim_rate": 0.0, "dialogue_coverage": 0.0,
                            "repair_rounds": 0})

    bad = [r for r in results if r["problems"]]

    # 按严重程度分级。全部重跑既费钱又没必要——
    # 「说话人是泛称」和「引用被改写一条」跟「臆造」不是一个量级的事。
    tiers = {"T1_臆造": [], "T2_逐字或审查不达标": [], "T3_登记不全或简介短": []}
    for r in bad:
        ps = r["problems"]
        if any("找不到" in p or "臆造" in p for p in ps):
            tiers["T1_臆造"].append(r["seq"])
        elif any("逐字命中率" in p or "审查分不达标" in p or "台词覆盖率" in p for p in ps):
            tiers["T2_逐字或审查不达标"].append(r["seq"])
        else:
            tiers["T3_登记不全或简介短"].append(r["seq"])
    rerun = sorted(missing + tiers["T1_臆造"] + tiers["T2_逐字或审查不达标"])

    if args.rerun_list:
        print(" ".join(map(str, rerun)))
        return 1 if rerun else 0

    print(f"章节总数 {total} | 已分析 {len(results)} | 未分析 {len(missing)}")
    if missing:
        print(f"  未分析 seq：{missing[:30]}{' ...' if len(missing) > 30 else ''}")

    def dist(key: str, threshold: float) -> str:
        vals = [r[key] for r in results if isinstance(r[key], (int, float))]
        if not vals:
            return "无数据"
        return (f"均值 {statistics.mean(vals):.1f} | 中位 {statistics.median(vals):.1f} | "
                f"最低 {min(vals):.2f} | <门槛{threshold:g} {sum(1 for v in vals if v < threshold)} 章")

    if results:
        print(f"\n结构审查分   {dist('structure_score', config.PASS_SCORE['structure_review'])}")
        print(f"一致性审查分 {dist('fidelity_score', config.PASS_SCORE['fidelity_review'])}")
        vr = [r["verbatim_rate"] for r in results]
        print(f"逐字命中率   均值 {statistics.mean(vr):.2%} | 最低 {min(vr):.2%} | "
              f"未达 {config.VERBATIM_PASS_RATE:.0%} 的有 "
              f"{sum(1 for v in vr if v < config.VERBATIM_PASS_RATE)} 章")
        dc = [r["dialogue_coverage"] for r in results]
        print(f"台词覆盖率   均值 {statistics.mean(dc):.2%} | 最低 {min(dc):.2%} | "
              f"未达 {config.COVERAGE_PASS_RATE:.0%} 的有 "
              f"{sum(1 for v in dc if v < config.COVERAGE_PASS_RATE)} 章")
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

    print("\n按严重程度分级：")
    print(f"  T1 臆造（违反原则二，必须清零）         {len(tiers['T1_臆造']):4d} 章")
    print(f"  T2 逐字率或审查分不达标                 {len(tiers['T2_逐字或审查不达标']):4d} 章")
    print(f"  T3 说话人未登记 / 简介过短 / 个别改写    {len(tiers['T3_登记不全或简介短']):4d} 章")

    drifted = {k: dict(v.most_common(6)) for k, v in drift.items() if v}
    if drifted:
        print("\n枚举外取值（只统计不判错；schema 里的是建议值，口径见 doc/02）：")
        for k, v in drifted.items():
            print(f"  {k:<28} 共 {sum(drift[k].values()):5d} 条  例：{v}")

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = paths.REPORTS_DIR / "quality_report.json"
    write_json(out, {"total_chapters": total, "analyzed": len(results),
                     "missing": missing, "problem_count": len(bad),
                     "rerun_seqs": rerun, "tiers": tiers,
                     "enum_drift": {k: dict(v) for k, v in drift.items()},
                     "details": results})
    seq_file = paths.REPORTS_DIR / "rerun_seqs.txt"
    seq_file.write_text("\n".join(f"{k}: {' '.join(map(str, v))}"
                                  for k, v in tiers.items() if v) + ("\n" if any(tiers.values()) else ""),
                        encoding="utf-8")
    print(f"\n完整报告：{out.relative_to(paths.ROOT)}")
    print(f"分级清单：{seq_file.relative_to(paths.ROOT)}")
    if rerun:
        print(f"\n建议只重跑 T1+T2 共 {len(rerun)} 章（全量重跑没必要，也费钱）：")
        print(f"    python pipeline/s2_analyze_chapters.py --force --seqs "
              f"{','.join(map(str, rerun[:12]))}{',...' if len(rerun) > 12 else ''}")
    # 退出码只看 T1 / T2 与缺章：那是必须重跑的；T3（登记不全、简介短）只提示，不拦流水线。
    return 1 if rerun else 0


if __name__ == "__main__":
    sys.exit(main())
