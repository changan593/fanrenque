"""
管道统一配置。所有可调参数集中在这里，脚本里不要出现散落的魔法数字。
环境变量优先，方便本地跑的时候临时覆盖。
"""
import os

# ---------------- API ----------------
API_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY_ENV = "DEEPSEEK_API_KEY"          # 从环境变量读，绝不写进仓库
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

REQUEST_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))     # 单次请求超时（秒）
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))       # 失败重试次数
RETRY_BASE_DELAY = 2.0                                     # 退避基数：2/4/8/16/32 秒
MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))

# 三次调用各自的温度：抽取要稳，审查要冷静
TEMPERATURE = {
    "extract": 0.2,
    "structure_review": 0.0,
    "fidelity_review": 0.0,
    "repair": 0.2,
}

# ---------------- 并发与节流 ----------------
WORKERS = int(os.getenv("PIPELINE_WORKERS", "4"))          # 并发章节数
QPS_LIMIT = float(os.getenv("PIPELINE_QPS", "0"))          # 0 = 不限速

# ---------------- 质量门槛 ----------------
# 两次审查的合格分数线，低于线触发修订轮
PASS_SCORE = {"structure_review": 85, "fidelity_review": 90}
# 逐字引用命中率门槛：对话/独白/旁白必须能在原文里逐字找到
VERBATIM_PASS_RATE = 0.95
# 修订轮上限（0 = 关闭，只做三次调用）
MAX_REPAIR_ROUNDS = int(os.getenv("PIPELINE_REPAIR_ROUNDS", "1"))

# ---------------- 上下文 ----------------
PREV_SYNOPSIS_COUNT = 3        # 结构审查时回喂前几章简介
ALIAS_REGISTRY_LIMIT = 400     # 传给抽取的已知人物名上限，防止 prompt 膨胀


def api_key() -> str:
    key = os.getenv(API_KEY_ENV)
    if not key:
        raise SystemExit(
            f"未设置 {API_KEY_ENV}。请先执行：export {API_KEY_ENV}=你的密钥"
        )
    return key
