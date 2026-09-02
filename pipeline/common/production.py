"""production/ 里角色卡与场景卡的目录索引。

卡的约定（见 production/characters/README.md 与 production/scenes/README.md）：

    production/characters/CNN_角色名/00_身份母版/角色名_身份母版_超详细提示词.md + 角色卡.png
    production/characters/GNN_群像名/00_群像母版/…
    production/scenes/SNN_场景名/00_场景母版/…_超详细提示词.md + 场景卡.png + 平面图.svg
                                 NN_状态名/…

以前 s13 判「有没有卡」读的是 production/s01/02_角色资产.md 的标题，
而卡早已搬到上面的目录里，于是姜羽、周东东、南季礼都被判成没卡。
现在 s13 与 s16 都从这里读，判据只有一个：目录在不在、母版提示词在不在。

以下划线开头的目录（`_深度档案/`、`_归档/`）不是卡，跳过。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import paths

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
CODE_RE = re.compile(r"^([CGS]\d{2})_(.+)$")


def card_dirs(root: Path) -> dict[str, Path]:
    """{名字 -> 卡目录}。名字取目录名 `CNN_` 之后的部分。"""
    if not root.is_dir():
        return {}
    out = {}
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith("_"):
            continue
        m = CODE_RE.match(p.name)
        out[m.group(2) if m else p.name] = p
    return out


def card_codes(root: Path) -> dict[str, Path]:
    """{编号 -> 卡目录}，如 {'S01': .../S01_北阳城城隍庙}。"""
    out = {}
    for name, p in card_dirs(root).items():
        m = CODE_RE.match(p.name)
        if m:
            out[m.group(1)] = p
    return out


def states_of(card: Path) -> list[Path]:
    """卡下的状态目录，00 母版排最前。"""
    return [p for p in sorted(card.iterdir()) if p.is_dir()]


def master_of(card: Path) -> Path | None:
    """00_ 开头的母版目录。"""
    return next((p for p in states_of(card) if p.name.startswith("00_")), None)


def md_of(state: Path) -> Path | None:
    hits = sorted(state.glob("*超详细提示词.md"))
    return hits[0] if hits else None


def imgs_of(state: Path) -> list[Path]:
    return [p for p in sorted(state.iterdir()) if p.suffix.lower() in IMG_EXT]


def has_master_prompt(card: Path) -> bool:
    """有母版目录且母版里有提示词——这才算「建了卡」。只有一张 PNG 不算。"""
    m = master_of(card)
    return bool(m and md_of(m))


def character_cards() -> dict[str, Path]:
    return card_dirs(paths.PROD_CHARACTERS_DIR)


def scene_cards() -> dict[str, Path]:
    return card_dirs(paths.PROD_SCENES_DIR)


def duplicate_codes(root: Path) -> dict[str, list[Path]]:
    """同一编号出现在两个目录名里——「同一个记号指两个人」是编号最坏的撞法。"""
    seen: dict[str, list[Path]] = {}
    for p in sorted(root.iterdir()) if root.is_dir() else []:
        m = CODE_RE.match(p.name) if p.is_dir() else None
        if m:
            seen.setdefault(m.group(1), []).append(p)
    return {k: v for k, v in seen.items() if len(v) > 1}
