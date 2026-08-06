"""
管道统一配置。所有可调参数集中在这里，脚本里不要出现散落的魔法数字。

配置来源优先级：真实环境变量 > 项目根目录的 .env > 这里的默认值。
密钥只从 .env 或环境变量读，绝不写进代码，.env 已在 .gitignore 里。
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _clean_value(val: str) -> str:
    """
    取出 KEY=VALUE 右边的真实值。

    行尾注释必须剥掉：.env.example 里写满了 `LLM_TIMEOUT=600   # 单次请求总时限`
    这样的说明，直接 cp 成 .env 之后，值会变成 "600   # 单次请求总时限"，
    到 int() 那一步才炸，而且报错完全看不出是注释的锅。
    """
    val = val.strip()
    if val[:1] in ("\"", "'"):
        quote = val[0]
        end = val.find(quote, 1)
        return val[1:end] if end != -1 else val[1:]   # 引号内原样，引号后一律丢掉
    if val.startswith("#"):        # 等号右边整个就是注释，等于没赋值
        return ""
    # 未加引号时，只有「空白 + #」才算注释。
    # 不能见 # 就切——密钥里真的可能带 #，切了就成了错密钥，比报错更难查。
    return re.split(r"\s+#", val, maxsplit=1)[0].rstrip()


def _load_dotenv(path: Path = ENV_FILE) -> None:
    """
    极简 .env 读取，不引第三方依赖。支持 KEY=VALUE、export KEY=VALUE、
    整行注释、行尾注释、引号包裹的值。已存在的真实环境变量优先，不会被 .env 覆盖。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), _clean_value(val))


def _bool(name: str, default: str) -> bool:
    """
    读开关型配置。大小写、0/1、true/false、yes/no、on/off 一律认。

    这里必须宽松：写错一个开关不会报错，只会**静默地按相反的方式跑**，
    等发现时几个小时已经烧掉了——LLM_THINKING 就是这样的开关。
    """
    v = os.getenv(name, default).strip().lower()
    if v in ("0", "false", "no", "off", "n", ""):
        return False
    if v in ("1", "true", "yes", "on", "y"):
        return True
    raise SystemExit(
        f"配置项 {name} 的值看不懂：{os.getenv(name)!r}\n"
        f"  只接受 0/1、true/false、yes/no、on/off。\n"
        f"  改这里：{ENV_FILE}"
    )


def _num(name: str, default: str, cast=int):
    """
    读数值型配置。配置读错会挡住一切，所以报错必须说清是哪个键、值是什么、
    该去哪儿改——而不是甩一句 invalid literal for int()。
    """
    raw = os.getenv(name, default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise SystemExit(
            f"配置项 {name} 的值不是合法数字：{raw!r}\n"
            f"  常见原因：.env 里这一行的值后面跟了行尾注释或多余字符。\n"
            f"  改这里：{ENV_FILE}\n"
            f"  正确写法：{name}={default}"
        ) from None


_load_dotenv()

# ---------------- API ----------------
API_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
API_KEY_ENV = "DEEPSEEK_API_KEY"          # 从环境变量读，绝不写进仓库
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 用流式接收：非流式请求在生成完之前什么都收不到，几分钟里无法判断是活着还是卡死。
# 流式下每收到一段就能刷新进度，还能用「多久没收到数据」做卡死判定。
STREAM = _bool("LLM_STREAM", "1")

# 强制 JSON 输出模式。个别模型不支持，会返回空回复；关掉后靠提示词约束，
# 解析仍走 extract_json_block 兜底。
JSON_MODE = _bool("LLM_JSON_MODE", "1")

# 思考模式（思维链）。**默认关掉**，这是刻意的：
#
# DeepSeek 官方文档写明「思考模式默认打开，且 effort 默认为 high」，
# 而逐章分析是「照着原文抄成结构化 JSON」的抽取活，不是推理题——
# 思维链在这里只带来三个坏处：
#   1) 思维链和正式回复共用 max_tokens，长章节会把额度吃光，content 直接为空；
#   2) 官方文档同时写明「使用 JSON Output 功能时，API 有概率返回空的 content」，
#      两者叠加时空回复概率极高（实测 500 章跑了 6 小时 45 分，只成功 9 章）；
#   3) 思考模式下 temperature 不生效，抽取要的稳定性反而拿不到。
# 需要模型多想时（比如只重跑审查环节）再单独打开。
THINKING = _bool("LLM_THINKING", "0")
# 思考强度，仅在 THINKING 打开时发送。flash 的映射：low→low, high→high, max→max
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "high")

