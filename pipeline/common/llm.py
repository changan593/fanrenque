"""
DeepSeek API 客户端（OpenAI 兼容接口）。

只做四件事：强制 JSON 输出、失败退避重试、限速、用量统计。
业务逻辑一概不放这里。
"""
import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

import config
from .jsonio import append_jsonl, extract_json_block

_RETRIABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

# ---------------------------------------------------------------- 实时上报
# 请求进行中的状态（正在生成 / 已收多少字 / 第几次重试）要能透到进度看板上，
# 否则跑批时只能看着一个不动的「抽取」干等，无法判断是活着还是卡死。
# 用线程局部变量传，免得给调用链上每个函数都加一个回调参数。
_local = threading.local()


def set_reporter(fn) -> None:
    """由 worker 线程注册；fn(**kw) 接收 state/attempt/chars/detail。"""
    _local.fn = fn


def report(**kw) -> None:
    fn = getattr(_local, "fn", None)
    if fn:
        try:
            fn(**kw)
        except Exception:
            pass          # 上报只是辅助，绝不能因为它把正事搞挂


def set_call_context(**kw) -> None:
    """记到调用日志里的上下文，比如当前章节 seq 和环节名。"""
    _local.ctx = kw


def _log_call(attempt: int, status: int | None, elapsed: float,
              chars: int, error: str | None) -> None:
    from . import paths
    try:
        append_jsonl(paths.LOG_DIR / "s2_calls.jsonl", {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **getattr(_local, "ctx", {}),
            "attempt": attempt, "http": status,
            "elapsed": round(elapsed, 1), "chars": chars, "error": error,
        })
    except Exception:
        pass


@dataclass
class Usage:
    """全局用量统计，跑完一批打印出来心里有数。"""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0
    failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, usage: dict, retries: int = 0) -> None:
        with self._lock:
            self.calls += 1
            self.retries += retries
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)

    def fail(self) -> None:
        with self._lock:
            self.failures += 1

    def summary(self) -> str:
        return (f"调用 {self.calls} 次 | 重试 {self.retries} | 失败 {self.failures} | "
                f"输入 {self.prompt_tokens:,} tok | 输出 {self.completion_tokens:,} tok")


USAGE = Usage()


class _RateLimiter:
    """简单全局限速；QPS_LIMIT=0 时直接放行。"""

    def __init__(self, qps: float):
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval


_limiter = _RateLimiter(config.QPS_LIMIT)
_session = threading.local()


def _sess() -> requests.Session:
    if not hasattr(_session, "s"):
        _session.s = requests.Session()
    return _session.s


class LLMError(RuntimeError):
    pass


# 零宽字符与 BOM：Python 的 str.strip() 不认它们，`"﻿".strip()` 仍然为真。
# 模型偶尔只吐这么一个字符，于是「空回复」判定被绕过，一路跌到 JSON 解析
# 才报错，且错误信息里打印出来是一片空白，完全没法定位。
_INVISIBLE = "﻿​‌‍⁠ 　"


def _visible(s: str) -> str:
    return s.strip().strip(_INVISIBLE).strip()


