#!/usr/bin/env python3
"""
步骤 2：逐章分析。每章独立产出一个 data/chapters/chXXXX.json。

每章固定跑三次模型调用（外加一轮可选的修订）：

  ① extract           抽取     -> 人物/场景/对话/独白/旁白/节拍/简介
  ② structure_review  结构+上下文审查（回喂前几章简介）
  ③ fidelity_review   内容与原文一致性审查（喂入程序逐字核验结果）
  ④ repair            仅当 ②③ 有一项不达标时触发，修完再跑一遍核验

②③ 之间夹了一道**程序逐字核验**：把模型摘出来的每条引用回原文做字符串精确匹配。
这是唯一不依赖模型判断的客观闸门，一致性审查员必须采信它的结论。

用法：
    export DEEPSEEK_API_KEY=sk-xxx
    python pipeline/s2_analyze_chapters.py --smoke 3        # 先跑3章验管道
    python pipeline/s2_analyze_chapters.py                  # 跑全书（断点续跑）
    python pipeline/s2_analyze_chapters.py --range 1 100    # 只跑一段
    python pipeline/s2_analyze_chapters.py --phase review --force  # 全书补跑审查
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths, verbatim
from common.jsonio import append_jsonl, read_json, write_json
from common.llm import USAGE, LLMError, chat_json
from common.novel import chapter_label, chapter_text, iter_chapters, load_novel

PIPELINE_VERSION = "1.0"
SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_prompt(name: str) -> str:
    return (paths.PROMPTS_DIR / name).read_text(encoding="utf-8")


PROMPTS = {
    "extract": lambda: load_prompt("p1_extract.txt"),
    "structure_review": lambda: load_prompt("p2_structure_review.txt"),
    "fidelity_review": lambda: load_prompt("p3_fidelity_review.txt"),
    "repair": lambda: load_prompt("p4_repair.txt"),
}


# ---------------------------------------------------------------- 上下文构建

def numbered_text(ch: dict) -> str:
    """把正文按 [n] 编号喂给模型。编号让引用可定位、可核验。"""
    return "\n".join(f"[{i}] {p}" for i, p in enumerate(ch["paragraphs"], 1))


def prev_synopses(seq: int, lookback: int = 12) -> list[dict]:
    """
    取本章之前最近 N 章的简介。并发跑时紧邻的前几章可能还没落盘，
    就往前多找几章，保证审查员总有上下文可依。
    """
    out = []
    for s in range(seq - 1, max(0, seq - 1 - lookback), -1):
        p = paths.chapter_json_path(s)
        if not p.exists():
            continue
        try:
            d = read_json(p)
        except Exception:
            continue
        a = d.get("analysis") or {}
        out.append({"seq": s, "label": d.get("label", ""),
                    "synopsis": a.get("synopsis", "")})
        if len(out) >= config.PREV_SYNOPSIS_COUNT:
            break
    return list(reversed(out))


def alias_registry(before_seq: int) -> list[str]:
    """
    已完成章节里出现过的人物主名，用于统一命名写法（防止同一人前后叫法漂移）。
    只给名字，不给描述——避免模型据此往本章里塞不存在的人。
    """
    names: dict[str, int] = {}
    for p in sorted(paths.CHAPTERS_DIR.glob("ch*.json")):
        try:
            d = read_json(p)
        except Exception:
            continue
        if d.get("seq", 0) >= before_seq:
            continue
        for c in (d.get("analysis") or {}).get("characters") or []:
            n = (c or {}).get("name")
            if n:
                names[n] = names.get(n, 0) + 1
    ranked = sorted(names, key=lambda n: -names[n])
    return ranked[:config.ALIAS_REGISTRY_LIMIT]


def build_extract_user(ch: dict, registry: list[str], prevs: list[dict]) -> str:
    parts = []
    if registry:
        parts.append("【已知人物命名参考】（只用于统一写法，绝不允许把本章没出现的人写进来）\n"
                     + "、".join(registry))
    if prevs:
        parts.append("【前文提要】\n" + "\n".join(
            f"{p['label']}：{p['synopsis']}" for p in prevs))
    if ch["is_merged"]:
        extra = "、".join(f"第{t['declared_num']}章{t['title']}" for t in ch["merged_titles"])
        parts.append(f"【注意】本单元是原站点把多章合并在一页，除主标题外还并入了：{extra}。"
                     f"请按实际正文内容解析，不要为没有正文的标题编造情节。")
    parts.append(f"【本章】{chapter_label(ch)}")
    parts.append("【正文（已按段落编号，所有引用必须标注对应段落号）】\n" + numbered_text(ch))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 三次调用

def call_extract(ch: dict, registry: list[str], prevs: list[dict]) -> tuple[dict, dict]:
    return chat_json(PROMPTS["extract"](), build_extract_user(ch, registry, prevs),
                     config.TEMPERATURE["extract"])


def call_structure_review(ch: dict, analysis: dict, prevs: list[dict]) -> tuple[dict, dict]:
    import json
    user = "\n\n".join([
        "【前文提要】\n" + ("\n".join(f"{p['label']}：{p['synopsis']}" for p in prevs)
                            or "（本章为开篇或前文尚未解析，无需做跨章连贯性判断，"
                               "请只审结构，并在 analysis 中说明这一点）"),
        f"【本章】{chapter_label(ch)}（正文 {ch['char_count']} 字，共 {ch['paragraph_count']} 段）",
        "【待审查的解析结果】\n" + json.dumps(analysis, ensure_ascii=False, indent=1),
    ])
    return chat_json(PROMPTS["structure_review"](), user,
                     config.TEMPERATURE["structure_review"])


def call_fidelity_review(ch: dict, analysis: dict, vb: dict, cov: dict) -> tuple[dict, dict]:
    import json
    report = {"逐字核验": vb, "台词覆盖率": cov}
    user = "\n\n".join([
        f"【本章原文】{chapter_label(ch)}\n" + numbered_text(ch),
        "【待审查的解析结果】\n" + json.dumps(analysis, ensure_ascii=False, indent=1),
        "【程序逐字核验报告】（客观事实，必须采信）\n"
        + json.dumps(report, ensure_ascii=False, indent=1),
    ])
    return chat_json(PROMPTS["fidelity_review"](), user,
                     config.TEMPERATURE["fidelity_review"])


def call_repair(ch: dict, analysis: dict, sr: dict, fr: dict, vb: dict) -> tuple[dict, dict]:
    import json
    user = "\n\n".join([
        f"【本章原文】{chapter_label(ch)}\n" + numbered_text(ch),
        "【原解析结果】\n" + json.dumps(analysis, ensure_ascii=False, indent=1),
        "【结构审查意见】\n" + json.dumps(
            {k: sr.get(k) for k in ("score", "analysis", "issues")}, ensure_ascii=False, indent=1),
        "【一致性审查意见】\n" + json.dumps(
            {k: fr.get(k) for k in ("score", "analysis", "issues", "missing_items")},
            ensure_ascii=False, indent=1),
        "【程序逐字核验报告】\n" + json.dumps(vb, ensure_ascii=False, indent=1),
    ])
    return chat_json(PROMPTS["repair"](), user, config.TEMPERATURE["repair"])


# ---------------------------------------------------------------- 单章流程

def review_record(stage: str, rnd: int, obj: dict, meta: dict) -> dict:
    """统一的审查留痕格式：分数 + 详细分析 + 问题清单 + 调用元信息。"""
    return {
        "stage": stage,
        "round": rnd,
        "score": obj.get("score"),
        "verdict": obj.get("verdict"),
        "analysis": obj.get("analysis", ""),
        "issues": obj.get("issues", []),
        "checked_items": obj.get("checked_items", {}),
        "missing_items": obj.get("missing_items", {}),
        "model": config.MODEL,
        "tokens": meta.get("tokens", {}),
        "attempts": meta.get("attempts"),
        "reviewed_at": _now(),
    }


def quality_gate(sr: dict, fr: dict, vb: dict) -> tuple[bool, list[str]]:
    """三个门槛：结构分、一致性分、程序逐字命中率。任一不过就要修。"""
    reasons = []
    s, f = sr.get("score") or 0, fr.get("score") or 0
    if s < config.PASS_SCORE["structure_review"]:
        reasons.append(f"结构分 {s} < {config.PASS_SCORE['structure_review']}")
    if f < config.PASS_SCORE["fidelity_review"]:
        reasons.append(f"一致性分 {f} < {config.PASS_SCORE['fidelity_review']}")
    if vb["verbatim_rate"] < config.VERBATIM_PASS_RATE:
        reasons.append(f"逐字命中率 {vb['verbatim_rate']:.2%} < {config.VERBATIM_PASS_RATE:.0%}")
    if vb["counts"]["miss"] > 0:
        reasons.append(f"存在 {vb['counts']['miss']} 条原文找不到的引用（臆造）")
    return (not reasons), reasons


def analyze_chapter(ch: dict, phase: str = "all") -> dict:
    seq = ch["seq"]
    started = _now()
    t0 = time.time()
    paras = ch["paragraphs"]
    prevs = prev_synopses(seq)
    reviews: list[dict] = []
    calls = 0

    # ① 抽取（phase=review 时复用已有结果，只重跑审查）
    if phase == "review":
        prev_doc = read_json(paths.chapter_json_path(seq))
        analysis = prev_doc["analysis"]
        reviews = [r for r in prev_doc.get("reviews", []) if r.get("stage") == "extract_meta"]
    else:
        analysis, meta = call_extract(ch, alias_registry(seq), prevs)
        calls += 1

    # 程序逐字核验（客观闸门）
    vb = verbatim.check_analysis(analysis, paras)
    cov = verbatim.coverage_report(analysis, paras)

    # ② 结构与上下文审查
    sr_obj, sr_meta = call_structure_review(ch, analysis, prevs)
    reviews.append(review_record("structure_review", 0, sr_obj, sr_meta))
    calls += 1

    # ③ 内容一致性审查
    fr_obj, fr_meta = call_fidelity_review(ch, analysis, vb, cov)
    reviews.append(review_record("fidelity_review", 0, fr_obj, fr_meta))
    calls += 1

    # ④ 不达标则修订，修完重新核验 + 重新审一致性
    passed, reasons = quality_gate(sr_obj, fr_obj, vb)
    rounds = 0
    while not passed and rounds < config.MAX_REPAIR_ROUNDS:
        rounds += 1
        try:
            analysis, _ = call_repair(ch, analysis, sr_obj, fr_obj, vb)
            calls += 1
        except LLMError as e:
            reviews.append({"stage": "repair", "round": rounds, "error": str(e),
                            "reviewed_at": _now()})
            break
        vb = verbatim.check_analysis(analysis, paras)
        cov = verbatim.coverage_report(analysis, paras)
        fr_obj, fr_meta = call_fidelity_review(ch, analysis, vb, cov)
        reviews.append(review_record("fidelity_review", rounds, fr_obj, fr_meta))
        calls += 1
        passed, reasons = quality_gate(sr_obj, fr_obj, vb)

    return {
        "schema_version": SCHEMA_VERSION,
        "seq": seq,
        "chapter_id": ch["chapter_id"],
        "declared_num": ch["declared_num"],
        "title": ch["title"],
        "label": chapter_label(ch),
        "merged_titles": ch["merged_titles"],
        "is_merged": ch["is_merged"],
        "source": {
            "line_start": ch["line_start"], "line_end": ch["line_end"],
            "char_count": ch["char_count"], "paragraph_count": ch["paragraph_count"],
        },
        "raw_text": chapter_text(ch),
        "paragraphs": paras,
        "analysis": analysis,
        "quality": {
            "passed": passed,
            "fail_reasons": reasons,
            "structure_score": sr_obj.get("score"),
            "fidelity_score": fr_obj.get("score"),
            "verbatim": vb,
            "coverage": cov,
            "repair_rounds": rounds,
            "context_chapters_used": [p["seq"] for p in prevs],
        },
        "reviews": reviews,
        "run": {
            "pipeline_version": PIPELINE_VERSION,
            "model": config.MODEL,
            "phase": phase,
            "llm_calls": calls,
            "started_at": started,
            "finished_at": _now(),
            "elapsed_sec": round(time.time() - t0, 1),
        },
    }


# ---------------------------------------------------------------- 批量调度

def worker(ch: dict, phase: str, force: bool) -> tuple[int, str]:
    out = paths.chapter_json_path(ch["seq"])
    if out.exists() and not force and phase == "all":
        return ch["seq"], "skip"
    if phase == "review" and not out.exists():
        return ch["seq"], "skip(未抽取)"
    try:
        doc = analyze_chapter(ch, phase)
        write_json(out, doc)
        q = doc["quality"]
        append_jsonl(paths.LOG_DIR / "s2_runs.jsonl", {
            "ts": _now(), "seq": ch["seq"], "passed": q["passed"],
            "structure": q["structure_score"], "fidelity": q["fidelity_score"],
            "verbatim_rate": q["verbatim"]["verbatim_rate"],
            "miss": q["verbatim"]["counts"]["miss"], "repairs": q["repair_rounds"],
            "calls": doc["run"]["llm_calls"], "elapsed": doc["run"]["elapsed_sec"],
        })
        flag = "OK " if q["passed"] else "!! "
        return ch["seq"], (f"{flag}结构{q['structure_score']} 一致{q['fidelity_score']} "
                           f"逐字{q['verbatim']['verbatim_rate']:.0%} "
                           f"修订{q['repair_rounds']}")
    except Exception as e:
        append_jsonl(paths.LOG_DIR / "s2_errors.jsonl",
                     {"ts": _now(), "seq": ch["seq"], "error": f"{type(e).__name__}: {e}"})
        return ch["seq"], f"ERROR {type(e).__name__}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser(description="逐章分析（三次调用 + 可选修订轮）")
    ap.add_argument("--range", nargs=2, type=int, metavar=("START", "END"),
                    help="只处理 seq 区间（含两端）")
    ap.add_argument("--smoke", type=int, metavar="N", help="只跑前 N 章，验管道用")
    ap.add_argument("--phase", choices=["all", "review"], default="all",
                    help="all=完整跑；review=复用已有抽取结果，只重跑审查")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的章节 json")
    ap.add_argument("--workers", type=int, default=config.WORKERS)
    args = ap.parse_args()

    paths.ensure_dirs()
    total = load_novel()["meta"]["unit_count"]
    start, end = (args.range if args.range else (1, args.smoke or total))
    chapters = list(iter_chapters(start, end))
    todo = [c for c in chapters
            if args.force or args.phase == "review"
            or not paths.chapter_json_path(c["seq"]).exists()]

    print(f"模型 {config.MODEL} | 阶段 {args.phase} | 并发 {args.workers}")
    print(f"范围 seq {start}~{end}（{len(chapters)} 章），待处理 {len(todo)} 章\n")
    if not todo:
        print("没有需要处理的章节。")
        return

    t0 = time.time()
    done = 0
    # 按 seq 顺序分批：让后面的章节尽量能读到前面章节的简介做连贯性审查
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i in range(0, len(todo), args.workers):
            batch = todo[i:i + args.workers]
            for seq, msg in pool.map(lambda c: worker(c, args.phase, args.force), batch):
                done += 1
                print(f"[{done}/{len(todo)}] seq{seq:4d}  {msg}", flush=True)

    print(f"\n用时 {time.time() - t0:.0f}s | {USAGE.summary()}")
    print(f"日志：{(paths.LOG_DIR / 's2_runs.jsonl').relative_to(paths.ROOT)}")
    print("下一步：python pipeline/s3_validate_chapters.py")


if __name__ == "__main__":
    main()
