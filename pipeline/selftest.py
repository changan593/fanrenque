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


# ------------------------------------------------------------------ 4. .env
def test_dotenv() -> None:
    print("\n[4] .env 密钥管理")
    import os
    import tempfile

    check(".env.example 已提供", (paths.ROOT / ".env.example").exists())
    gi = (paths.ROOT / ".gitignore").read_text(encoding="utf-8")
    check(".env 已被 gitignore", ".env" in gi.split())

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / ".env"
        f.write_text("# 注释\nSELFTEST_A=hello\nexport SELFTEST_B=\"quoted value\"\n"
                     "SELFTEST_C=from_dotenv\n坏行没有等号\n", encoding="utf-8")
        os.environ["SELFTEST_C"] = "from_real_env"     # 真实环境变量应当优先
        config._load_dotenv(f)
        check("能读取 KEY=VALUE", os.environ.get("SELFTEST_A") == "hello")
        check("支持 export 与引号", os.environ.get("SELFTEST_B") == "quoted value")
        check("真实环境变量优先于 .env",
              os.environ.get("SELFTEST_C") == "from_real_env")
        for k in ("SELFTEST_A", "SELFTEST_B", "SELFTEST_C"):
            os.environ.pop(k, None)

    check("缺密钥时报错指向 .env", _missing_key_message_mentions_env())


def _missing_key_message_mentions_env() -> bool:
    import os
    saved = os.environ.pop(config.API_KEY_ENV, None)
    try:
        config.api_key()
        return False
    except SystemExit as e:
        return ".env" in str(e)
    finally:
        if saved is not None:
            os.environ[config.API_KEY_ENV] = saved


# ------------------------------------------------------------------ 5. 续传+并发
def test_resume_and_concurrency() -> None:
    print("\n[5] 并发跑批 + 断点续传")
    import os
    import tempfile

    import s2_analyze_chapters as s2

    ch1 = novel.get_chapter(1)

    def fake_chat_json(system, user, temperature, max_tokens=None):
        meta = {"tokens": {"prompt_tokens": 10, "completion_tokens": 5}, "attempts": 1}
        if "原文解析员" in system:
            return build_fake_analysis(ch1, flawed=False), meta
        return {"score": 97, "verdict": "pass", "analysis": "自测假审查。" * 20,
                "issues": [], "checked_items": {}, "missing_items": {}}, meta

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_dir, orig_chat = paths.CHAPTERS_DIR, s2.chat_json
        paths.CHAPTERS_DIR = tmp
        s2.chat_json = fake_chat_json
        try:
            chapters = [novel.get_chapter(i) for i in range(1, 7)]

            # 首轮：全新，6 章都要跑
            todo, stats = s2.select_todo(chapters, "all", False, False)
            check("首轮识别出全部未跑", len(todo) == 6 and stats["missing"] == 6)

            from concurrent.futures import ThreadPoolExecutor
            from contextlib import redirect_stdout

            from common.progress import Progress
            prog = Progress(total=len(todo), workers=3, force_plain=True)
            with open(os.devnull, "w") as devnull, redirect_stdout(devnull):
                with ThreadPoolExecutor(max_workers=3) as pool:
                    list(pool.map(lambda c: s2.worker(c, "all", prog), chapters))

            check("并发跑完 6 章", prog.done == 6 and prog.failed == 0,
                  f"成功 {prog.ok}")
            check("6 个章节 json 已落盘", len(list(tmp.glob("ch*.json"))) == 6)
            check("章内步骤已走完（无残留活动槽）", not prog.active)

            # 二轮：断点续传，应当全部跳过
            todo2, stats2 = s2.select_todo(chapters, "all", False, False)
            check("续传时全部跳过", todo2 == [] and stats2["done"] == 6)

            # 模拟被打断的残缺文件 + 未达标文件
            from common.jsonio import read_json, write_json
            p3 = tmp / "ch0003.json"
            bad = read_json(p3)
            bad["run"].pop("finished_at")
            write_json(p3, bad)
            check("能识别残缺文件", s2.chapter_state(3) == "incomplete")

            p4 = tmp / "ch0004.json"
            d4 = read_json(p4)
            d4["quality"]["passed"] = False
            write_json(p4, d4)
            check("能识别未达标文件", s2.chapter_state(4) == "failed")

            (tmp / "ch0005.json").write_text("{ 坏掉的 json", encoding="utf-8")
            check("能识别写坏的文件", s2.chapter_state(5) == "incomplete")

            todo3, _ = s2.select_todo(chapters, "all", False, False)
            check("续传自动重跑残缺章，不碰未达标章",
                  sorted(c["seq"] for c in todo3) == [3, 5])
            todo4, _ = s2.select_todo(chapters, "all", False, True)
            check("--redo-failed 会带上未达标章",
                  sorted(c["seq"] for c in todo4) == [3, 4, 5])
            todo5, _ = s2.select_todo(chapters, "all", True, False)
            check("--force 全部重跑", len(todo5) == 6)
        finally:
            paths.CHAPTERS_DIR = orig_dir
            s2.chat_json = orig_chat


# ------------------------------------------------------------------ 6. 进度显示
def test_progress_render() -> None:
    print("\n[6] 进度看板")
    from common.progress import Progress, fmt_dur

    check("时长格式化", fmt_dur(65) == "01:05" and fmt_dur(3725) == "1:02:05")
    p = Progress(total=10, workers=2, done_already=90, force_plain=True)
    p.done, p.ok, p.failed = 5, 4, 1
    head = p._headline(plain=True)
    check("总进度含已完成与总数", "95/100" in head, head.strip())
    check("总进度含成功/失败计数", "✓4" in head and "✗1" in head)
    p.begin(7, "第7章 测试", 4)
    p.stage("抽取")
    p.stage("逐字核验")
    row = p._rows()[0]
    check("章内进度含步骤与环节", "[2/4]" in row and "逐字核验" in row, row.strip())
    p.stage("修订 第1轮", 6)
    check("修订轮会上调章内总步数", "[3/6]" in p._rows()[0])
    check("非 TTY 时自动退化为逐行输出", not p.tty)


def main() -> None:
    print(config.describe())
    test_novel()
    test_verbatim()
    test_pipeline()
    test_dotenv()
    test_resume_and_concurrency()
    test_progress_render()
    print(f"\n{'=' * 50}")
    if FAIL:
        print(f"✗ {len(FAIL)} 项未通过：{FAIL}")
        sys.exit(1)
    print("✓ 全部通过。配好密钥就能跑真实分析了：")
    print("    cp .env.example .env      # 然后填入 DEEPSEEK_API_KEY")
    print("    python pipeline/s2_analyze_chapters.py --smoke 3")


if __name__ == "__main__":
    main()
