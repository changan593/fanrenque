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
2. **风格词入侵内容层**：`production/s01/` 是画面内容层，
   按 `production/s01/01_总则与模板.md` 第一节，任何风格词都不许出现在正向提示词里
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
    python pipeline/s15_style_guard.py --path production/s01/E01
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
    "doc/archive/06_画风与画幅选型.md":
        "选型分析留痕。头部已标注『结论是国风三维，本文推荐的 A 已作废』",
    "doc/archive/07_画风矩阵测试.md":
        "矩阵操作手册留痕。头部已标注『选型已结束』",
    "doc/archive/12_参考片画风研究.md":
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

# ── 内容层：风格词一律不许出现（黑名单来自 production/s01/01_总则与模板.md 第 1.1 节）──
CONTENT_LAYER = "production/s01"
STYLE_WORDS_IN_CONTENT = [
    "厚涂", "笔触", "半写实", "水墨", "油画", "插画", "赛璐璐",
    "二次元", "动漫", "漫画风", "三渲二", "建模", "滤镜", "胶片", "颗粒感",
    "画质", "超清", "杰作", "masterpiece", "best quality", "artstation",
    "octane", "虚幻引擎", "画风",
]
# 说明：黑名单原文还含「CG / 渲染 / 写实 / 高清 / 4k / 8k / 低饱和 / 饱和度 /
# 调色 / 色调统一 / 风格 / style」等。其中一部分在中文里是常用字
# （「渲染气氛」「色调」「风格化」），逐字查会淹没在误报里。
# 这里只查歧义低的那批；完整黑名单仍以 01_总则与模板.md 为准，人工复核时对照。


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


def scan_file(path: Path, content_layer: bool):
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
    for name in ("统一风格提示词.md", "统一角色提示词.md"):
        f = paths.STYLE_ASSETS_DIR / name
        if not f.exists():
            problems.append(f"缺失：{f.relative_to(paths.ROOT)}")
            continue
        if "定稿" not in f.read_text(encoding="utf-8"):
            problems.append(f"未声明定稿：{f.relative_to(paths.ROOT)}")
    spec = paths.DOC_DIR / "04_风格规范.md"
    if spec.exists() and "国风三维" not in spec.read_text(encoding="utf-8"):
        problems.append("doc/04_风格规范.md 里找不到「国风三维」——3.1 载体没落地？")
    return problems



def canonical_blocks() -> dict[str, str]:
    """从 style_assets/统一角色提示词.md 取出规范段落（短版 / 克制版）。"""
    f = paths.STYLE_ASSETS_DIR / "统一角色提示词.md"
    if not f.exists():
        return {}
    blocks = re.findall(r"```text\n(.*?)\n```", f.read_text(encoding="utf-8"), re.S)
    out = {}
    for b in blocks:
        if b.startswith("高预算中国院线3D动画电影人物"):
            out["短版"] = b
        elif b.startswith("服从《凡人阙》"):
            out["克制版"] = b
    return out


def check_render_locks() -> list[str]:
    """渲染锁逐字共用：卡里每一处渲染锁都必须与统一文件一字不差。

    s15 的载体词扫描只抓「正向声明了别的画风」；
    一张卡自己手写一段渲染锁、词都不犯规但和统一文件不一致，
    照样是画风漂移——C01 唐真曾经三个阶段卡三种写法。

    检查两件事：
      ① 短版与克制版**都在**（缺一不可）；
      ② 卡里**任何一行**以规范段落开头的文字，必须与规范段落完全相同。
         ② 是必要的：一张卡通常有两份短版（渲染锁一份、拼进英文提示词一份），
         只查「在不在」的话，改坏其中一份也查不出来。
    """
    canon = canonical_blocks()
    if not canon:
        return ["读不到 style_assets/统一角色提示词.md 的规范段落"]
    heads = {name: block[:14] for name, block in canon.items()}
    problems = []
    root = paths.PROD_CHARACTERS_DIR
    if not root.exists():
        return problems
    for card in sorted(root.rglob("*_超详细提示词.md")):
        text = card.read_text(encoding="utf-8")
        rel = str(card.relative_to(paths.ROOT))
        for name, block in canon.items():
            if block not in text:
                problems.append(f"{rel}：缺{name}")
        for lineno, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            for name, head in heads.items():
                if s.startswith(head) and s != canon[name]:
                    want = canon[name]
                    k = next((i for i, (a, b) in enumerate(zip(s, want))
                              if a != b), min(len(s), len(want)))
                    problems.append(
                        f"{rel}:{lineno}：{name}被改过，不是逐字"
                        f"（第 {k + 1} 字起：卡里「{s[k:k + 12]}」"
                        f"／应为「{want[k:k + 12]}」）")
    return problems


