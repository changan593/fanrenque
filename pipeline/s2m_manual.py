#!/usr/bin/env python3
"""
人工分析装配器：把手写的分析稿装配成标准章节 json。**不调 API。**

用途：产出一小批**人工基准集**，有两个作用——
  1. 验证整条流水线（分析 → 资产 → 分集 → 脚本）走得通
  2. 当作标尺，用来校准 deepseek-v4-flash 跑出来的质量

关键设计：**引用不手打，由程序从原文精确取。**

手写稿里只写「第几段、第几条引号」，正文由本脚本从 novel.json 里取，
所以逐字率天然是 100%，不可能出现改写或臆造。人的精力全部放在
真正需要判断的地方：说话人归属、心理活动的主体、哪些旁白算关键、
节拍怎么划、场景怎么归。

这个思路也应该反哺到模型提示词上——让模型输出「段号 + 引号序号」
而不是让它复述原文，能从根子上消灭改写和臆造。

手写稿格式（production/s01/manual_analysis/chNNNN.analysis.json）：

    "dialogues":  [{"para": 13, "q": 0, "speaker": "老拐子", "addressee": "唐真",
                    "manner": "打趣"}]          // q = 该段第几条引号内容，0 起
    "monologues": [{"para": 19, "character": "唐真", "kind": "心理活动"}]
                                                // 不给 text 就取整段
    "narration":  [{"para": 4, "function": "情节交代"}]
    "characters": [{"name": "唐真", "appearance_quotes": [{"para": 16}], ...}]
                                                // 也可写 {"para":16,"text":"子串"}

用法：
    python pipeline/s2m_manual.py            # 装配 + 核验 + 落盘
    python pipeline/s2m_manual.py --check    # 只核验不落盘
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths, verbatim
from common.jsonio import read_json, write_json
from common.novel import chapter_label, chapter_text, get_chapter

MANUAL_DIR = paths.ROOT / "production" / "s01" / "manual_analysis"
QUOTE_RE = re.compile(r"[“](.+?)[”]")
SCHEMA_VERSION = "1.0"


def quotes_in(par: str) -> list[str]:
    """一段里的所有引号内容，按出现顺序。"""
    return QUOTE_RE.findall(par)


def pick_text(paras: list[str], ref: dict, what: str) -> str:
    """
    按引用规格取原文。三种写法：
      {"para": N, "q": K}  取该段第 K 条引号内容
      {"para": N}          取整段
      {"para": N, "text":} 取指定子串，并校验它确实是该段的子串
    """
    i = ref.get("para")
    if not isinstance(i, int) or not 1 <= i <= len(paras):
        raise ValueError(f"{what}: para={i} 越界（本章 {len(paras)} 段）")
    par = paras[i - 1]
    if "q" in ref:
        qs = quotes_in(par)
        k = ref["q"]
        if not 0 <= k < len(qs):
            raise ValueError(f"{what}: 第{i}段只有 {len(qs)} 条引号，取不到 q={k}\n  段落：{par[:70]}")
        return qs[k]
    if "text" in ref:
        t = ref["text"]
        if t not in par:
            raise ValueError(f"{what}: 第{i}段里找不到该子串\n  子串：{t[:50]}\n  段落：{par[:70]}")
        return t
    return par


def build_analysis(src: dict, paras: list[str]) -> dict:
    """把手写稿里的引用规格展开成正式的分析结构。"""
    a = {k: src.get(k) for k in ("one_line", "synopsis", "time_setting", "emotional_tone")}

    a["characters"] = []
    for c in src.get("characters", []):
        c = dict(c)
        c["appearance_quotes"] = [
            pick_text(paras, r, f"characters[{c.get('name')}].appearance_quotes")
            for r in c.get("appearance_quotes", [])]
        a["characters"].append(c)

    a["scenes"] = []
    for s in src.get("scenes", []):
        s = dict(s)
        s["description_quotes"] = [
            pick_text(paras, r, f"scenes[{s.get('name')}].description_quotes")
            for r in s.get("description_quotes", [])]
        a["scenes"].append(s)

    a["dialogues"] = []
    for i, d in enumerate(src.get("dialogues", [])):
        d = dict(d)
        d["text"] = pick_text(paras, d, f"dialogues[{i}]")
        d.pop("q", None)
        a["dialogues"].append(d)

    for field in ("monologues", "narration"):
        a[field] = []
        for i, m in enumerate(src.get(field, [])):
            m = dict(m)
            m["text"] = pick_text(paras, m, f"{field}[{i}]")
            m.pop("q", None)
            a[field].append(m)

    for k in ("beats", "props", "terms", "continuity"):
        a[k] = src.get(k, [] if k != "continuity" else {})
    return a


def assemble(seq: int) -> dict:
    src = read_json(MANUAL_DIR / f"ch{seq:04d}.analysis.json")
    ch = get_chapter(seq)
    paras = ch["paragraphs"]
    analysis = build_analysis(src, paras)

    vb = verbatim.check_analysis(analysis, paras)
    cov = verbatim.coverage_report(analysis, paras)
    reasons = []
    if vb["counts"]["miss"] or vb["counts"]["near"]:
        reasons.append(f"逐字异常 miss={vb['counts']['miss']} near={vb['counts']['near']}")
    if cov["dialogue_coverage"] < 0.95:
        reasons.append(f"台词覆盖率 {cov['dialogue_coverage']:.1%} < 95%")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": SCHEMA_VERSION,
        "seq": seq, "chapter_id": ch["chapter_id"],
        "declared_num": ch["declared_num"], "title": ch["title"],
        "label": chapter_label(ch),
        "merged_titles": ch["merged_titles"], "is_merged": ch["is_merged"],
        "source": {"line_start": ch["line_start"], "line_end": ch["line_end"],
                   "char_count": ch["char_count"],
                   "paragraph_count": ch["paragraph_count"]},
        "raw_text": chapter_text(ch), "paragraphs": paras,
        "analysis": analysis,
        "quality": {
            "passed": not reasons, "fail_reasons": reasons,
            "structure_score": None, "fidelity_score": None,
            "verbatim": vb, "coverage": cov,
            "repair_rounds": 0, "context_chapters_used": [],
        },
        "reviews": [{
            "stage": "fidelity_review", "round": 0,
            "score": 100 if not reasons else 0,
            "verdict": "pass" if not reasons else "revise",
            "analysis": src.get("review_note", ""),
            "issues": [], "checked_items": {
                "无臆造": "通过（引用由程序从原文精确取，构造上不可能臆造）",
                "逐字准确": "通过" if not vb["counts"]["near"] else "不通过",
                "对话无遗漏": f"覆盖率 {cov['dialogue_coverage']:.1%}",
            },
            "missing_items": {}, "model": "人工", "reviewed_at": now,
        }],
        "run": {"pipeline_version": "manual-1.0", "model": "人工",
                "phase": "manual", "llm_calls": 0,
                "started_at": now, "finished_at": now, "elapsed_sec": 0},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="装配人工分析稿为标准章节 json")
    ap.add_argument("--check", action="store_true", help="只核验不落盘")
    args = ap.parse_args()

    files = sorted(MANUAL_DIR.glob("ch*.analysis.json"))
    if not files:
        raise SystemExit(f"{MANUAL_DIR} 下没有手写稿")
    paths.ensure_dirs()
    ok = True
    for f in files:
        seq = int(f.name[2:6])
        try:
            doc = assemble(seq)
        except ValueError as e:
            print(f"✗ seq{seq}  {e}")
            ok = False
            continue
        q, a = doc["quality"], doc["analysis"]
        print(f"{'✓' if q['passed'] else '✗'} seq{seq} {doc['title'][:16]:<18} "
              f"逐字 {q['verbatim']['verbatim_rate']:.0%}"
              f"({q['verbatim']['total_quotes']}条) "
              f"台词覆盖 {q['coverage']['dialogue_coverage']:.0%}"
              f"({len(a['dialogues'])}/{q['coverage']['raw_speech_count']}) "
              f"人物{len(a['characters'])} 场景{len(a['scenes'])} "
              f"独白{len(a['monologues'])} 旁白{len(a['narration'])} 节拍{len(a['beats'])}")
        if not q["passed"]:
            ok = False
            for r in q["fail_reasons"]:
                print(f"    → {r}")
            for p in q["coverage"]["missed_samples"][:5]:
                print(f"    漏台词：{p[:60]}")
        if not args.check:
            write_json(paths.chapter_json_path(seq), doc)
    if not args.check:
        print(f"\n已写出 {len(files)} 份到 {paths.CHAPTERS_DIR.relative_to(paths.ROOT)}/")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