def _build_payload(system: str, user: str, temperature: float, max_tokens: int,
                   json_mode: bool, thinking: bool) -> dict:
    p: dict[str, Any] = {
        "model": config.MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "stream": config.STREAM,
        # 官方文档：思考模式默认打开、effort 默认 high。默认值对抽取任务是灾难，
        # 所以这里**永远显式声明**，不依赖服务端默认值。
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if thinking:
        p["reasoning_effort"] = config.REASONING_EFFORT
    else:
        # 文档：思考模式不支持 temperature（不报错但也不生效）。
        # 只在关掉思考时发它，避免看着像设了实际没用。
        p["temperature"] = temperature
    if json_mode:
        p["response_format"] = {"type": "json_object"}
    if config.STREAM:
        p["stream_options"] = {"include_usage": True}   # 末帧带上 token 用量
    return p


def chat_json(system: str, user: str, temperature: float,
              max_tokens: int | None = None) -> tuple[Any, dict]:
    """
    发一次对话并把回复解析成 JSON。

    返回 (解析后的对象, 元信息)。元信息含 tokens / 重试次数 / 原始回复，
    便于把审查过程完整留痕。

    重试是**自适应**的，不是把同一个请求原样再发一遍：

      空回复      → 先摘掉 JSON 模式（官方已知问题），再关掉思考模式
      length 截断 → 把 max_tokens 上调 1.5 倍再发
      5xx / 网络  → 原样退避重试

    这条很重要。上一版对空回复原样重试 5 次，每次都空，一章白烧十几分钟——
    参数不变的重试对确定性错误没有任何意义。
    """
    headers = {"Authorization": f"Bearer {config.api_key()}",
               "Content-Type": "application/json"}
    url = f"{config.API_BASE.rstrip('/')}/chat/completions"

    json_mode, thinking = config.JSON_MODE, config.THINKING
    max_tok = max_tokens or config.MAX_OUTPUT_TOKENS
    adapted: list[str] = []          # 记下自适应过程，留痕到元信息里
    last_err = None

    for attempt in range(config.MAX_RETRIES + 1):
        if attempt:
            delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))
            wait = delay + random.uniform(0, delay * 0.25)     # 抖动，避免并发同步重试
            report(state=f"退避重试 第{attempt}次", detail=f"等{wait:.0f}s · 上次：{last_err}"[:70])
            time.sleep(wait)
        payload = _build_payload(system, user, temperature, max_tok, json_mode, thinking)
        _limiter.wait()
        t0 = time.time()
        try:
            report(state="请求中", attempt=attempt + 1, chars=0)
            content, usage, status, info = (
                _do_stream if config.STREAM else _do_blocking)(
                url, payload, headers, attempt + 1)
            if status in _RETRIABLE_STATUS:
                last_err = f"HTTP {status}"
                _log_call(attempt + 1, status, time.time() - t0, 0, last_err)
                continue

            clean = _visible(content)
            if not clean:
                _log_call(attempt + 1, status, time.time() - t0, 0, "空回复")
                # 换个参数还有救，就换；无参数可换了再带诊断失败
                if json_mode:
                    json_mode = False
                    adapted.append("空回复→关JSON模式")
                    last_err = "空回复（官方已知：JSON 模式有概率返回空 content），改用提示词约束"
                    continue
                if thinking:
                    thinking = False
                    adapted.append("空回复→关思考模式")
                    last_err = "空回复，改为关闭思考模式"
                    continue
                raise LLMError(_empty_content_hint(info, usage, max_tok))

            if info.get("finish_reason") == "length":
                # 截断的 JSON 必然解析失败。原样重试只会截断在同一个位置，
                # 必须先把额度加上去。
                grown = min(int(max_tok * 1.5), config.MAX_OUTPUT_CAP)
                _log_call(attempt + 1, status, time.time() - t0, len(clean), "length截断")
                if grown > max_tok:
                    adapted.append(f"截断→额度{max_tok}→{grown}")
                    last_err = f"输出被 max_tokens={max_tok} 截断，上调到 {grown}"
                    max_tok = grown
                    continue
                raise LLMError(
                    f"输出被截断，且 max_tokens 已到封顶 {config.MAX_OUTPUT_CAP}。"
                    f"已收 {len(clean)} 字。\n"
                    f"  处置：拆小单次任务，或在 .env 调高 LLM_MAX_TOKENS_CAP")

            report(state="解析", detail=f"{len(clean)}字")
            obj = extract_json_block(clean)        # JSON 坏掉也当可重试错误
            USAGE.add(usage, retries=attempt)
            _log_call(attempt + 1, status, time.time() - t0, len(clean), None)
            return obj, {"tokens": usage, "attempts": attempt + 1, "raw": clean,
                         "adapted": adapted, "thinking": thinking, "json_mode": json_mode}
        except LLMError:
            raise                                   # 4xx 等不可重试错误直接抛
        except (requests.RequestException, ValueError, KeyError) as e:
            last_err = f"{type(e).__name__}: {e}"
            _log_call(attempt + 1, None, time.time() - t0, 0, last_err)
            # JSON 模式下还解析失败，说明这个模式在帮倒忙，摘掉它再来
            if isinstance(e, ValueError) and json_mode:
                json_mode = False
                adapted.append("解析失败→关JSON模式")
            continue

    USAGE.fail()
    tail = f"（已尝试自适应：{' / '.join(adapted)}）" if adapted else ""
    raise LLMError(f"重试 {config.MAX_RETRIES} 次仍失败：{last_err}{tail}")


def _do_blocking(url, payload, headers, attempt) -> tuple[str, dict, int, dict]:
    r = _sess().post(url, json=payload, headers=headers,
                     timeout=(config.CONNECT_TIMEOUT, config.REQUEST_TIMEOUT))
    if r.status_code in _RETRIABLE_STATUS:
        return "", {}, r.status_code, {}
    _raise_for_bad_status(r.status_code, r.text)
    body = r.json()
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    info = {"finish_reason": choice.get("finish_reason"),
            "reasoning_chars": len(msg.get("reasoning_content") or "")}
    return msg.get("content") or "", body.get("usage") or {}, 200, info


