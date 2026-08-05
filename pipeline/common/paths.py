"""全项目路径的唯一事实来源。任何脚本都不要自己拼路径。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 源数据
SOURCE_DIR = ROOT / "source"
RAW_TXT = SOURCE_DIR / "穿越后，系统变成白噪音了怎么办.txt"
NOVEL_JSON = SOURCE_DIR / "novel.json"          # 标准化全文
NOVEL_INDEX = SOURCE_DIR / "novel.index.json"   # 轻量目录（不含正文）

# 数据资产
DATA_DIR = ROOT / "data"
CHAPTERS_DIR = DATA_DIR / "chapters"            # 逐章分析，一章一个 json
CHARACTERS_DIR = DATA_DIR / "characters"        # 角色资产
SCENES_DIR = DATA_DIR / "scenes"                # 场景资产
PLOT_DIR = DATA_DIR / "plot"                    # 剧情线分析
SEASONS_DIR = DATA_DIR / "seasons"              # 分季分集方案

# 管道自身
PIPELINE_DIR = ROOT / "pipeline"
PROMPTS_DIR = PIPELINE_DIR / "prompts"
SCHEMAS_DIR = PIPELINE_DIR / "schemas"
CHAPTER_SCHEMA = SCHEMAS_DIR / "chapter.schema.json"

# 产出
PRODUCTION_DIR = ROOT / "production"
DOC_DIR = ROOT / "doc"

# 运行期产物（不入库）
RUN_DIR = ROOT / ".run"
LOG_DIR = RUN_DIR / "logs"
CACHE_DIR = RUN_DIR / "cache"


def chapter_json_path(seq: int) -> Path:
    """seq -> data/chapters/ch0001.json"""
    return CHAPTERS_DIR / f"ch{seq:04d}.json"


def ensure_dirs() -> None:
    for d in (SOURCE_DIR, CHAPTERS_DIR, CHARACTERS_DIR, SCENES_DIR,
              PLOT_DIR, SEASONS_DIR, PRODUCTION_DIR, DOC_DIR,
              RUN_DIR, LOG_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
