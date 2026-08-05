"""
DeepSeek API 客户端（OpenAI 兼容接口）。

只做四件事：强制 JSON 输出、失败退避重试、限速、用量统计。
业务逻辑一概不放这里。
"""
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

import config
from .jsonio import extract_json_block

_RETRIABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


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
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {config.api_key()}",
               "Content-Type": "application/json"}
    url = f"{config.API_BASE.rstrip('/')}/chat/completions"

    last_err = None
    for attempt in range(config.MAX_RETRIES + 1):
        if attempt:
            delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))
            time.sleep(delay + random.uniform(0, delay * 0.25))  # 抖动，避免并发同步重试
        _limiter.wait()
        try:
            r = _sess().post(url, json=payload, headers=headers,
                             timeout=config.REQUEST_TIMEOUT)
            if r.status_code in _RETRIABLE_STATUS:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            if r.status_code != 200:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")  # 4xx 不重试
            body = r.json()
            content = body["choices"][0]["message"]["content"]
            obj = extract_json_block(content)          # JSON 坏掉也算可重试
            usage = body.get("usage", {}) or {}
            USAGE.add(usage, retries=attempt)
            return obj, {"tokens": usage, "attempts": attempt + 1, "raw": content}
        except (requests.RequestException, ValueError, KeyError) as e:
            last_err = f"{type(e).__name__}: {e}"
            continue

    USAGE.fail()
    raise LLMError(f"重试 {config.MAX_RETRIES} 次仍失败：{last_err}")