def _do_stream(url, payload, headers, attempt) -> tuple[str, dict, int, dict]:
    """
    流式接收。read timeout 作用在每次读取上，等于「多久没收到新数据就判定卡死」，
    比总超时靠谱得多：正常生成 5 分钟不该被砍，真卡住 90 秒就该重试。

    推理模型会先吐 reasoning_content（思维链）再吐 content（正式回复），
    两者分开累计——只有分开看，才能发现「token 全被思维链吃光、正式回复为空」。
    """
    deadline = time.time() + config.REQUEST_TIMEOUT
    parts: list[str] = []
    reasoning = 0
    finish_reason = None
    usage: dict = {}
    last_report = 0.0
    with _sess().post(url, json=payload, headers=headers, stream=True,
                      timeout=(config.CONNECT_TIMEOUT, config.STALL_TIMEOUT)) as r:
        if r.status_code in _RETRIABLE_STATUS:
            return "", {}, r.status_code, {}
        # 只有出错时才碰 r.text —— 读它会把整个响应体一次性读完，
        # 流式就彻底失效了（帧全堆到最后才到，判活信号形同虚设）。
        if r.status_code != 200:
            _raise_for_bad_status(r.status_code, r.text)
        # chunk_size 必须显式给 1：默认的 512 会攒够一块才吐，chunk_size=None
        # 更糟——实测所有帧都堆到最后才到，等于白开了流式。判活信号要求实时。
        for raw in r.iter_lines(chunk_size=1, decode_unicode=False):
            if time.time() > deadline:
                raise requests.exceptions.Timeout(
                    f"超过单次请求总时限 {config.REQUEST_TIMEOUT}s")
            if not raw or not raw.startswith(b"data:"):
                continue
            data = raw[5:].strip()
            if data == b"[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning += len(delta["reasoning_content"])
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]
            if chunk.get("usage"):
                usage = chunk["usage"]
            now = time.time()
            if now - last_report > 0.3:            # 限流上报，别把看板刷爆
                last_report = now
                done = sum(map(len, parts))
                # 三种状态要分清。早先把「还没收到正文」一律标成「推理中」，
                # 于是短回复的第一帧必然先报一次「推理中」——哪怕思维链一个字都没有。
                # --doctor 据此判定「思考模式开着」，就成了稳定的假警报。
                # 「在思考」的唯一证据是收到过 reasoning_content，不是「正文还没来」。
                state = "生成中" if done else ("推理中" if reasoning else "等待首字")
                report(state=state, attempt=attempt, chars=done or reasoning)
    return ("".join(parts), usage, 200,
            {"finish_reason": finish_reason, "reasoning_chars": reasoning})


def _empty_content_hint(info: dict, usage: dict, max_tokens: int) -> str:
    """
    模型返回了空的 content。原样报「找不到 JSON」等于什么都没说，
    这里把能拿到的证据全摆出来，并给出可直接执行的处置。
    """
    reasoning = info.get("reasoning_chars") or 0
    finish = info.get("finish_reason")
    out_tok = (usage or {}).get("completion_tokens")
    facts = (f"模型返回空回复（content 为空）。"
             f"finish_reason={finish}，思维链 {reasoning} 字，"
             f"输出 {out_tok} tok，max_tokens={max_tokens}")

    if reasoning and finish == "length":
        fix = (f"\n  原因：思维链把 max_tokens 吃光了，没剩下额度写正式回复。\n"
               f"  处置（改 .env 后重跑）：\n"
               f"    1) 关掉思考模式：LLM_THINKING=0（推荐，抽取任务不需要思维链）\n"
               f"    2) 或调大额度：LLM_MAX_TOKENS=32768")
    elif finish == "length":
        fix = (f"\n  原因：输出被 max_tokens={max_tokens} 截断。\n"
               f"  处置：.env 里调大 LLM_MAX_TOKENS")
    elif reasoning:
        fix = (f"\n  原因：只产出了思维链，没产出正式回复。\n"
               f"  处置：.env 里设 LLM_THINKING=0")
    elif finish == "content_filter":
        fix = "\n  原因：被内容过滤拦截。\n  处置：检查该章原文是否触发了敏感词"
    else:
        fix = (f"\n  原因：官方文档已载明「使用 JSON Output 功能时，API 有概率返回空的"
               f" content」。本次已自动降级重试（关 JSON 模式 → 关思考模式）仍为空。\n"
               f"  处置：先跑 python pipeline/s2_analyze_chapters.py --doctor 定位")
    return facts + fix


def _raise_for_bad_status(status: int, text: str) -> None:
    if status == 200:
        return
    hint = ""
    if status in (401, 403):
        hint = "  ← 密钥无效或没权限，检查 .env 里的 DEEPSEEK_API_KEY"
    elif status in (400, 404, 422):
        hint = f"  ← 请求被拒，常见原因是模型名不对（当前 {config.MODEL}）"
    raise LLMError(f"HTTP {status}: {text[:400]}{hint}")
