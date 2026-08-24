#!/usr/bin/env python3
"""
步骤 16：**按幕装配出图包**。不调 API。

做某一幕的图时，需要的东西散在五个地方：剧本、幕提示词、角色卡、场景卡、风格段。
手工凑一次要翻十几个文件，还容易漏掉克制版约束或用错阶段卡。
这一步把它们按 `production/style_assets/README.md` 的九步拼装顺序装配成一份文档，
外加一张参考图清单，直接可以开工。

## 用法

    # 列出某一集有哪些幕
    python pipeline/s16_shot_pack.py --list s01/E01

    # 装配一幕
    python pipeline/s16_shot_pack.py s01/E01/01_第一幕_北阳城城隍庙

    # 写到文件而不是标准输出
    python pipeline/s16_shot_pack.py s01/E01/01_第一幕_北阳城城隍庙 -o out.md

    # 只要参考图清单（喂给出图工具）
    python pipeline/s16_shot_pack.py s01/E01/01_第一幕_北阳城城隍庙 --refs-only

## 它怎么知道这一幕要哪些卡

读幕提示词里的两张表：

- `## 本幕人物与状态` —— 第一列是角色名，第二列写着资产对位（如 `C01·4.1 破庙状态`）
- 正文里出现的场景码 `SNN` / `SNN-x`

角色名与 `production/characters/CNN_名字/` 按**名字**匹配，
场景码与 `production/scenes/SNN_名字/` 按**编号**匹配。
匹配不上的会明确报出来，不会静默跳过——**漏掉一张卡比报错危险**。

## 装配顺序

严格按 `production/style_assets/README.md`：原文证据 → 身份母版 → 阶段卡 →
画面内容 → 统一角色提示词 → 追加段 → 克制版约束 → 负面约束 → 统一风格提示词。
越靠前优先级越高，后面的段不得覆盖前面的。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths  # noqa: E402

PROD = paths.PRODUCTION_DIR
CHARS = PROD / "characters"
SCENES = PROD / "scenes"
STYLE = PROD / "style_assets"

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg"}

# ── 不建卡是对的：这些名字匹配不上是设计，不是遗漏 ──────────────────
# 每条都写清楚规格在哪，免得后人以为是漏了
NO_CARD_BY_DESIGN = {
    "唐假": "Q 版旁白装置，规格在 doc/05_唐假旁白系统.md 第 4.2 节（二头身，脚下无影）",
    "人魔尊": "即齐渊，规格在 production/s01/02_角色资产.md C05；E01 只有一帧模糊剪影",
    "雪中身影": "就是唐真本人（两年前的闪回），用 C01_唐真 + S01·04 雪门口闪回",
    "店铺掌柜": "纯背景虚化人物，原文无描写，按 doc/04 第五节不发挥",
    "硕大的人头": "身份原文未明，处理为极远景剪影，见 02_角色资产.md 次要角色卡",
}


# ── 找卡 ──────────────────────────────────────────────────────────────
def card_dirs(root: Path) -> dict[str, Path]:
    """{`C01_唐真` -> 路径}"""
    if not root.is_dir():
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if p.is_dir()}


def states_of(card: Path) -> list[Path]:
    return [p for p in sorted(card.iterdir()) if p.is_dir()]


def md_of(state: Path) -> Path | None:
    hits = sorted(state.glob("*超详细提示词.md"))
    return hits[0] if hits else None


def imgs_of(state: Path) -> list[Path]:
    return [p for p in sorted(state.iterdir()) if p.suffix.lower() in IMG_EXT]


# ── 解析幕文档 ────────────────────────────────────────────────────────
ROSTER_HEAD = re.compile(r"^##\s*本幕人物与状态\s*$")
NEXT_HEAD = re.compile(r"^##\s")
SCENE_CODE = re.compile(r"\bS(\d{2})\b")


def parse_act(act_md: Path):
    """返回 (角色名列表, 资产对位说明, 场景码集合, 幕标题, 对应剧本行)"""
    text = act_md.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = lines[0].lstrip("# ").strip() if lines else act_md.parent.name
    script_ref = next((l for l in lines[:12] if "对应剧本" in l), "")

    names, pairing = [], {}
    inside = False
    for l in lines:
        if ROSTER_HEAD.match(l):
            inside = True
            continue
        if inside and NEXT_HEAD.match(l):
            break
        if inside and l.startswith("|"):
            cells = [c.strip() for c in l.strip("|").split("|")]
            if len(cells) < 2 or cells[0] in ("角色", "") or set(cells[0]) <= set("- "):
                continue
            raw = cells[0]
            # 「红儿（小丫鬟）」「唐真（梦中·仙宫期）」→ 取括号前的主名
            name = re.split(r"[（(]", raw)[0].strip().strip("*")
            names.append(name)
            pairing[name] = cells[1]

    scenes = {f"S{m.group(1)}" for m in SCENE_CODE.finditer(text)}
    return names, pairing, sorted(scenes), title, script_ref


# ── 装配 ──────────────────────────────────────────────────────────────
def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p and p.exists() else ""


def section(title: str, body: str) -> str:
    return f"\n\n{'─' * 66}\n## {title}\n{'─' * 66}\n\n{body.rstrip()}\n"


def build(act_dir: Path, refs_only: bool = False) -> tuple[str, list[str]]:
    act_md = act_dir / "提示词.md"
    if not act_md.exists():
        raise SystemExit(f"找不到幕提示词：{act_md}")

    names, pairing, scodes, title, script_ref = parse_act(act_md)
    chars, scenes = card_dirs(CHARS), card_dirs(SCENES)

    # 同一角色可能在名册里出现多行（如「唐真（梦中）」「唐真（惊醒）」），按卡目录去重
    hit_c, miss_c, seen_c = [], [], set()
    for n in names:
        m = [d for k, d in chars.items() if k.split("_", 1)[-1] == n]
        if not m:
            if n not in miss_c:
                miss_c.append(n)
            continue
        if m[0] in seen_c:
            pairing.setdefault(n, "")      # 同卡的第二行，只并对位说明
            continue
        seen_c.add(m[0])
        hit_c.append((n, m[0]))

    hit_s, miss_s = [], []
    for code in scodes:
        m = [d for k, d in scenes.items() if k.split("_", 1)[0] == code]
        (hit_s.append((code, m[0])) if m else miss_s.append(code))

    ref_imgs: list[str] = []
    # 本幕自己的分镜草图（构图与运镜锁）
    sb = act_dir / "分镜草图.svg"
    if sb.exists():
        ref_imgs.append(str(sb.relative_to(paths.ROOT)))
    # 角色与场景卡的图（角色卡 PNG、场景平面图 SVG、场景卡 PNG）
    for _, d in hit_c + hit_s:
        for st in states_of(d):
            ref_imgs += [str(p.relative_to(paths.ROOT)) for p in imgs_of(st)]

    # 去重（保序）
    seen, uniq = set(), []
    for x in ref_imgs:
        if x not in seen:
            seen.add(x); uniq.append(x)
    ref_imgs = uniq

    if refs_only:
        return "", ref_imgs

    ep = act_dir.parent
    out = [f"# 出图包｜{title}", "",
           f"由 `pipeline/s16_shot_pack.py` 装配。**这是产物，不要手改**——",
           f"改内容请改下面各段的源文件。", "",
           f"- 幕目录：`{act_dir.relative_to(paths.ROOT)}`",
           f"- {script_ref.strip() or '剧本：' + str((ep / '剧本.md').relative_to(paths.ROOT))}",
           f"- 角色 {len(hit_c)} 个｜场景 {len(hit_s)} 个｜参考图 {len(ref_imgs)} 张", ""]

    if miss_c or miss_s:
        out += ["> ## ⚠ 有卡没找到", ">"]
        if miss_c:
            real = [n for n in miss_c if n not in NO_CARD_BY_DESIGN]
            byd = [n for n in miss_c if n in NO_CARD_BY_DESIGN]
            if real:
                out += [f"> **角色（真的缺卡）**：{'、'.join(real)}", ">",
                        "> 这些多半是次要角色／群像，规格在 "
                        "`production/s01/02_角色资产.md` 的紧凑卡里；"
                        "若是主要角色或成组出现的龙套，"
                        "该建 `production/characters/` 下的卡。", ">"]
            if byd:
                out += ["> **角色（不建卡是对的）**：", ">"]
                out += [f"> - **{n}**：{NO_CARD_BY_DESIGN[n]}" for n in byd]
                out += [">"]
        if miss_s:
            out += [f"> **场景**：{'、'.join(miss_s)}", ">",
                    "> 说明还没建 `production/scenes/SNN_名字/`。", ">"]
        if [n for n in miss_c if n not in NO_CARD_BY_DESIGN] or miss_s:
            out += ["> **不要当它不存在就开工**——先补卡，或确认它确实只需要紧凑卡。", ""]
        else:
            out += ["> 以上都有明确去处，可以开工。", ""]

    out.append("## 拼装顺序")
    out.append("")
    out.append("越靠前优先级越高，后面的段**不得覆盖**前面的。冲突时一律服从原文。")
    out.append("")
    for i, s in enumerate([
        "原文章节证据与当前镜头事实（幕提示词里逐镜的【原】引用）",
        "身份母版引用 + 身份一致性段",
        "当前阶段允许变化项、服装、标志物",
        "画面内容：构图、景别、动作、表情、光源出处",
        "统一角色提示词（完整版或短版）",
        "角色卡 / 剧情镜头专用追加段",
        "《凡人阙》项目克制版追加约束　**必须**",
        "公共负面约束",
        "统一风格提示词（含环境时）　**完整版 + 克制版追加约束**",
    ], 1):
        out.append(f"{i}. {s}")

    # ① 幕的画面内容层
    out.append(section("① 本幕画面内容（第 1、4 步）", read(act_md)))

    # ② 角色卡
    for n, d in hit_c:
        out.append(section(f"② 角色｜{n}　（第 2、3 步）",
                           f"资产对位（幕文档所写）：**{pairing.get(n, '—')}**\n\n"
                           f"卡目录：`{d.relative_to(paths.ROOT)}`"))
        for st in states_of(d):
            md, ims = md_of(st), imgs_of(st)
            tag = "★ 身份母版" if st.name.startswith("00_") else "状态"
            out.append(f"\n### {tag}｜`{st.name}`\n")
            out.append(f"参考图：{'、'.join(f'`{p.name}`' for p in ims) if ims else '**待生成**'}\n")
            out.append(read(md) if md else "_（缺提示词 md）_")

    # ③ 场景卡
    for code, d in hit_s:
        out.append(section(f"③ 场景｜{d.name}　（第 4 步的环境部分）",
                           f"卡目录：`{d.relative_to(paths.ROOT)}`"))
        for st in states_of(d):
            md, ims = md_of(st), imgs_of(st)
            tag = "★ 场景母版" if st.name.startswith("00_") else "状态"
            out.append(f"\n### {tag}｜`{st.name}`\n")
            out.append(f"参考图：{'、'.join(f'`{p.name}`' for p in ims) if ims else '**待生成**'}\n")
            out.append(read(md) if md else "_（缺提示词 md）_")

    # ④ 风格段
    out.append(section("④ 统一角色提示词　（第 5、6、7、8 步）",
                       read(STYLE / "统一角色提示词.md")))
    out.append(section("⑤ 统一风格提示词　（第 9 步）",
                       read(STYLE / "统一风格提示词.md")))

    # ⑥ 参考图清单
    lst = "\n".join(f"- `{p}`" for p in ref_imgs) if ref_imgs else \
        "_一张都还没有。角色卡与场景卡的 PNG 都待生成——\n" \
        "先按上面各卡的「可直接生成提示词」把母版出出来，再回头做本幕。_"
    out.append(section("⑥ 参考图清单", lst))

    # ⑦ 自查
    out.append(section("⑦ 开工前自查", """按 `doc/04_风格规范.md` 第十三节：

