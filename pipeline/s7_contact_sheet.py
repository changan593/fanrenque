#!/usr/bin/env python3
"""
步骤 7：把画风矩阵的所有图拼成一张大对比图（contact sheet）。

    横轴 = 画风（带名称、参考作品、一句话简介）
    纵轴 = 画面目标（带名称、为什么测它、看什么）
    每格 = 该组合的 3 张 16:9 图，竖排

一次性铺开，才能横着扫一行看「同一个画面各画风谁扛得住」，
竖着扫一列看「这个画风在十种画面里会不会翻车」。

产出（默认在 production/style_test/sheets/）：

    master.jpg              总表，10 目标 × 8 画风
    by_target/T03_*.jpg     单目标横向对比，图更大，判生死用这个
    by_style/S6_*.jpg       单画风纵向体检，看这个画风有没有短板

用法：
    python pipeline/s7_contact_sheet.py                  # 全部三种表都出
    python pipeline/s7_contact_sheet.py --only master
    python pipeline/s7_contact_sheet.py --thumb 640      # 缩略图更大
    python pipeline/s7_contact_sheet.py --format png     # 无损，但文件大得多
"""
import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths

STYLE_DIR = paths.PRODUCTION_DIR / "style_test"
OUT_DIR = STYLE_DIR / "out"
SHEET_DIR = STYLE_DIR / "sheets"

# 中文字体候选。缺字体会退化成豆腐块，那这张表就白拼了，所以找不到直接报错
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]

# 浅色底。这是拿来做判断的工具图，不是作品，可读性压倒一切
BG = (245, 243, 239)
HEAD_BG = (232, 228, 220)
HEAD_BG_ALT = (238, 235, 228)
INK = (28, 26, 23)
DIM = (110, 104, 94)
GRID = (201, 195, 184)
ACCENT = (168, 58, 42)          # ★ 关键项
HOLE = (222, 218, 210)          # 缺图占位


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit(
        "找不到中文字体，拼出来会全是豆腐块。装一个再跑：\n"
        "  Debian/Ubuntu: sudo apt-get install -y fonts-wqy-zenhei\n"
        "  或用 --font 指定一个 .ttc/.ttf 路径")


def load_fonts(path: str, scale: float) -> dict:
    s = lambda n: ImageFont.truetype(path, max(9, int(n * scale)))
    return {"title": s(40), "h1": s(24), "h2": s(19), "body": s(15), "tiny": s(13)}


def wrap(text: str, font, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """
    按像素宽度折行。中文没有空格，只能逐字量；遇到英文单词就整词量，
    免得把 《不良人》 这种混排里的拉丁词从中间劈开。
    """
    if not text:
        return []
    tokens = re.findall(r"[A-Za-z0-9]+|\s+|[^A-Za-z0-9\s]", text)
    lines, cur = [], ""
    for tok in tokens:
        if tok == "\n":
            lines.append(cur.rstrip())
            cur = ""
            continue
        trial = cur + tok
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok.lstrip() if tok.strip() == "" else tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def draw_block(draw, xy, lines, font, fill, leading: float = 1.32) -> int:
    """画一段折好行的文字，返回占用高度。"""
    x, y = xy
    lh = int(font.size * leading)
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=fill)
        y += lh
    return y - xy[1]


def is_key(item: dict) -> bool:
    """★ 可能标在「为什么测它」也可能标在「看什么」上，两处都认。"""
    return any((item.get(k) or "").lstrip().startswith("★")
               for k in ("why", "key_check", "brief"))


def cell_images(tid: str, sid: str, repeats: int, out_dir: Path) -> list[Path | None]:
    r = []
    for i in range(1, repeats + 1):
        p = out_dir / f"{tid}_{sid}_{i}.png"
        if not p.exists():                       # 万一手动换成了 jpg 也认
            alt = out_dir / f"{tid}_{sid}_{i}.jpg"
            p = alt if alt.exists() else p
        r.append(p if p.exists() else None)
    return r


