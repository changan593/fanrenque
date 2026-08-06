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
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

    # 行尾注释。.env.example 里写满了说明文字，直接 cp 成 .env 之后
    # 「600   # 单次请求总时限」会一路带到 int() 才炸。
    cv = config._clean_value
    check("剥掉行尾注释", cv("600    # 单次请求总时限") == "600", cv("600    # 说明"))
    check("引号内的 # 保留", cv('"a # b"  # 注释') == "a # b")
    check("紧挨着的 # 不当注释（密钥可能带 #）", cv("sk-ab#cd") == "sk-ab#cd")
    check("值本身为空时不报错", cv("   # 只有注释") == "")

    # 最要命的一条：.env.example 必须能原样当 .env 用。
    # 它是 README 让人 cp 的那个文件，它自己跑不通等于第一步就把人挡在门外。
    ex = (paths.ROOT / ".env.example").read_text(encoding="utf-8")
    bad = []
    for ln in ex.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), cv(v)
        if k in ("LLM_CONNECT_TIMEOUT", "LLM_STALL_TIMEOUT", "LLM_TIMEOUT",
                 "LLM_MAX_RETRIES", "LLM_MAX_TOKENS", "LLM_MAX_TOKENS_CAP",
                 "PIPELINE_WORKERS", "PIPELINE_REPAIR_ROUNDS", "IMAGE_WORKERS",
                 "IMAGE_BATCH", "IMAGE_TIMEOUT", "IMAGE_MAX_RETRIES"):
            if not v.isdigit():
                bad.append(f"{k}={v!r}")
        elif k in ("PIPELINE_QPS",):
            try:
                float(v)
            except ValueError:
                bad.append(f"{k}={v!r}")
        elif k in ("LLM_STREAM", "LLM_JSON_MODE", "LLM_THINKING"):
            if v.lower() not in ("0", "1", "true", "false", "yes", "no", "on", "off"):
                bad.append(f"{k}={v!r}")
    check(".env.example 能原样当 .env 用（每个值都解析得出来）",
          not bad, "；".join(bad))

    # 开关写错不会报错，只会静默按相反的方式跑——这种坑必须在读的时候就拦住
    for v, want in (("0", False), ("false", False), ("FALSE", False), ("no", False),
                    ("off", False), ("1", True), ("true", True), ("Yes", True)):
        os.environ["SELFTEST_SW"] = v
        check(f"开关认得 {v!r}", config._bool("SELFTEST_SW", "0") is want)
    os.environ["SELFTEST_SW"] = "开"
    try:
        config._bool("SELFTEST_SW", "0")
        check("开关值看不懂时报错而非猜", False)
    except SystemExit as e:
        check("开关值看不懂时报错而非猜", "只接受" in str(e))
    os.environ["SELFTEST_SW"] = "20    # 建连超时"
    try:
        config._num("SELFTEST_SW", "20")
        check("数值配置坏掉时给出可执行的处置", False)
    except SystemExit as e:
        check("数值配置坏掉时给出可执行的处置",
              "行尾注释" in str(e) and "正确写法" in str(e), str(e).splitlines()[0])
    os.environ.pop("SELFTEST_SW", None)

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