def check_homology_locks() -> list[str]:
    """同源锁逐字共用：两张卡共用的段落必须与权威文件一字不差。

    第一把是眉眼同源锁（南红枝 × 红儿）：原文把「眉眼相像」当叙事工具用了
    91 章，相像的那部分若靠两张卡各写各的形容词，出图三张就散了。
    机制与渲染锁完全一致：权威在 production/characters/_*同源锁*.md，
    卡里只许原样抄。
    """
    problems = []
    root = paths.PROD_CHARACTERS_DIR
    if not root.exists():
        return problems
    for f in sorted(root.glob("_*同源锁*.md")):
        text = f.read_text(encoding="utf-8")
        rel_f = str(f.relative_to(paths.ROOT))
        m = re.search(r"绑定[：:]\s*(.+)", text)
        if not m:
            problems.append(f"{rel_f}：缺「绑定：」行")
            continue
        dirs = re.findall(r"`([^`]+)`", m.group(1))
        blocks = re.findall(r"```text\n(.*?)\n```", text, re.S)
        if not dirs or not blocks:
            problems.append(f"{rel_f}：绑定卡或 ```text 同源段为空")
            continue
        for d in dirs:
            master = root / d / "00_身份母版"
            cards = sorted(master.glob("*_超详细提示词.md")) if master.exists() else []
            if not cards:
                problems.append(f"{rel_f}：绑定的 {d} 找不到身份母版卡")
                continue
            ct = cards[0].read_text(encoding="utf-8")
            rel_c = str(cards[0].relative_to(paths.ROOT))
            for bi, b in enumerate(blocks, 1):
                if b in ct:
                    continue
                bad = next((ln for ln in b.splitlines()
                            if ln.strip() and ln not in ct), b.splitlines()[0])
                problems.append(f"{rel_c}：缺同源段{bi}（或被改过）"
                                f"——第一处对不上的行：「{bad[:24]}…」")
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

    anchor_problems = check_anchors()
    for p in anchor_problems:
        print(f"  ✗ 锚点：{p}")

    lock_problems = check_render_locks()
    if lock_problems:
        print(f"\n🔴 渲染锁逐字共用：{len(lock_problems)} 处不一致\n")
        for p in lock_problems:
            print(f"     {p}")
        print("\n     改法：把 production/style_assets/统一角色提示词.md 的")
        print("     短版与克制版**原样**复制进卡里。要改风格改那份文件，不要改单张卡。")
    else:
        print("\n✓ 渲染锁逐字共用：全部角色卡一致")

    homology_problems = check_homology_locks()
    if homology_problems:
        print(f"\n🔴 同源锁逐字共用：{len(homology_problems)} 处不一致\n")
        for p in homology_problems:
            print(f"     {p}")
        print("\n     改法：改 production/characters/ 下的 _*同源锁*.md 权威文件，")
        print("     然后把新段**原样**抄进绑定的每张卡。单独改一张卡就是漂移。")
    else:
        print("\n✓ 同源锁逐字共用：绑定卡全部一致")

    roots = ([args.path.resolve()] if args.path
             else [paths.PRODUCTION_DIR, paths.DOC_DIR])
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
        hits = scan_file(f, content_layer=rel.startswith(CONTENT_LAYER))
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
    if violations or lock_problems or homology_problems or anchor_problems:
        return 1
    print("\n✓ 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