CONNECT_TIMEOUT = _num("LLM_CONNECT_TIMEOUT", "20")   # 建连超时
STALL_TIMEOUT = _num("LLM_STALL_TIMEOUT", "90")       # 流式：两段数据之间的最大间隔
REQUEST_TIMEOUT = _num("LLM_TIMEOUT", "600")          # 单次请求总时限
MAX_RETRIES = _num("LLM_MAX_RETRIES", "5")            # 失败重试次数
RETRY_BASE_DELAY = 2.0                                          # 退避基数：2/4/8/16/32 秒
# 推理模型的思维链也算在 max_tokens 里，给少了会出现「额度被思维链吃光、
# 正式回复为空」。max_tokens 是上限不是预扣，按实际生成量计费，给足不额外花钱。
MAX_OUTPUT_TOKENS = _num("LLM_MAX_TOKENS", "16384")
# 被 finish_reason=length 截断时自动上调 max_tokens 的封顶值。
# 无脑重试同一个截断请求只会截断在同一个地方，必须把额度加上去才有意义。
MAX_OUTPUT_CAP = _num("LLM_MAX_TOKENS_CAP", "65536")

# 三次调用各自的温度：抽取要稳，审查要冷静
TEMPERATURE = {
    "extract": 0.2,
    "structure_review": 0.0,
    "fidelity_review": 0.0,
    "repair": 0.2,
}

# ---------------- 并发与节流 ----------------
WORKERS = _num("PIPELINE_WORKERS", "4")          # 并发章节数
QPS_LIMIT = _num("PIPELINE_QPS", "0", float)          # 0 = 不限速

# ---------------- 质量门槛 ----------------
# 两次审查的合格分数线，低于线触发修订轮
PASS_SCORE = {"structure_review": 85, "fidelity_review": 90}
# 逐字引用命中率门槛：对话/独白/旁白必须能在原文里逐字找到
VERBATIM_PASS_RATE = 0.95
# 修订轮上限（0 = 关闭，只做三次调用）
MAX_REPAIR_ROUNDS = _num("PIPELINE_REPAIR_ROUNDS", "1")

# ---------------- 上下文 ----------------
PREV_SYNOPSIS_COUNT = 3        # 结构审查时回喂前几章简介
ALIAS_REGISTRY_LIMIT = 400     # 传给抽取的已知人物名上限，防止 prompt 膨胀

# ---------------- 出图 API（image-2.0，兼容 OpenAI Images API）----------------
# 官方文档明确要求 base_url 末尾不带斜杠，这里统一 rstrip 掉，
# 免得 .env 里多敲一个 / 就整批 404。
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://api2.tabcode.cc/openai/draw/v1").rstrip("/")
IMAGE_API_KEY_ENV = "IMAGE_API_KEY"
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2")
# 16:9 = 1.778。接口给的档位里 1792x1024 = 1.75 最接近，直接用它，
# 后期统一裁到 16:9 只需切掉左右各 1%。1536x1024 是 3:2，明显更方。
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1792x1024")
IMAGE_WORKERS = _num("IMAGE_WORKERS", "3")
# 单次请求出几张。接口支持 n:3，一次要满能省 2/3 的请求数；
# 若上游只认 n=1，脚本会按实际返回张数自动补跑，不会漏图。
IMAGE_BATCH = _num("IMAGE_BATCH", "3")
IMAGE_TIMEOUT = _num("IMAGE_TIMEOUT", "300")     # 出图比出文慢，给足
IMAGE_MAX_RETRIES = _num("IMAGE_MAX_RETRIES", "5")
# 出图的退避基数比文本大一档：5xx 多半是上游整体抽风，几秒内重试大概率还是撞上，
# 且这类错误按官方文档不扣费，等得起。4/8/16/32 秒共约 1 分钟，够熬过一次短暂故障。
IMAGE_RETRY_BASE = _num("IMAGE_RETRY_BASE", "4", float)


def image_api_key() -> str:
    key = os.getenv(IMAGE_API_KEY_ENV)
    if not key:
        raise SystemExit(
            f"未找到 {IMAGE_API_KEY_ENV}。二选一：\n"
            f"  1) cp .env.example .env  然后填入画图密钥（推荐）\n"
            f"  2) export {IMAGE_API_KEY_ENV}=你的密钥\n"
            f"  当前查找的 .env 路径：{ENV_FILE}"
        )
    return key


def api_key() -> str:
    key = os.getenv(API_KEY_ENV)
    if not key:
        raise SystemExit(
            f"未找到 {API_KEY_ENV}。二选一：\n"
            f"  1) cp .env.example .env  然后填入密钥（推荐）\n"
            f"  2) export {API_KEY_ENV}=你的密钥\n"
            f"  当前查找的 .env 路径：{ENV_FILE}"
        )
    return key


def describe() -> str:
    """启动时打印当前生效的配置，避免跑到一半才发现参数不对。"""
    src = ".env" if ENV_FILE.exists() else "环境变量/默认值"
    think = f"思考{REASONING_EFFORT}" if THINKING else "思考关"
    return (f"模型 {MODEL} | 配置来源 {src} | 并发 {WORKERS} | "
            f"{think} | JSON模式{'开' if JSON_MODE else '关'} | "
            f"额度 {MAX_OUTPUT_TOKENS}→{MAX_OUTPUT_CAP} | "
            f"合格线 结构{PASS_SCORE['structure_review']}/一致{PASS_SCORE['fidelity_review']} | "
            f"逐字门槛 {VERBATIM_PASS_RATE:.0%} | 修订轮上限 {MAX_REPAIR_ROUNDS}")