# ------------------------------------------------------------------ 7. 流式与容错
class _FakeAPI(BaseHTTPRequestHandler):
    """假的 DeepSeek 接口，用来验流式接收、错误处理、卡死检测。"""
    mode = "ok"
    hits = 0
    seen: list = []          # 收到的请求体，用来验参数是不是真发对了

    def log_message(self, *a):
        pass

    def do_POST(self):
        _FakeAPI.hits += 1
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        _FakeAPI.seen.append(body)
        mode = _FakeAPI.mode
        if mode == "bad_model":
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Model Not Exist"}}')
            return
        if mode == "flaky" and _FakeAPI.hits <= 2:      # 前两次 503，第三次成功
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"overloaded")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if mode == "stall":                             # 建连后就不再吐数据
            time.sleep(10)
            return
        if mode == "invisible":
            # content 只有一个 BOM。str.strip() 不认它，会绕过「空回复」判定
            frame = {"choices": [{"delta": {"content": "﻿"}, "finish_reason": "stop"}]}
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if mode == "truncate":
            # 额度不够就截断；调用方把 max_tokens 加上去之后才给完整 JSON
            enough = body.get("max_tokens", 0) > 1000
            text = ('{"ok": true, "echo": "fanrenque"}' if enough
                    else '{"ok": true, "echo": "fanren')
            frame = {"choices": [{"delta": {"content": text},
                                  "finish_reason": "stop" if enough else "length"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if mode == "reasoning_only":
            # 推理模型把 max_tokens 全烧在思维链上，正式回复一个字都没有
            for _ in range(6):
                frame = {"choices": [{"delta": {"reasoning_content": "思考" * 20}}]}
                self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                self.wfile.flush()
            end = {"choices": [{"delta": {}, "finish_reason": "length"}],
                   "usage": {"prompt_tokens": 900, "completion_tokens": 8192}}
            self.wfile.write(b"data: " + json.dumps(end).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        text = '{"ok": true, "echo": "fanrenque", "pad": "' + "内容" * 60 + '"}'
        for i in range(0, len(text), 6):
            frame = {"choices": [{"delta": {"content": text[i:i + 6]}}]}
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(0.02)
        tail = {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 47}}
        self.wfile.write(b"data: " + json.dumps(tail).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def test_streaming() -> None:
    print("\n[7] 流式接收与容错（本地假接口，不碰真 API）")
    import os

    from common import llm

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved = (config.API_BASE, config.MODEL, config.STALL_TIMEOUT,
             config.MAX_RETRIES, config.RETRY_BASE_DELAY,
             os.environ.get(config.API_KEY_ENV))
    config.API_BASE = f"http://127.0.0.1:{srv.server_address[1]}"
    os.environ[config.API_KEY_ENV] = "sk-selftest"
    try:
        check("默认启用流式", config.STREAM, "非流式时几分钟看不到任何反馈")

        ticks = []
        llm.set_reporter(lambda **kw: ticks.append(kw))
        _FakeAPI.mode, _FakeAPI.hits = "ok", 0
        obj, meta = llm.chat_json("测试", "返回 JSON", 0.0, 400)
        check("流式能拼回完整 JSON", obj.get("echo") == "fanrenque")
        check("末帧带回 token 用量", meta["tokens"].get("completion_tokens") == 47)
        gen = [t["chars"] for t in ticks if t.get("state") == "生成中"]
        check("生成过程持续上报已收字数", len(gen) >= 2 and gen[-1] > gen[0],
              f"{gen[:3]}…{gen[-1] if gen else 0}")
        # 没有思维链时绝不能报「推理中」。早先把「正文还没来」也标成「推理中」，
        # 短回复的第一帧必然触发，--doctor 据此判定「思考模式开着」，成了稳定的假警报。
        check("没有思维链时不谎报「推理中」",
              not [t for t in ticks if t.get("state") == "推理中"],
              str([t.get("state") for t in ticks][:4]))

        _FakeAPI.mode, _FakeAPI.hits = "bad_model", 0
        config.MODEL = "不存在的模型"
        try:
            llm.chat_json("测试", "x", 0.0, 50)
            check("模型名错时立即报错", False)
        except llm.LLMError as e:
            check("模型名错时立即报错不空转", "模型名" in str(e), str(e)[:60])
        check("4xx 不做无谓重试", _FakeAPI.hits == 1, f"实际请求 {_FakeAPI.hits} 次")

        config.MODEL, config.RETRY_BASE_DELAY = saved[1], 0.05
        _FakeAPI.mode, _FakeAPI.hits = "flaky", 0
        obj, meta = llm.chat_json("测试", "返回 JSON", 0.0, 400)
        check("5xx 会退避重试直到成功", meta["attempts"] == 3, f"第 {meta['attempts']} 次成功")

        # 思考模式必须显式声明。官方默认是「开、effort=high」，
        # 而抽取任务开着它会把 max_tokens 烧在思维链上导致 content 为空。
        _FakeAPI.mode, _FakeAPI.hits, _FakeAPI.seen = "ok", 0, []
        llm.chat_json("测试", "返回 JSON", 0.3, 400)
        sent = _FakeAPI.seen[0]
        check("显式关闭思考模式而非依赖服务端默认值",
              sent.get("thinking") == {"type": "disabled"}, str(sent.get("thinking")))
        check("关思考时才发 temperature", sent.get("temperature") == 0.3)
        check("不发 reasoning_effort", "reasoning_effort" not in sent)

        saved_think = config.THINKING
        config.THINKING = True
        _FakeAPI.seen = []
        llm.chat_json("测试", "返回 JSON", 0.3, 400)
        sent = _FakeAPI.seen[0]
        check("打开思考模式时如实发 enabled + effort",
              sent.get("thinking") == {"type": "enabled"}
              and sent.get("reasoning_effort") == config.REASONING_EFFORT)
        check("思考模式下不发 temperature（官方：不生效）", "temperature" not in sent)
        config.THINKING = saved_think

        # 截断：原样重试只会截在同一个地方，必须把额度加上去
        _FakeAPI.mode, _FakeAPI.hits, _FakeAPI.seen = "truncate", 0, []
        obj, meta = llm.chat_json("测试", "返回 JSON", 0.0, 800)
        check("length 截断会自动上调 max_tokens 重试", obj.get("echo") == "fanrenque")
        check("上调后的额度确实发出去了",
              [s["max_tokens"] for s in _FakeAPI.seen] == [800, 1200],
              str([s["max_tokens"] for s in _FakeAPI.seen]))
        check("自适应过程有留痕", any("截断" in a for a in meta["adapted"]),
              str(meta["adapted"]))

        # 不可见字符：BOM 能骗过 strip()，必须当成空回复处理
        _FakeAPI.mode, _FakeAPI.hits, _FakeAPI.seen = "invisible", 0, []
        try:
            llm.chat_json("测试", "返回 JSON", 0.0, 400)
            check("零宽字符按空回复处理", False)
        except llm.LLMError as e:
            check("零宽字符按空回复处理", "空回复" in str(e), str(e)[:60])

        # 空回复：官方已知问题。参数不变的重试毫无意义，必须逐级降级后带诊断失败
        _FakeAPI.mode, _FakeAPI.hits, _FakeAPI.seen = "reasoning_only", 0, []
        ticks.clear()
        t0 = time.time()
        try:
            llm.chat_json("测试", "返回 JSON", 0.0, 65536)
            check("空回复能被识别", False)
        except llm.LLMError as e:
            msg = str(e)
            check("空回复能被识别并说清原因", "空回复" in msg and "思维链" in msg)
            check("诊断里带上证据", "finish_reason=length" in msg and "思维链" in msg,
                  msg.splitlines()[0][:70])
            check("给出可直接执行的处置", "LLM_THINKING" in msg)
            modes = [(s.get("response_format") is not None,
                      s["thinking"]["type"]) for s in _FakeAPI.seen]
            check("空回复走逐级降级而非原样空转",
                  modes == [(True, "disabled"), (False, "disabled")], str(modes))
            check("降级到底就立刻失败，不烧满 5 次重试",
                  _FakeAPI.hits <= 3 and time.time() - t0 < 8,
                  f"请求 {_FakeAPI.hits} 次，{time.time() - t0:.1f}s 内返回")
        check("思维链阶段单独上报为「推理中」",
              any(t.get("state") == "推理中" for t in ticks))

        config.STALL_TIMEOUT, config.MAX_RETRIES = 1, 0
        _FakeAPI.mode, _FakeAPI.hits = "stall", 0
        t0 = time.time()
        try:
            llm.chat_json("测试", "x", 0.0, 50)
            check("卡死能被判定并中止", False)
        except llm.LLMError:
            dt = time.time() - t0
            check("卡死能被判定并中止", dt < 6, f"{dt:.1f}s 后中止，未无限等待")
    finally:
        (config.API_BASE, config.MODEL, config.STALL_TIMEOUT,
         config.MAX_RETRIES, config.RETRY_BASE_DELAY, oldkey) = saved
        if oldkey is None:
            os.environ.pop(config.API_KEY_ENV, None)
        else:
            os.environ[config.API_KEY_ENV] = oldkey
        llm.set_reporter(None)
        srv.shutdown()


# ------------------------------------------------------------------ 8. 资产聚合
def _fake_chapter(seq: int, chars: list[dict], scenes: list[dict]) -> dict:
    return {"seq": seq, "chapter_id": f"ch{seq:04d}", "declared_num": seq,
            "title": f"测试第{seq}章", "analysis": {
                "synopsis": "测试", "characters": chars, "scenes": scenes,
                "dialogues": [], "monologues": [], "narration": [], "beats": []}}


def test_asset_build() -> None:
    print("\n[8] 角色/场景资产聚合")
    import tempfile

    from common.jsonio import read_json, write_json
    import s4_build_assets as s4

    C = [
        # 同一人两种叫法，应归并成「唐真」（出场章数多的那个当主名）
        _fake_chapter(1, [{"name": "唐真", "aliases": ["三只眼"], "role_in_chapter": "主导",
                           "appearance_quotes": ["额头正中有块黑色椭圆形的印记"],
                           "state": "修为尽失", "actions": ["惊醒"]},
                          {"name": "老拐子", "aliases": [], "role_in_chapter": "参与",
                           "appearance_quotes": ["老脸上的皱纹挤在一起像是旧抹布"]}],
                      [{"name": "城隍庙", "type": "室内", "time_of_day": "正午",
                        "description_quotes": ["从屋顶的破洞照进这间小庙"],
                        "present_characters": ["唐真", "老拐子"], "events": ["醒来"]}]),
        _fake_chapter(2, [{"name": "三只眼", "aliases": ["唐真"], "role_in_chapter": "主导",
                           "appearance_quotes": ["一身破烂却总是什么都无所谓的样子"]},
                          {"name": "老拐子", "aliases": [], "role_in_chapter": "参与"}],
                      [{"name": "城隍庙", "type": "室内", "description_quotes": [],
                        "present_characters": ["三只眼"]}]),
        # 泛称「老人」不该把两个不同角色并到一起
        _fake_chapter(3, [{"name": "唐真", "aliases": ["老人"], "role_in_chapter": "主导"},
                          {"name": "南季礼", "aliases": ["老人"], "role_in_chapter": "参与"}],
                      [{"name": "紫云仙宫", "type": "室外", "present_characters": ["南季礼"]}]),
        # 脏数据：字段类型不对 / 缺字段，不该把整次聚合搞崩
        _fake_chapter(4, [{"name": "唐真", "aliases": "不是数组", "role_in_chapter": "主导"},
                          {"aliases": ["无名"]}, "根本不是对象"],
                      [{"type": "室内"}, {"name": "破庙", "present_characters": None}]),
    ]

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        saved = (paths.CHAPTERS_DIR, paths.CHARACTERS_DIR, paths.SCENES_DIR, paths.PLOT_DIR)
        paths.CHAPTERS_DIR = tmp / "chapters"
        paths.CHARACTERS_DIR = tmp / "characters"
        paths.SCENES_DIR = tmp / "scenes"
        paths.PLOT_DIR = tmp / "plot"
        for p in (paths.CHAPTERS_DIR, paths.CHARACTERS_DIR, paths.SCENES_DIR, paths.PLOT_DIR):
            p.mkdir(parents=True)
        try:
            for c in C:
                write_json(paths.CHAPTERS_DIR / f"{c['chapter_id']}.json", c)
            (paths.CHAPTERS_DIR / "ch0009.json").write_text("{坏", encoding="utf-8")

            docs, problems = s4.load_chapters()
            check("跳过读不了的章节且留痕", len(docs) == 4 and len(problems) == 1)

            amap, susp = s4.build_alias_map(docs, merge=True)
            check("别名归并到出场最多的主名",
                  amap.get("三只眼") == "唐真" and amap.get("唐真") == "唐真",
                  f"三只眼 -> {amap.get('三只眼')}")
            check("泛称不参与归并（唐真与南季礼没被并成一个）",
                  amap.get("南季礼") == "南季礼")
            check("泛称本身不进映射", "老人" not in amap)

            chars = s4.build_characters(docs, amap)
            by = {c["canonical_name"]: c for c in chars}
            check("角色去重正确", set(by) == {"唐真", "老拐子", "南季礼"}, str(sorted(by)))
            t = by["唐真"]
            check("合并了两种叫法的出场章节", t["chapter_count"] == 4, str(t["chapters"]))
            check("别名收全", "三只眼" in t["aliases"])
            check("原文描写逐字带章节号收集",
                  len(t["appearance_quotes"]) == 2
                  and t["appearance_quotes"][0]["seq"] == 1
                  and "黑色椭圆形" in t["appearance_quotes"][0]["quote"])
            check("状态变化留档", t["states"] and t["states"][0]["state"] == "修为尽失")
            check("戏份分布统计", t["role_distribution"].get("主导") == 4)

            scenes = s4.build_scenes(docs, amap)
            sby = {s["name"]: s for s in scenes}
            check("场景聚合并按别名归一人物",
                  sby["城隍庙"]["chapter_count"] == 2
                  and sby["城隍庙"]["characters"].get("唐真") == 2,
                  str(sby["城隍庙"]["characters"]))
            check("场景原文描写逐字收集",
                  "破洞" in sby["城隍庙"]["description_quotes"][0]["quote"])
            check("脏场景数据被跳过而非崩溃", "破庙" in sby and len(scenes) == 3)

            cooc = s4.build_cooccurrence(docs, amap)
            pairs = {(p["a"], p["b"]): p["chapters"] for p in cooc["pairs"]}
            check("同框统计正确", pairs.get(("唐真", "老拐子")) == 2, str(pairs))

            # 制造一次真会误并的情况，确认能被标出来
            bad = [_fake_chapter(1, [{"name": "甲角色", "aliases": ["乙角色"]}], [])
                   for _ in range(1)]
            bad += [_fake_chapter(i, [{"name": "甲角色"}, {"name": "乙角色"}], [])
                    for i in range(2, 14)]
            _, susp2 = s4.build_alias_map(bad, merge=True)
            check("可疑归并会被标记出来交人工复核", len(susp2) == 1, str(susp2[:1]))
            check("--no-merge 时完全不归并",
                  s4.build_alias_map(docs, merge=False)[0].get("三只眼") == "三只眼")
        finally:
            (paths.CHAPTERS_DIR, paths.CHARACTERS_DIR,
             paths.SCENES_DIR, paths.PLOT_DIR) = saved


def main() -> None:
    print(config.describe())
    test_novel()
    test_verbatim()
    test_pipeline()
    test_dotenv()
    test_resume_and_concurrency()
    test_progress_render()
    test_streaming()
    test_asset_build()
    print(f"\n{'=' * 50}")
    if FAIL:
        print(f"✗ {len(FAIL)} 项未通过：{FAIL}")
        sys.exit(1)
    print("✓ 全部通过。配好密钥就能跑真实分析了：")
    print("    cp .env.example .env      # 然后填入 DEEPSEEK_API_KEY")
    print("    python pipeline/s2_analyze_chapters.py --smoke 3")


if __name__ == "__main__":
    main()
