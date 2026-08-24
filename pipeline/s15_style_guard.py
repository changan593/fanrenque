#!/usr/bin/env python3
"""
步骤 15：画风金标准的**程序化闸门**。不调 API。

`doc/04_风格规范.md` 3.1 把画风载体定为**国风三维**（中国院线三维 CG 动画电影，
风格化写实）。这是全项目的金标准，一切提示词、资产卡、规范文档都必须与它一致。

这一步兑现那句「优先排查出来修改」——把排查做成每次都能重跑的检查，
而不是一次性的人肉 grep。

## 它检查什么

1. **载体声明冲突**：提示词里出现「厚涂／笔触／赛璐璐／平涂／水墨／二维／
   线稿／描边／概念设定图／painterly／brushwork／cel shading…」等**非三维载体**的
   正向声明。负面写法（「不做水墨」「禁描边」「no 2D anime」）是**对的**，不报。
2. **风格词入侵内容层**：`production/s01/prompts/` 是画面内容层，
   按 `prompts/00_总则与模板.md` 第一节，任何风格词都不许出现在正向提示词里
   ——风格段由程序统一追加。这一项按那份文档自己的黑名单查。
3. **金标准锚点在不在**：`production/style_assets/` 的两份统一提示词必须存在，
   且都声明为已定稿。

## 判正负的办法

按标点把行切成**子句**，只看该子句里有没有否定词
（不／无／非／禁／免／避免／严禁／删／去掉／no／not／without／avoid／never／禁止）。
「风格：半写实厚涂，笔触可见」→ 子句无否定 → **报**。
「不描边、不加阴影」「No photography, 2D anime, painterly art」→ 有否定 → 不报。

## 留痕豁免

选型过程的文档与配置**故意**保留旧载体的名字，它们是证据不是指令。
豁免名单见 `LEGACY_ALLOW`，每一条都写了为什么。豁免文件仍会统计，
用 `--show-legacy` 看。

## 用法

    python pipeline/s15_style_guard.py              # 全量检查，有违规则退出码 1
    python pipeline/s15_style_guard.py --show-legacy # 连留痕文件一起列出来
    python pipeline/s15_style_guard.py --path production/s01/prompts/E01
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths  # noqa: E402


# ── 非三维载体的正向声明 ───────────────────────────────────────────────
# 出现即冲突，除非所在子句是否定式。
CARRIER_TERMS = [
    # 中文
    "厚涂", "笔触", "赛璐璐", "平涂", "三渲二", "线稿", "描边",
    "水墨", "二维", "卡通渲染", "手绘", "油画", "插画", "概念设定图",
    "照片级", "真人摄影", "真人剧照", "低多边形", "游戏引擎",
    # 英文
    "painterly", "brushwork", "brushstroke", "brush stroke",
    "thick paint", "impasto", "cel shading", "cel-shaded",
    "2d anime", "anime style", "illustration", "concept art",
    "ink wash", "watercolor", "watercolour", "photoreal", "photo-real",
    "live action", "live-action", "hand-painted", "toon shading",
]

# 否定标记。出现在词**之前**即视为负面写法，是对的。
NEGATIONS = [
    "不", "无", "没", "非", "禁", "免", "避免", "严禁", "删", "去掉", "别", "勿",
    "拒绝", "作废", "已废", "不再", "改为", "原「", "旧称", "而不是", "并非",
    "低于", "弱于", "少于",              # 「细节密度低于真人摄影」是在划上限
    "no ", "not ", "without", "avoid", "never", "exclude", "instead of",
    "negative",                          # 数据字典里的 negative_extra 字段样例
]

# 句子切分：只在真正的句末断开，逗号顿号不断
# ——「不做蜡像、塑料、二维厚涂、水墨」是一句话，否定管到底
SENT_SPLIT = re.compile(r"[。！!？?\n]")

# 负面清单代码块的引子：紧邻在代码块上方的文字里出现这些 → 整块豁免
NEG_BLOCK_CUE = re.compile(
    r"负面|negative|不要什么|禁改|禁止|不得|排除|黑名单|作废|已废|不再"
)

# ── 留痕豁免：选型过程的证据，不是指令 ────────────────────────────────
LEGACY_ALLOW = {
    "doc/06_画风与画幅选型.md":
        "选型分析留痕。头部已标注『结论是国风三维，本文推荐的 A 已作废』",
    "doc/07_画风矩阵测试.md":
        "矩阵操作手册留痕。头部已标注『选型已结束』",
    "doc/12_参考片画风研究.md":
        "参考片拆解留痕，是 3.1 定稿的推导过程。头部已标注结论",
    "production/style_test/matrix.json":
        "选型矩阵配置，11 个画风候选按定义就该列出旧载体",
    "production/style_test/prompts.md":
        "由 matrix.json 导出的产物",
    "production/style_test/refs/README.md":
        "八部参考片的截图采集清单，描述的是参考片不是本片",
    "production/style_assets/refs_场景参考_image2/场景反推提示词.md":
        "参考图的忠实反推，文件头已声明不可直接入生产",
    "pipeline/s15_style_guard.py":
        "本文件。检查器自己必须写出被禁的词",
}

# ── 内容层：风格词一律不许出现（黑名单来自 prompts/00_总则与模板.md 第 1.1 节）──
CONTENT_LAYER = "production/s01/prompts"
STYLE_WORDS_IN_CONTENT = [
    "厚涂", "笔触", "半写实", "水墨", "油画", "插画", "赛璐璐",
    "二次元", "动漫", "漫画风", "三渲二", "建模", "滤镜", "胶片", "颗粒感",
    "画质", "超清", "杰作", "masterpiece", "best quality", "artstation",
    "octane", "虚幻引擎", "画风",
]
# 说明：黑名单原文还含「CG / 渲染 / 写实 / 高清 / 4k / 8k / 低饱和 / 饱和度 /
# 调色 / 色调统一 / 风格 / style」等。其中一部分在中文里是常用字
# （「渲染气氛」「色调」「风格化」），逐字查会淹没在误报里。
# 这里只查歧义低的那批；完整黑名单仍以 00_总则与模板.md 为准，人工复核时对照。


def prompt_regions(text: str):
    """只取会被喂给模型的区域：围栏代码块与引用块。

    散文、表格、说明性文字不在内——它们到不了模型，
    「厚涂的笔触每次重画都是一次重新解释」这种定稿理由不该报。

    返回 [(行号, 行文, 是否负面块)]
    """
    out = []
    lines = text.splitlines()
    in_fence = False
    fence_is_neg = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                # 往上找最近两行非空文字，判断这块是不是负面清单
                cue = ""
                for j in range(i - 1, max(-1, i - 4), -1):
                    if lines[j].strip():
                        cue += lines[j]
                fence_is_neg = bool(NEG_BLOCK_CUE.search(cue))
            in_fence = not in_fence
            continue
        if in_fence:
            out.append((i + 1, line, fence_is_neg))
        elif stripped.startswith(">"):
            out.append((i + 1, line, bool(NEG_BLOCK_CUE.search(line))))
    return out


def scan_file(path: Path, rel: str, content_layer: bool):
    """返回 [(行号, 词, 片段, 类别)]，只扫提示词区域。"""
    hits = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits

    terms = CARRIER_TERMS + (STYLE_WORDS_IN_CONTENT if content_layer else [])
    for lineno, line, is_neg_block in prompt_regions(text):
        if is_neg_block:
            continue
        low = line.lower()
        if not any(t in line or t in low for t in terms):
            continue
        for sent in SENT_SPLIT.split(line):
            sl = sent.lower()
            for t in terms:
                pos = sent.find(t)
                if pos < 0 and t.isascii():
                    pos = sl.find(t)
                if pos < 0:
                    continue
                head = sent[:pos]
                hl = head.lower()
                if any(n in head or n in hl for n in NEGATIONS):
                    continue          # 否定在前，是负面清单写法
                hits.append((lineno, t, sent.strip()[:110], 
                             "载体冲突" if t in CARRIER_TERMS else "风格词入侵内容层"))
                break
    return hits


def check_anchors() -> list[str]:
    """金标准锚点必须在，且声明为已定稿。"""
    problems = []
    base = paths.PRODUCTION_DIR / "style_assets"
    for name in ("统一风格提示词.md", "统一角色提示词.md"):
        f = base / name
        if not f.exists():
            problems.append(f"缺失：{f.relative_to(paths.ROOT)}")
            continue
        if "定稿" not in f.read_text(encoding="utf-8"):
            problems.append(f"未声明定稿：{f.relative_to(paths.ROOT)}")
    spec = paths.ROOT / "doc" / "04_风格规范.md"
    if spec.exists() and "国风三维" not in spec.read_text(encoding="utf-8"):
        problems.append("doc/04_风格规范.md 里找不到「国风三维」——3.1 载体没落地？")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="画风金标准闸门：国风三维")
    ap.add_argument("--path", type=Path, default=None,
                    help="只查这个目录或文件（默认查 production/ 与 doc/）")
    ap.add_argument("--show-legacy", action="store_true",
                    help="把留痕豁免文件也列出来")
    args = ap.parse_args()

    print("=" * 62)
    print("画风金标准闸门　国风三维（doc/04 第 3.1 节）")
    print("=" * 62)

    for p in check_anchors():
        print(f"  ✗ 锚点：{p}")

    roots = ([args.path.resolve()] if args.path
             else [paths.PRODUCTION_DIR, paths.ROOT / "doc"])
    files: list[Path] = []
    for r in roots:
        if r.is_file():
            files.append(r)
        else:
            files += [p for p in r.rglob("*.md")]
            files += [p for p in r.rglob("*.json") if "style_test" in str(p)]
    files = sorted(set(files))

    violations, legacy_hits, scanned = {}, {}, 0
    for f in files:
        rel = str(f.relative_to(paths.ROOT))
        hits = scan_file(f, rel, content_layer=rel.startswith(CONTENT_LAYER))
        scanned += 1
        if not hits:
            continue
        (legacy_hits if rel in LEGACY_ALLOW else violations)[rel] = hits

    if violations:
        print(f"\n🔴 {len(violations)} 个文件与金标准冲突：\n")
        for rel, hits in sorted(violations.items()):
            print(f"  {rel}")
            for lineno, term, clause, kind in hits[:8]:
                print(f"     {lineno:>5}  [{kind}] 「{term}」  {clause}")
            if len(hits) > 8:
                print(f"     …… 另有 {len(hits) - 8} 处")
            print()
    else:
        print("\n✓ 没有与金标准冲突的正向声明")

    if args.show_legacy and legacy_hits:
        print(f"\n留痕豁免（{len(legacy_hits)} 个文件，是证据不是指令）：\n")
        for rel, hits in sorted(legacy_hits.items()):
            print(f"  {rel}  ({len(hits)} 处)")
            print(f"     理由：{LEGACY_ALLOW[rel]}")

    total = sum(len(v) for v in violations.values())
    print("-" * 62)
    print(f"扫描 {scanned} 个文件｜违规 {total} 处 / {len(violations)} 个文件"
          f"｜留痕豁免 {len(legacy_hits)} 个文件")

    if violations:
        print("\n改法：正向提示词里删掉载体词，风格段统一由")
        print("      production/style_assets/ 的两份文件在出图时追加。")
        print("      负面清单里保留这些词是对的——那是在禁止它们。")
        return 1
    print("\n✓ 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