def paste_thumb(sheet: Image.Image, draw, path: Path | None,
                box: tuple[int, int, int, int], font) -> None:
    x, y, w, h = box
    if path is None:
        draw.rectangle([x, y, x + w, y + h], fill=HOLE, outline=GRID)
        draw.text((x + w // 2 - 22, y + h // 2 - 8), "缺图", font=font, fill=DIM)
        return
    try:
        im = Image.open(path).convert("RGB")
    except Exception as e:
        draw.rectangle([x, y, x + w, y + h], fill=HOLE, outline=GRID)
        draw.text((x + 8, y + h // 2 - 8), f"读不出：{type(e).__name__}", font=font, fill=ACCENT)
        return
    # contain 而非 crop：这张表是用来判画风的，裁掉边缘可能正好裁掉要看的东西
    im = ImageOps.contain(im, (w, h), Image.LANCZOS)
    sheet.paste(im, (x + (w - im.width) // 2, y + (h - im.height) // 2))
    draw.rectangle([x, y, x + w, y + h], outline=GRID)


# ------------------------------------------------------------------ 拼表
def build_sheet(matrix: dict, targets: list[dict], styles: list[dict], *,
                title: str, subtitle: str, thumb_w: int, repeats: int,
                fonts: dict, out_dir: Path, transpose: bool = False) -> Image.Image:
    """
    transpose=False：列=画风，行=画面目标（总表与单目标表用这个）
    transpose=True ：列=画面目标，行=画风（单画风体检表用这个，横着铺更好看）
    """
    thumb_h = round(thumb_w * 9 / 16)
    gap, pad = 8, 22
    cell_w = thumb_w
    cell_h = repeats * thumb_h + (repeats - 1) * gap

    cols = targets if transpose else styles
    rows = styles if transpose else targets
    row_head_w = max(300, int(thumb_w * 0.78))
    col_head_h = 190 if not transpose else 150
    band_h = 96

    W = pad * 2 + row_head_w + len(cols) * (cell_w + gap) - gap
    H = pad * 2 + band_h + col_head_h + len(rows) * (cell_h + gap) - gap

    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)

    d.text((pad, pad), title, font=fonts["title"], fill=INK)
    draw_block(d, (pad, pad + int(fonts["title"].size * 1.35)),
               wrap(subtitle, fonts["body"], W - pad * 2, d), fonts["body"], DIM)

    top = pad + band_h
    left = pad + row_head_w

    # ---- 列表头 ----
    for j, c in enumerate(cols):
        x = left + j * (cell_w + gap)
        d.rectangle([x, top, x + cell_w, top + col_head_h - gap],
                    fill=HEAD_BG if j % 2 == 0 else HEAD_BG_ALT, outline=GRID)
        y = top + 10
        star = is_key(c)
        head = f"{c['id']}　{c['name']}"
        d.text((x + 10, y), head, font=fonts["h2"], fill=ACCENT if star else INK)
        y += int(fonts["h2"].size * 1.5)
        if c.get("ref"):
            d.text((x + 10, y), c["ref"], font=fonts["tiny"], fill=DIM)
            y += int(fonts["tiny"].size * 1.5)
        note = c.get("brief") or c.get("why") or ""
        limit = top + col_head_h - gap - 8
        for ln in wrap(note, fonts["tiny"], cell_w - 20, d):
            if y + fonts["tiny"].size > limit:
                break
            d.text((x + 10, y), ln, font=fonts["tiny"], fill=DIM)
            y += int(fonts["tiny"].size * 1.34)

    # ---- 行表头 + 格子 ----
    for i, r in enumerate(rows):
        y0 = top + col_head_h + i * (cell_h + gap)
        d.rectangle([pad, y0, pad + row_head_w - gap, y0 + cell_h],
                    fill=HEAD_BG if i % 2 == 0 else HEAD_BG_ALT, outline=GRID)
        star = is_key(r)
        y = y0 + 12
        d.text((pad + 12, y), f"{r['id']}　{r['name']}", font=fonts["h1"],
               fill=ACCENT if star else INK)
        y += int(fonts["h1"].size * 1.6)
        if r.get("ref"):
            d.text((pad + 12, y), r["ref"], font=fonts["body"], fill=DIM)
            y += int(fonts["body"].size * 1.5)
        tw = row_head_w - gap - 24
        limit = y0 + cell_h - 10
        for label, txt in (("为什么测它", r.get("why")), ("看什么", r.get("key_check")),
                           ("", r.get("brief"))):
            if not txt:
                continue
            if label:
                if y + fonts["body"].size > limit:
                    break
                d.text((pad + 12, y), label, font=fonts["body"],
                       fill=ACCENT if label == "看什么" else DIM)
                y += int(fonts["body"].size * 1.45)
            for ln in wrap(txt, fonts["tiny"], tw, d):
                if y + fonts["tiny"].size > limit:
                    break
                d.text((pad + 12, y), ln, font=fonts["tiny"], fill=INK)
                y += int(fonts["tiny"].size * 1.34)
            y += 6

        for j, c in enumerate(cols):
            t, s = (c, r) if transpose else (r, c)
            x = left + j * (cell_w + gap)
            for k, img in enumerate(cell_images(t["id"], s["id"], repeats, out_dir)):
                paste_thumb(sheet, d, img,
                            (x, y0 + k * (thumb_h + gap), thumb_w, thumb_h), fonts["tiny"])
    return sheet


def rel(p: Path) -> str:
    """尽量打印仓库内相对路径；--out 指到仓库外时退回绝对路径。"""
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def save(sheet: Image.Image, path: Path, fmt: str) -> Path:
    path = path.with_suffix("." + fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jpg":
        sheet.save(path, "JPEG", quality=94, subsampling=1, optimize=True)
    else:
        sheet.save(path, "PNG")
    mb = path.stat().st_size / 1024 / 1024
    print(f"  {rel(path)}　{sheet.width}×{sheet.height}　{mb:.1f}MB")
    return path


def safe(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff·]+", "", name)


def main() -> int:
    ap = argparse.ArgumentParser(description="把画风矩阵拼成大对比图")
    ap.add_argument("--matrix", type=Path, default=STYLE_DIR / "matrix.json")
    ap.add_argument("--images", type=Path, default=OUT_DIR)
    ap.add_argument("--out", type=Path, default=SHEET_DIR)
    ap.add_argument("--thumb", type=int, default=460, help="总表单张缩略图宽度")
    ap.add_argument("--repeats", type=int, default=None)
    ap.add_argument("--font", default=None)
    ap.add_argument("--format", choices=["jpg", "png"], default="jpg",
                    help="jpg 体积小得多、更好打开；要无损再用 png")
    ap.add_argument("--only", choices=["master", "target", "style", "all"], default="all")
    args = ap.parse_args()

    if not args.matrix.exists():
        raise SystemExit(f"找不到矩阵定义：{args.matrix}")
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    repeats = args.repeats or int(matrix["meta"].get("repeats_per_cell", 3))
    targets, styles = matrix["targets"], matrix["styles"]

    have = sum(1 for t in targets for s in styles
               for p in cell_images(t["id"], s["id"], repeats, args.images) if p)
    total = len(targets) * len(styles) * repeats
    if have == 0:
        raise SystemExit(f"{args.images} 里一张图都没有。先跑 s6_style_matrix.py 出图。")
    print(f"已有 {have}/{total} 张图。缺的会画成「缺图」占位，补完再跑一次即可。")

    font_path = args.font or find_font()
    fonts = load_fonts(font_path, 1.0)
    big = load_fonts(font_path, 1.25)
    args.out.mkdir(parents=True, exist_ok=True)

    ver = matrix["meta"].get("version", "")
    foot = (f"{config.IMAGE_MODEL} · {config.IMAGE_SIZE} · 每格 {repeats} 张 · "
            f"矩阵 v{ver}　｜　行首标 ★ 的是关键判据，先看它")

    if args.only in ("master", "all"):
        print("总表：")
        sheet = build_sheet(matrix, targets, styles,
                            title="画风选型总表　画面目标 × 画风",
                            subtitle="横轴＝画风，纵轴＝画面目标，每格三张同提示词重绘。" + foot,
                            thumb_w=args.thumb, repeats=repeats, fonts=fonts,
                            out_dir=args.images)
        save(sheet, args.out / "master", args.format)

    if args.only in ("target", "all"):
        print("单目标横向对比：")
        for t in targets:
            sheet = build_sheet(matrix, [t], styles,
                                title=f"{t['id']}　{t['name']}　八种画风对比",
                                subtitle=f"{t.get('why','')}　｜　看什么：{t.get('key_check','')}",
                                thumb_w=int(args.thumb * 1.35), repeats=repeats,
                                fonts=big, out_dir=args.images)
            save(sheet, args.out / "by_target" / f"{t['id']}_{safe(t['name'])}", args.format)

    if args.only in ("style", "all"):
        print("单画风纵向体检：")
        for s in styles:
            sheet = build_sheet(matrix, targets, [s],
                                title=f"{s['id']}　{s['name']}"
                                      f"{'（' + s['ref'] + '）' if s.get('ref') else ''}"
                                      f"　十种画面体检",
                                subtitle=s.get("brief", "") + "　｜　"
                                         "看这个画风有没有撑不住的画面类型",
                                thumb_w=int(args.thumb * 0.92), repeats=repeats,
                                fonts=fonts, out_dir=args.images, transpose=True)
            save(sheet, args.out / "by_style" / f"{s['id']}_{safe(s['name'])}", args.format)

    print(f"\n全在 {rel(args.out)}／")
    print("看的顺序：先 by_target/T03（老年反美化）和 T06（无阴影）淘汰，"
          "再 by_style 查短板，最后 master 看整体。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
