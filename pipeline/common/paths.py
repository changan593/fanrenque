"""全项目路径的唯一事实来源。任何脚本都不要自己拼路径。

目录职责（详见根 README「目录」一节）：
  source/      原文，只读
  data/        脚本产物，删了能重跑；data/manual/ 是例外——人定的输入（分季表等）
  production/  人写的成片素材，删了就没了
  .run/        运行期产物（日志、体检报告），不入库
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------- 源数据 ----------------
SOURCE_DIR = ROOT / "source"
RAW_TXT = SOURCE_DIR / "穿越后，系统变成白噪音了怎么办.txt"
NOVEL_JSON = SOURCE_DIR / "novel.json"          # 标准化全文
NOVEL_INDEX = SOURCE_DIR / "novel.index.json"   # 轻量目录（不含正文）

# ---------------- 数据资产 ----------------
DATA_DIR = ROOT / "data"
CHAPTERS_DIR = DATA_DIR / "chapters"            # 逐章分析，一章一个 json（s2 / s2m / s5）
CHARACTERS_DIR = DATA_DIR / "characters"        # 角色资产 cNNN + index.json（s4）
SCENES_DIR = DATA_DIR / "scenes"                # 场景资产 sNNN + index.json（s4）
DOSSIERS_DIR = DATA_DIR / "dossiers"            # 角色卷宗 dossier_*.md/.json（s8；json 不入库）
PLOT_DIR = DATA_DIR / "plot"                    # 剧情线、分集表等（s0/s9/s10/s11/s12/s13）
MANUAL_DATA_DIR = DATA_DIR / "manual"           # 人定的输入：分季表、人工卷段、一次性分析产物

CHAR_INDEX = CHARACTERS_DIR / "index.json"
SCENE_INDEX = SCENES_DIR / "index.json"
SEASONS_JSON = MANUAL_DATA_DIR / "seasons.json" # 六季定义，人定，可改
ARCS_JSON = PLOT_DIR / "arcs.json"
EPISODES_JSON = PLOT_DIR / "episodes.json"
EPISODE_ASSETS_JSON = PLOT_DIR / "episode_assets.json"
TIMELINE_JSON = PLOT_DIR / "timeline.json"
COOCCURRENCE_JSON = PLOT_DIR / "cooccurrence.json"
SEASON_ROSTER_JSON = PLOT_DIR / "season_roster.json"
SEASON_ROSTER_MD = PLOT_DIR / "season_roster.md"
QUOTE_REPAIR_LOG = PLOT_DIR / "quote_repair_log.json"
TEXT_PROBE_JSON = PLOT_DIR / "text_probe.json"

# ---------------- 管道自身 ----------------
PIPELINE_DIR = ROOT / "pipeline"
PROMPTS_DIR = PIPELINE_DIR / "prompts"
CHAPTER_SCHEMA = PIPELINE_DIR / "schemas" / "chapter.schema.json"

# ---------------- 产出 ----------------
PRODUCTION_DIR = ROOT / "production"
PROD_CHARACTERS_DIR = PRODUCTION_DIR / "characters"   # CNN_名 / GNN_名 角色卡
PROD_SCENES_DIR = PRODUCTION_DIR / "scenes"           # SNN_名 场景卡
PROFILES_DIR = PROD_CHARACTERS_DIR / "_深度档案"       # 人写的角色深度档案
STYLE_ASSETS_DIR = PRODUCTION_DIR / "style_assets"
STYLE_TEST_DIR = PRODUCTION_DIR / "style_test"
MANUAL_ANALYSIS_DIR = PRODUCTION_DIR / "s01" / "manual_analysis"   # s2m 的手写稿

DOC_DIR = ROOT / "doc"

# ---------------- 运行期产物（不入库）----------------
RUN_DIR = ROOT / ".run"
LOG_DIR = RUN_DIR / "logs"
REPORTS_DIR = RUN_DIR / "reports"               # s3 体检报告等每次重写的报告


def chapter_json_path(seq: int) -> Path:
    """seq -> data/chapters/ch0001.json"""
    return CHAPTERS_DIR / f"ch{seq:04d}.json"


def season_dir(season: int) -> Path:
    """1 -> production/s01"""
    return PRODUCTION_DIR / f"s{season:02d}"


def episode_dir(code: str) -> Path:
    """'S01E07' -> production/s01/E07"""
    return season_dir(int(code[1:3])) / f"E{code[4:6]}"


def script_path(code: str) -> Path:
    """'S01E07' -> production/s01/E07/剧本.md"""
    return episode_dir(code) / "剧本.md"


def ensure_dirs() -> None:
    """只建脚本真的会往里写的目录。"""
    for d in (CHAPTERS_DIR, CHARACTERS_DIR, SCENES_DIR, DOSSIERS_DIR, PLOT_DIR,
              LOG_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
