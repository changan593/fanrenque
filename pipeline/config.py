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

# 台词覆盖率门槛：原文里带引号的说话有多少被抽进 dialogues。s3 低于它报问题，
# s2m 低于它判人工稿不合格。doc/02 第六节写的 95% 是全书均值的验收目标，不是单章门槛。
COVERAGE_PASS_RATE = 0.90

# ---------------- 上下文 ----------------
PREV_SYNOPSIS_COUNT = 3        # 结构审查时回喂前几章简介
ALIAS_REGISTRY_LIMIT = 400     # 传给抽取的已知人物名上限，防止 prompt 膨胀

# ---------------- 体检（s3）----------------
SYNOPSIS_MIN_CHARS = 40          # 简介短于此报「过短」；真正要抓的是被截断（末尾没句读）
REVIEW_ANALYSIS_MIN_CHARS = 100  # 审查记录的详细分析短于此报「过短」

# ---------------- 资产聚合（s4）----------------
# 一个称呼要以「正名」身份出现过这么多章，才算独立主名——两个都含主名的簇不许合并。
# 定 5 是为了既挡住主角互相吞并，又不影响真正的别名归并（别名通常只作 alias 出现）。
PRINCIPAL_MIN_CHAPTERS = 5
SUSPICIOUS_STRONG_MIN = 10       # 簇内有多个出场 ≥ 此值的称呼 → 报可疑归并
COOCCURRENCE_TOP = 3000          # 同框关系保留多少对

# ---------------- 剧情线摘要（s9）----------------
DIGEST_MAJOR_MIN_CHAPTERS = 8    # 出场章数 ≥ 此值才进摘要与 timeline，龙套不入

# ---------------- 分集求解（s10）----------------
# 时长模型：语音字数 ÷ 4.5 字/秒 × 1.20。1.20 由 E01/E02 两集人工试点标定（doc/08 第五节）。
# 注意：这些只是**求解器排分集边界用的估值**，doc/00 已决定不对成片时长做预设。
CHARS_PER_SEC = 4.5
PAUSE_FACTOR = 1.20
EPISODE_TARGET_MIN = 28.0        # 目标时长（分钟）
EPISODE_SOFT_RANGE = (25.0, 32.0)  # 舒适区，轻罚
EPISODE_HARD_RANGE = (18.0, 42.0)  # 越过这条罚到几乎不可选
# 每集章数范围。下限给到 2 是必要的：seq655~659 连续三章加起来就 42~44 分，
# 卡死 3 章下限只能做出一个超长集。上限 5 是为了不让一集稀释成流水账。
EPISODE_CHAPTERS = (2, 5)
CUT_PENALTY_SOFT = 8             # 收在软断点的惩罚
CUT_PENALTY_NONE = 25            # 从卷段中间劈开的惩罚（硬断点与季末为 0）

# ---------------- 卷段检测（s12）----------------
ARC_CHAPTERS = (10, 50)          # 卷段长度范围
ARC_EXIT_MIN_CHAPTERS = 15       # 出场 ≥ 此值的角色末现才算「主要角色退场」（硬断的唯一判据）
ARC_CHAR_WEIGHT = 0.65           # 变更分里「换人」的权重，其余给「换景」——观众跟的是人

# ---------------- 每集资产需求（s11）----------------
ASSET_LOAD_CHAR_WEIGHT = 2       # asset_load = 新角色 × 2 + 新场景

# ---------------- 按季清单（s13）----------------
ROSTER_MAJOR_MIN = 10            # 本季章数 ≥10 主要
ROSTER_MINOR_MIN = 3             # 3~9 次要，其余龙套
ROSTER_SCENE_KEY_MIN = 3         # 场景 ≥3 章算重点

# ---------------- 旁白承载账（s14，口径来自 doc/05 5.7 / 5.8）----------------
FREEZE_MAX = 6                   # 每集唐假现身（世界静止）次数上限
DELETE_RATE_ALARM = 0.15         # N6 删除率超过此值报警：判据用松了

# ---------------- 出图 API（OpenAI Images API 规范）----------------
# 按 OpenAI 官方 Images API 对接，base_url 与密钥走 .env，指向哪个网关都行。
# 官方要求 base_url 末尾不带斜杠，这里统一 rstrip，免得多敲一个 / 就整批 404。
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
IMAGE_API_KEY_ENV = "IMAGE_API_KEY"
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")

