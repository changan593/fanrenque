"""章节分析的质量块：逐字核验 + 台词覆盖 + 闸门判定，一份实现三处共用。

以前 s2 算一遍、s2m 算一遍、s5 修完只回写 verbatim 不回写 coverage/passed，
于是 668 份被 s5 修过的章里有 374 份 coverage 是修前的旧值、31 份 passed=False
但逐字早已达标。现在谁改了 analysis 都调 refresh()，整块重算。

闸门（doc/02 第三节）：
  结构审查分 ≥ PASS_SCORE.structure_review
  一致性审查分 ≥ PASS_SCORE.fidelity_review
  逐字命中率 ≥ VERBATIM_PASS_RATE
  臆造条数 = 0
  台词覆盖率 ≥ COVERAGE_PASS_RATE（原则二「不遗漏」的客观判据：原文带引号的话有多少被抽进 dialogues）
人工基准集（run.model == "人工"）没有模型分数，只看后三条，且不允许改写（引用由程序取原文）。

一致性审查员列出的 missing_items **不是**闸门。它只作为修订轮的输入（p4 按它逐条补）。
实测 1200 章里 415 章的审查员列了遗漏项，其中「是！」「蜜语」这类占大半，而程序覆盖率全部 100%——
拿它当闸门会让三分之一的章过不了，且拦的多是噪音。盯漏靠覆盖率，不靠模型的印象。

人工放行（quality.waiver）：审查分是模型给的、主观的；有的章三轮修订仍卡在结构分 82，而引用早已
100% 逐字。人读过之后可以接受它——`s5 --waive --seqs … --reason …` 记一条带理由的豁免，
refresh 时**只对分数类原因生效**：逐字率、臆造、台词覆盖这些客观项不放行。放行的原因留在
waived_reasons 里，s3 单列不算问题。
"""
from __future__ import annotations

import config
from . import verbatim


def measure(analysis: dict, paragraphs: list[str]) -> tuple[dict, dict]:
    """(逐字核验报告, 台词覆盖报告)。"""
    return verbatim.check_analysis(analysis, paragraphs), verbatim.coverage_report(analysis, paragraphs)


def gate(structure_score, fidelity_score, vb: dict, cov: dict,
         manual: bool = False) -> tuple[bool, list[str]]:
    """任一不过就要修。返回 (是否通过, 原因列表)。"""
    reasons: list[str] = []
    if not manual:
        s, f = structure_score or 0, fidelity_score or 0
        if s < config.PASS_SCORE["structure_review"]:
            reasons.append(f"结构分 {s} < {config.PASS_SCORE['structure_review']}")
        if f < config.PASS_SCORE["fidelity_review"]:
            reasons.append(f"一致性分 {f} < {config.PASS_SCORE['fidelity_review']}")
    if vb["verbatim_rate"] < config.VERBATIM_PASS_RATE:
        reasons.append(f"逐字命中率 {vb['verbatim_rate']:.2%} < {config.VERBATIM_PASS_RATE:.0%}")
    if vb["counts"]["miss"] > 0:
        reasons.append(f"存在 {vb['counts']['miss']} 条原文找不到的引用（臆造）")
    if manual and vb["counts"]["near"] > 0:
        reasons.append(f"存在 {vb['counts']['near']} 条改写（人工稿由程序取原文，不该出现）")
    if cov["dialogue_coverage"] < config.COVERAGE_PASS_RATE:
        reasons.append(f"台词覆盖率 {cov['dialogue_coverage']:.1%} < {config.COVERAGE_PASS_RATE:.0%}"
                       "（疑似漏台词）")
    return (not reasons), reasons


def refresh(doc: dict) -> dict:
    """按当前 analysis 重算 quality 里的 verbatim / coverage / passed / fail_reasons。
    分数与修订轮数来自落盘值，不动。"""
    q = doc.setdefault("quality", {})
    vb, cov = measure(doc["analysis"], doc["paragraphs"])
    manual = (doc.get("run") or {}).get("model") == "人工"
    passed, reasons = gate(q.get("structure_score"), q.get("fidelity_score"), vb, cov, manual)
    waived: list[str] = []
    if not passed and q.get("waiver") and all(is_score_reason(r) for r in reasons):
        waived, reasons, passed = reasons, [], True
    q.update(verbatim=vb, coverage=cov, passed=passed, fail_reasons=reasons)
    if waived:
        q["waived_reasons"] = waived
    else:
        q.pop("waived_reasons", None)
    return doc


def is_score_reason(reason: str) -> bool:
    """闸门原因里哪些是模型分数（可被人工放行），哪些是客观项（不可）。"""
    return reason.startswith(("结构分", "一致性分"))
