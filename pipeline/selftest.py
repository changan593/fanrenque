#!/usr/bin/env python3
"""
离线自测：不花一分钱 API 额度，验证整条管道能不能跑通。

用假的模型回复替换真实调用，检查三件事：
  1. novel.json 结构完整、正文能对上原文
  2. 逐字核验器能正确区分「逐字 / 改写 / 臆造」，能抓出段号标错
  3. 单章分析全流程（三次调用 + 质量闸门 + 修订轮）能产出合规的章节 json，
     并能通过 s3 的体检

跑通了再去配 DEEPSEEK_API_KEY 跑真的。

用法：python pipeline/selftest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import novel, paths, verbatim

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


# ------------------------------------------------------------------ 1. 原文
def test_novel() -> dict:
    print("\n[1] 标准化原文 novel.json")
    if not paths.NOVEL_JSON.exists():
        check("novel.json 存在", False, "请先跑 python pipeline/s1_normalize_novel.py")
        sys.exit(1)
    n = novel.load_novel()
    meta, chs = n["meta"], n["chapters"]
    check("章节数与 meta 一致", len(chs) == meta["unit_count"], f"{len(chs)} 章")
    check("seq 连续无缺", [c["seq"] for c in chs] == list(range(1, len(chs) + 1)))
    check("每章都有正文", all(c["paragraph_count"] > 0 for c in chs))
    check("字数统计对得上",
          all(c["char_count"] == sum(len(p) for p in c["paragraphs"]) for c in chs))

    # 正文必须能在原始 txt 里逐字找到
    raw = paths.RAW_TXT.read_bytes().decode("gb18030")
    ok = all(novel.get_chapter(s)["paragraphs"][0] in raw for s in (1, 500, 1000, len(chs)))
    check("正文与原始 txt 逐字一致（抽查4章）", ok)
    check("合并单元已标记", meta["merged_unit_count"] > 0,
          f"{meta['merged_unit_count']} 个")
    return n


# ------------------------------------------------------------------ 2. 核验器
def test_verbatim() -> None:
    print("\n[2] 逐字核验器")
    P = novel.get_chapter(1)["paragraphs"]
    line = "又来了！又来了！我早就说了嘛，白天睡觉容易做噩梦的啦！"
    para = next(i for i, p in enumerate(P, 1) if line in p)

    cases = [
        ("逐字命中", line, para, "exact"),
        ("标点全角半角差异", line.replace("！", "!").replace("，", ","), para, "exact"),
        ("段号标错仍能命中", line, 1, "exact"),
        ("改写一个字", line.replace("容易", "最容易"), para, "near"),
        ("凭空编造", "我要成为这世上最强的修士！", 20, "miss"),
        ("空串", "", para, "empty"),
    ]
    for name, q, p, want in cases:
        r = verbatim.check_quote(q, P, p)
        check(name, r["status"] == want, f"判定={r['status']} 相似度={r['similarity']:.2f}")

    r = verbatim.check_quote(line, P, 1)
    check("能识别段号标错", r["para_correct"] is False)

    rep = verbatim.check_analysis(
        {"dialogues": [{"para": para, "text": line},
                       {"para": 20, "text": "我要成为这世上最强的修士！"}]}, P)
    check("汇总命中率正确", abs(rep["verbatim_rate"] - 0.5) < 1e-6,
          f"{rep['verbatim_rate']:.0%}")
    cov = verbatim.coverage_report({"dialogues": [{"para": para, "text": line}]}, P)
    check("能测出漏台词", cov["missed_count"] > 0,
          f"原文{cov['raw_speech_count']}句，漏{cov['missed_count']}句")


# ------------------------------------------------------------------ 3. 全流程
def build_fake_analysis(ch: dict, flawed: bool) -> dict:
    """
    造一份假的抽取结果，模拟「一个合格的解析员」该交出什么。
    台词按原文所有引号内容逐字抄全，否则会被 s3 的台词覆盖率判为漏抽。
    flawed=True 时额外掺一条臆造台词，用来验证核验器能不能抓住。
    """
    import re
    P = ch["paragraphs"]
    dial = []
    for i, p in enumerate(P, 1):
        for s in re.findall(r"“([^”]{2,})”", p):
            dial.append({"para": i, "speaker": "唐真", "addressee": "众人",
                         "text": s, "manner": ""})
    if flawed:
        dial.append({"para": 2, "speaker": "唐真", "addressee": "天下人",
                     "text": "这天地终将由我改写！", "manner": "怒吼"})
    return {
        "one_line": "自测用假数据",
        "synopsis": "这是自测用的假简介，" * 8,
        "time_setting": "正午", "emotional_tone": "压抑",
        "characters": [{"name": "唐真", "aliases": ["三只眼"], "role_in_chapter": "主导",
                        "mentioned_only": False, "appearance_quotes": [],
                        "state": "修为尽失", "actions": ["从噩梦中惊醒"]}],
        "scenes": [{"name": "城隍庙", "type": "室内", "time_of_day": "正午",
                    "description_quotes": [], "present_characters": ["唐真"],
                    "events": ["唐真醒来"], "para_range": [1, len(P)]}],
        "dialogues": dial,
        "monologues": [], "narration": [],
        "beats": [{"order": 1, "para_range": [1, len(P)], "scene": "城隍庙",
                   "beat_type": "铺垫", "summary": "唐真惊醒"}],
        "props": [], "terms": [],
        "continuity": {"inherits": [], "foreshadows": [], "reveals": []},
    }


def test_pipeline() -> None:
    print("\n[3] 单章分析全流程（假模型，不调 API）")
    import s2_analyze_chapters as s2

    ch = novel.get_chapter(1)
    state = {"repaired": False}

    def fake_chat_json(system: str, user: str, temperature: float, max_tokens=None):
        meta = {"tokens": {"prompt_tokens": 100, "completion_tokens": 50}, "attempts": 1}
        if "原文解析员" in system:
            return build_fake_analysis(ch, flawed=True), meta
        if "修订员" in system:
            state["repaired"] = True
            return build_fake_analysis(ch, flawed=False), meta   # 修订后去掉臆造
        stage = "结构与上下文审查员" in system
        return {
            "score": 96 if stage else (55 if not state["repaired"] else 97),
            "verdict": "pass" if state["repaired"] or stage else "revise",
            "analysis": "自测用的假审查意见。" * 15,
            "issues": [], "checked_items": {"字段完整性": "通过"},
            "missing_items": {},
        }, meta

    orig = s2.chat_json
    s2.chat_json = fake_chat_json
    try:
        doc = s2.analyze_chapter(ch, phase="all")
    finally:
        s2.chat_json = orig

    q = doc["quality"]
    check("产出章节 json", doc["chapter_id"] == "ch0001")
    check("含用户要求的全部字段",
          all(k in doc for k in ("seq", "title", "raw_text", "analysis", "reviews"))
          and all(k in doc["analysis"] for k in
                  ("characters", "scenes", "synopsis", "monologues", "dialogues")))
    check("原文字段与 novel.json 一致", doc["raw_text"] == novel.chapter_text(ch))
    check("臆造被逐字核验抓到", state["repaired"], "触发了修订轮")
    check("修订后质量闸门放行", q["passed"], f"修订 {q['repair_rounds']} 轮")
    check("调用次数 ≥3", doc["run"]["llm_calls"] >= 3, f"{doc['run']['llm_calls']} 次")

    stages = [r["stage"] for r in doc["reviews"]]
    check("两类审查记录齐全",
          "structure_review" in stages and "fidelity_review" in stages, str(stages))
    check("每条审查都有分数和详细分析",
          all(r.get("score") is not None and len(r.get("analysis", "")) >= 100
              for r in doc["reviews"] if r["stage"].endswith("_review")))

    # 用 s3 的校验逻辑体检这份产物
    import s3_validate_chapters as s3
    res = s3.check_one(doc)
    check("能通过 s3 体检", not res["problems"], str(res["problems"][:3]))


def main() -> None:
    print(f"配置：模型 {config.MODEL} | 逐字门槛 {config.VERBATIM_PASS_RATE:.0%} | "
          f"合格线 结构{config.PASS_SCORE['structure_review']}/"
          f"一致{config.PASS_SCORE['fidelity_review']}")
    test_novel()
    test_verbatim()
    test_pipeline()
    print(f"\n{'=' * 50}")
    if FAIL:
        print(f"✗ {len(FAIL)} 项未通过：{FAIL}")
        sys.exit(1)
    print("✓ 全部通过。可以配 DEEPSEEK_API_KEY 跑真实分析了：")
    print("    export DEEPSEEK_API_KEY=sk-xxx")
    print("    python pipeline/s2_analyze_chapters.py --smoke 3")


if __name__ == "__main__":
    main()