# 尺寸。**这是最容易踩的一格**：
#   gpt-image-*  只认 1024x1024 / 1536x1024 / 1024x1536 / auto
#   dall-e-3     才认 1792x1024 / 1024x1792
# 1792x1024 配 gpt-image 会被上游拒掉，网关多半包成 502，看着像网络故障。
# 默认取 1536x1024（3:2 横版，gpt-image 的官方横版档），出图后由脚本裁成精确 16:9。
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1536x1024")
# 各模型官方支持的尺寸，用于请求前自查，把「上游 502」提前变成「本地说人话」
IMAGE_SIZES_BY_MODEL = {
    "gpt-image": ("1024x1024", "1536x1024", "1024x1536", "auto"),
    "dall-e-3": ("1024x1024", "1792x1024", "1024x1792"),
    "dall-e-2": ("256x256", "512x512", "1024x1024"),
}

# 以下四个是 gpt-image 的官方可选参数。留空则不发——
# 网关不认某个参数会直接 400，能不发就不发。
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high")           # low / medium / high / auto
IMAGE_OUTPUT_FORMAT = os.getenv("IMAGE_OUTPUT_FORMAT", "")   # png / jpeg / webp
IMAGE_BACKGROUND = os.getenv("IMAGE_BACKGROUND", "")         # transparent / opaque / auto
# 我们的提示词里全是「缺牙」「丑陋」「肮脏」「乞丐」这类词，默认审核档容易误伤。
# low 是官方允许的档位，正是为这种情况准备的。
IMAGE_MODERATION = os.getenv("IMAGE_MODERATION", "low")      # low / auto

# 出图后裁成精确 16:9。接口给不出 16:9 的档位，与其迁就，不如出完自己裁——
# 全片画幅已定 16:9，测试图就该按成片画幅看。
IMAGE_CROP_169 = _bool("IMAGE_CROP_169", "1")

IMAGE_WORKERS = _num("IMAGE_WORKERS", "3")
# 单次请求出几张。官方 n 上限 10（dall-e-3 只能 1）。
# 若上游只认 n=1，脚本会按实际返回张数自动补跑，不会漏图。
IMAGE_BATCH = _num("IMAGE_BATCH", "3")
IMAGE_TIMEOUT = _num("IMAGE_TIMEOUT", "300")     # 出图比出文慢，给足
IMAGE_MAX_RETRIES = _num("IMAGE_MAX_RETRIES", "5")
# 出图的退避基数比文本大一档：5xx 多半是上游整体抽风，几秒内重试大概率还是撞上，
# 且这类错误按官方文档不扣费，等得起。4/8/16/32 秒共约 1 分钟，够熬过一次短暂故障。
IMAGE_RETRY_BASE = _num("IMAGE_RETRY_BASE", "4", float)


def image_size_check(model: str, size: str) -> str:
    """
    请求前自查尺寸。返回空串表示没问题，否则返回该说给人听的话。

    不做这一步的代价：1792x1024 配 gpt-image 会被上游拒，网关包成 502
    「Upstream network error」，看上去和网络故障一模一样，只能靠猜。
    """
    if size == "auto":
        return ""
    for prefix, allowed in IMAGE_SIZES_BY_MODEL.items():
        if model.startswith(prefix) and size not in allowed:
            # 指出这个尺寸是谁家的档位——「你写的这个是别的模型的」比
            # 「你写的这个不对」有用得多，用户十有八九是从混排的文档里抄来的
            owner = [m for m, sizes in IMAGE_SIZES_BY_MODEL.items()
                     if m != prefix and size in sizes]
            src = f"（{size} 是 {' / '.join(owner)} 的档位）" if owner else ""
            return (f"模型 {model} 不支持尺寸 {size}{src}。\n"
                    f"  {model} 支持：{' / '.join(allowed)}\n"
                    f"  这类请求会被上游拒掉，网关多半包成 502「Upstream network error」，"
                    f"看着像网络故障，其实是参数问题。\n"
                    f"  改 .env 里的 IMAGE_SIZE，或换 IMAGE_MODEL。")
    return ""


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
