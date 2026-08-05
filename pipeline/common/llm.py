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


def chat_json(system: str, user: str, temperature: float,
              max_tokens: int | None = None) -> tuple[Any, dict]:
    """
    发一次对话并把回复解析成 JSON。

    返回 (解析后的对象, 元信息)。元信息含 tokens / 重试次数 / 原始回复，
    便于把审查过程完整留痕。
    """
    payload = {
        "model": config.MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens or config.MAX_OUTPUT_TOKENS,
        "stream": config.STREAM,
    }
    if config.JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    if config.STREAM:
        payload["stream_options"] = {"include_usage": True}   # 末帧带上 token 用量
    headers = {"Authorization": f"Bearer {config.api_key()}",
               "Content-Type": "application/json"}
    url = f"{config.API_BASE.rstrip('/')}/chat/completions"

    last_err = None
    for attempt in range(config.MAX_RETRIES + 1):
        if attempt:
            delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))
            wait = delay + random.uniform(0, delay * 0.25)     # 抖动，避免并发同步重试
            report(state=f"退避重试 第{attempt}次", detail=f"等{wait:.0f}s · 上次：{last_err}"[:70])
            time.sleep(wait)
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
            if not content.strip():
                # 空回复重试多少次都还是空，参数不改就没救。立刻带诊断信息失败，
                # 别把 5 次退避重试的十几分钟白白烧掉。
                _log_call(attempt + 1, status, time.time() - t0, 0, "空回复")
                raise LLMError(_empty_content_hint(info, usage,
                                                   payload["max_tokens"]))
            report(state="解析", detail=f"{len(content)}字")
            obj = extract_json_block(content)      # JSON 坏掉也当可重试错误
            USAGE.add(usage, retries=attempt)
            _log_call(attempt + 1, status, time.time() - t0, len(content), None)
            return obj, {"tokens": usage, "attempts": attempt + 1, "raw": content}
        except LLMError:
            raise                                   # 4xx 等不可重试错误直接抛
        except (requests.RequestException, ValueError, KeyError) as e:
            last_err = f"{type(e).__name__}: {e}"
            _log_call(attempt + 1, None, time.time() - t0, 0, last_err)
            continue

    USAGE.fail()
    raise LLMError(f"重试 {config.MAX_RETRIES} 次仍失败：{last_err}")


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
                # 分开显示：还在思考 vs 已经在写正式回复
                report(state="生成中" if done else "推理中", attempt=attempt,
                       chars=done or reasoning)
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
        fix = (f"\n  原因：{config.MODEL} 是推理模型，max_tokens 全被思维链吃光了，"
               f"没剩下额度写正式回复。\n"
               f"  处置（改 .env 后重跑）：\n"
               f"    1) 调大额度：LLM_MAX_TOKENS=32768\n"
               f"    2) 或换非推理模型：DEEPSEEK_MODEL=deepseek-chat")
    elif finish == "length":
        fix = (f"\n  原因：输出被 max_tokens={max_tokens} 截断。\n"
               f"  处置：.env 里调大 LLM_MAX_TOKENS")
    elif reasoning:
        fix = (f"\n  原因：只产出了思维链，没产出正式回复。\n"
               f"  处置：.env 里调大 LLM_MAX_TOKENS，或换 DEEPSEEK_MODEL=deepseek-chat")
    elif finish == "content_filter":
        fix = "\n  原因：被内容过滤拦截。\n  处置：检查该章原文是否触发了敏感词"
    else:
        fix = (f"\n  处置：先跑 python pipeline/s2_analyze_chapters.py --doctor 定位；"
               f"若模型不支持 JSON 模式，在 .env 里设 LLM_JSON_MODE=0")
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