- [ ] 画面里的东西**原文有没有**？没有的删掉
- [ ] 有没有出现负面清单里的词（`doc/04` 3.3）？
- [ ] 光有没有**出处**？
- [ ] 物体有没有**使用痕迹**？
- [ ] 人物有没有被**美化**成原文没写的样子？
- [ ] 是不是在**炫**？炫就砍
- [ ] 术法有没有变成**喷火放电**？
- [ ] 本集奇观额度**超了没有**？

外加本管线两条：

- [ ] **克制版追加约束带上了没有**——两份统一提示词各有一段，缺一不可
- [ ] 阶段卡用对了没有——对着幕文档的「资产对位」列逐个核

最后跑一次画风闸门：

```bash
python pipeline/s15_style_guard.py --path {act}
```""".replace("{act}", str(act_dir.relative_to(paths.ROOT)))))

    return "\n".join(out), ref_imgs


# ── CLI ───────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="按幕装配出图包")
    ap.add_argument("act", nargs="?",
                    help="幕目录，如 s01/E01/01_第一幕_北阳城城隍庙"
                         "（相对 production/，也接受完整路径）")
    ap.add_argument("--list", metavar="EP", help="列出某一集的幕，如 s01/E01")
    ap.add_argument("-o", "--out", type=Path, help="写到文件")
    ap.add_argument("--refs-only", action="store_true", help="只打印参考图清单")
    args = ap.parse_args()

    if args.list:
        ep = PROD / args.list
        if not ep.is_dir():
            raise SystemExit(f"找不到 {ep}")
        print(f"{args.list} 的幕：")
        for d in sorted(p for p in ep.iterdir() if p.is_dir()):
            has = "✓" if (d / "提示词.md").exists() else "✗ 无提示词"
            sk = "✓" if (d / "分镜草图.svg").exists() else "－无草图"
            print(f"  {has}  {sk}  {d.name}")
        return 0

    if not args.act:
        ap.error("要么给幕目录，要么给 --list")

    act_dir = Path(args.act)
    if not act_dir.is_dir():
        act_dir = PROD / args.act
    if not act_dir.is_dir():
        raise SystemExit(f"找不到幕目录 {args.act}")

    body, refs = build(act_dir, refs_only=args.refs_only)

    if args.refs_only:
        print("\n".join(refs) if refs else "（暂无参考图）", file=sys.stdout)
        return 0

    if args.out:
        args.out.write_text(body, encoding="utf-8")
        print(f"已写出 {args.out}（{len(body)} 字，参考图 {len(refs)} 张）")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
