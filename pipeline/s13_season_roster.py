#!/usr/bin/env python3
"""
步骤 13：按季汇总「这一季涉及哪些角色、哪些场景」。**不调 API。**

`s11` 回答的是「每一集要什么」，这一步回答上一级的问题：
**开一季的工，一共要备多少资产、哪些是重头、哪些只是路过。**
它是阶段四 4.2／4.3（角色与场景提示词深化）的工作队列。

## 归属怎么定

别名 → 主名用 `common/names.build_resolver` 的三级判定，**级别不够就不并**：

| 级 | 依据 | 例 |
| --- | --- | --- |
| 1 | `names.PRESETS`（人工逐字核实过的身份） | `红儿 → 姚望舒` |
| 2 | 索引里**只有一个主名认领**的别名 | `三只眼 → 唐真` |
| 3 | 多方认领 / 本身也是主名 / 人工标了存疑 | `师姐` 原样保留，标存疑 |

第三级**不猜**。宁可清单里多出一个「师姐」让你自己判，
也不要悄悄把它算到某个人头上。s9 / s11 现在用的是同一个解析器。

## 「有没有卡」怎么判

看 `production/characters/` 与 `production/scenes/` 里有没有对应名字的目录、
母版里有没有提示词（`common/production.py`）。早先读的是
`production/s01/02_角色资产.md` 的标题，卡搬进目录后那份标题就过期了，
姜羽、周东东、南季礼都被判成没卡——判据必须跟着资产走。

用法：
    python pipeline/s13_season_roster.py              # 六季全出
    python pipeline/s13_season_roster.py --season 1   # 只出第一季的 md
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths, production
from common.jsonio import read_json, write_json
from common.names import build_resolver

MAJOR, MINOR = config.ROSTER_MAJOR_MIN, config.ROSTER_MINOR_MIN
SCENE_KEY = config.ROSTER_SCENE_KEY_MIN


def collect(seq_lo, seq_hi, resolve):
    """扫一段 seq，返回 {名字: {chapters, first_seq, 存疑}} 与场景同构的表。"""
    chars: dict[str, dict] = {}
    scenes: dict[str, dict] = {}
    for seq in range(seq_lo, seq_hi + 1):
        p = paths.chapter_json_path(seq)
        if not p.exists():
            continue
        a = read_json(p).get("analysis") or {}

        per_c: dict[str, bool] = {}
        for c in a.get("characters") or []:
            if not isinstance(c, dict) or c.get("mentioned_only"):
                continue
            nm = (c.get("name") or "").strip()
            if nm:
                who, doubt = resolve(nm)
                per_c[who] = per_c.get(who, False) or doubt
        for who, doubt in per_c.items():
            e = chars.setdefault(who, {"chapters": 0, "first_seq": seq, "doubt": False})
            e["chapters"] += 1
            e["doubt"] = e["doubt"] or doubt

        for s in {s.get("name") for s in a.get("scenes") or []
                  if isinstance(s, dict) and s.get("name")}:
            e = scenes.setdefault(s, {"chapters": 0, "first_seq": seq})
            e["chapters"] += 1
    return chars, scenes


def tier(n: int) -> str:
    return "主要" if n >= MAJOR else ("次要" if n >= MINOR else "龙套")


def card_status(root: Path) -> dict[str, bool]:
    """{名字: 母版里有没有提示词}。只有 PNG 没提示词的目录算「没建卡」。"""
    return {name: production.has_master_prompt(d) for name, d in production.card_dirs(root).items()}


def rows(tbl, gidx, cards, seq2code, lo, hi, is_char, ambiguity, dossiers=None):
    r = []
    for name, v in sorted(tbl.items(), key=lambda kv: (-kv[1]["chapters"], kv[0])):
        g = gidx.get(name) or {}
        row = {
            "name": name,
            "season_chapters": v["chapters"],
            "book_chapters": g.get("chapter_count"),
            "first_seq": v["first_seq"],
            "first_episode": seq2code.get(v["first_seq"]),
            "debuts_this_season": g.get("first_seq") is not None and lo <= g["first_seq"] <= hi,
            "tier": tier(v["chapters"]),
            "has_card": cards.get(name, False),
        }
        if is_char:
            row.update(ambiguous=v["doubt"],
                       claimed_by=ambiguity.get(name) if v["doubt"] else None,
                       has_dossier=name in (dossiers or set()))
        else:
            row.update(types=g.get("types") or {})
        r.append(row)
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="按季汇总角色与场景清单")
    ap.add_argument("--seasons", type=Path, default=paths.SEASONS_JSON)
    ap.add_argument("--episodes", type=Path, default=paths.EPISODES_JSON)
    ap.add_argument("--season", type=int, help="md 只输出这一季（json 始终全出）")
    ap.add_argument("--out-json", type=Path, default=paths.SEASON_ROSTER_JSON)
    ap.add_argument("--out-md", type=Path, default=paths.SEASON_ROSTER_MD)
    args = ap.parse_args()

    for f in (args.seasons, args.episodes, paths.CHAR_INDEX, paths.SCENE_INDEX):
        if not f.exists():
            raise SystemExit(f"缺 {f.relative_to(paths.ROOT)}，先跑 s4 / s10")

    seasons = read_json(args.seasons)
    eps = read_json(args.episodes)["episodes"]
    cidx = read_json(paths.CHAR_INDEX)["characters"]
    sidx = read_json(paths.SCENE_INDEX)["scenes"]

    resolve, ambiguity = build_resolver(cidx)
    g_char = {c["canonical_name"]: c for c in cidx}
    g_scene = {s["name"]: s for s in sidx}

    # seq -> 集号
    seq2code = {}
    for e in eps:
        for s in range(e["seq_start"], e["seq_end"] + 1):
            seq2code[s] = e["code"]

    # 已有的人工产物：深度档案（production/characters/_深度档案）、角色卡、场景卡
    dossiers = {p.name.split("_角色深度档案")[0]
                for p in paths.PROFILES_DIR.glob("*_角色深度档案.md")} if paths.PROFILES_DIR.exists() else set()
    c_cards = card_status(paths.PROD_CHARACTERS_DIR)
    s_cards = card_status(paths.PROD_SCENES_DIR)

    out, md = [], []
    md.append("# 各季资产清单（角色与场景）\n")
    md.append("由 `pipeline/s13_season_roster.py` 生成，**不要手改**——"
              "改了会被下次重跑覆盖。\n")
    md.append("口径与已知问题见文末。\n")

    for i, s in enumerate(seasons, 1):
        lo, hi = s["seq_start"], s["seq_end"]
        chars, scenes = collect(lo, hi, resolve)
        codes = sorted({seq2code[q] for q in range(lo, hi + 1) if q in seq2code})

        crows = rows(chars, g_char, c_cards, seq2code, lo, hi, True, ambiguity, dossiers)
        srows = rows(scenes, g_scene, s_cards, seq2code, lo, hi, False, ambiguity)
        out.append({"season": i, "name": s["name"],
                    "seq_start": lo, "seq_end": hi,
                    "episodes": len(codes),
                    "episode_start": codes[0] if codes else None,
                    "episode_end": codes[-1] if codes else None,
                    "character_count": len(crows), "scene_count": len(srows),
                    "characters": crows, "scenes": srows})

        if args.season and args.season != i:
            continue

        md.append(f"\n---\n\n## 第{i}季《{s['name']}》\n")
        md.append(f"`{codes[0]}`–`{codes[-1]}`　共 {len(codes)} 集　seq {lo}–{hi}\n")
        md.append(f"**角色 {len(crows)} 个**（主要 "
                  f"{sum(1 for r in crows if r['tier']=='主要')}、次要 "
                  f"{sum(1 for r in crows if r['tier']=='次要')}、龙套 "
                  f"{sum(1 for r in crows if r['tier']=='龙套')}）　"
                  f"**场景 {len(srows)} 个**（≥{SCENE_KEY} 章的 "
                  f"{sum(1 for r in srows if r['season_chapters']>=SCENE_KEY)} 个）　"
                  f"已建卡：角色 {sum(1 for r in crows if r['has_card'])}、"
                  f"场景 {sum(1 for r in srows if r['has_card'])}\n")

        for label, sel in (("主要角色", lambda r: r["tier"] == "主要"),
                           ("次要角色", lambda r: r["tier"] == "次要")):
            sub = [r for r in crows if sel(r)]
            if not sub:
                continue
            md.append(f"\n### {label}（{len(sub)}）\n")
            md.append("| # | 角色 | 本季章数 | 全书章数 | 首现集 | 本季首现 |"
                      " 深度档案 | 资产卡 | 存疑 |")
            md.append("| --- | --- | ---: | ---: | --- | :-: | :-: | :-: | --- |")
            for n, r in enumerate(sub, 1):
                doubt = ("★ " + "／".join(r["claimed_by"][:4])) if r["ambiguous"] else ""
                md.append(f"| {n} | {r['name']} | {r['season_chapters']} |"
                          f" {r['book_chapters'] or '—'} | {r['first_episode']} |"
                          f" {'✓' if r['debuts_this_season'] else ''} |"
                          f" {'✓' if r['has_dossier'] else ''} |"
                          f" {'✓' if r['has_card'] else ''} | {doubt} |")

        extras = [r for r in crows if r["tier"] == "龙套"]
        if extras:
            md.append(f"\n### 龙套（{len(extras)}）\n")
            md.append("出场 1~2 章。按 `production/characters/README.md` 的群像卡（GNN）处理即可，"
                      "不必逐个做基准图。\n")
            md.append("　".join(f"{r['name']}({r['season_chapters']})" for r in extras))
            md.append("")

        key = [r for r in srows if r["season_chapters"] >= SCENE_KEY]
        md.append(f"\n### 重点场景（{len(key)}，本季 ≥{SCENE_KEY} 章）\n")
        md.append("| # | 场景 | 本季章数 | 全书章数 | 首现集 | 本季首现 | 室内/室外 | 资产卡 |")
        md.append("| --- | --- | ---: | ---: | --- | :-: | --- | :-: |")
        for n, r in enumerate(key, 1):
            t = "／".join(f"{k}{v}" for k, v in (r["types"] or {}).items()) or "—"
            md.append(f"| {n} | {r['name']} | {r['season_chapters']} |"
                      f" {r['book_chapters'] or '—'} | {r['first_episode']} |"
                      f" {'✓' if r['debuts_this_season'] else ''} | {t} |"
                      f" {'✓' if r['has_card'] else ''} |")
        rest = [r for r in srows if r["season_chapters"] < SCENE_KEY]
        if rest:
            md.append(f"\n### 一次性场景（{len(rest)}）\n")
            md.append("　".join(f"{r['name']}({r['season_chapters']})" for r in rest))
            md.append("")

    amb_used = sorted({r["name"] for s in out for r in s["characters"] if r["ambiguous"]})

    # 存疑名里混着两类完全不同的东西，分开报才有可操作性。
    # 排序键都带名字做次键：并列项的顺序不能依赖 set 迭代序，否则每次重跑 md 都有无意义的 diff。
    alias_of = {c["canonical_name"]: set(c.get("aliases") or []) for c in cidx}
    # 互相把对方列为别名的两个主名：s4 看出了关系却没并，多半是同一人被拆成两条
    same_person = sorted(
        {tuple(sorted((a, b))) for a in alias_of for b in alias_of[a]
         if b in alias_of and a != b and a in alias_of[b]},
        key=lambda p: (not (p[0] in p[1] or p[1] in p[0]),
                       -(g_char[p[0]]["chapter_count"] + g_char[p[1]]["chapter_count"]),
                       p[0], p[1]))
    name_is_scene = sorted(set(alias_of) & set(g_scene),
                           key=lambda n: (-(g_char[n]["chapter_count"]), n))
    flagged = {n for p in same_person for n in p} | set(name_is_scene)
    generic = [n for n in amb_used if n not in flagged]
    md.append("\n---\n\n## 口径\n")
    md.append(f"- **本季章数**：该名字在本季 seq 区间内出场的章数，按章去重，"
              f"`mentioned_only`（只被提到没出场）不算。")
    md.append(f"- **全书章数**：`data/characters/index.json` 的 `chapter_count`，"
              f"覆盖 1~1200 章。**本季章数大于全书章数说明归属有问题**，正常不会出现。")
    md.append(f"- **档位**：本季 ≥{MAJOR} 章为主要，{MINOR}~{MAJOR-1} 章为次要，其余龙套。")
    md.append(f"- **本季首现**：这个角色/场景在**全书**里第一次出现就落在本季，"
              f"即本季要从零做它的资产。")
    md.append(f"- **资产卡**：`production/characters/` 或 `production/scenes/` 里有同名目录且母版带提示词。")
    md.append(f"- **深度档案**：`production/characters/_深度档案/` 里有同名档案。")
    md.append(f"- **归属判定**：人工核实（`common/names.PRESETS`）> "
              f"索引里唯一认领的别名 > 不并。详见本脚本头部注释。\n")
    md.append(f"## 已知问题\n")
    md.append(f"**{len(amb_used)} 个名字归属存疑**，本表一律按字面保留、"
              f"没有并到任何人头上，「存疑」列给出了认领它的主名。\n")
    md.append(f"这 {len(amb_used)} 个里混着三类完全不同的东西，分开看才好动手。\n")

    strong = [p for p in same_person if p[0] in p[1] or p[1] in p[0]]
    md.append(f"\n### A. 疑似同人异名，可能该合并（{len(same_person)} 对）\n")
    md.append(f"两个主名**互相把对方列为别名**——`s4` 看出了关系却没有并，"
              f"所以现在很可能是**重复计数**的。\n")
    md.append(f"其中 {len(strong)} 对**一个是另一个的子串**，"
              f"几乎必然是同一人，标为「高」；其余标「待核」。\n")
    if same_person:
        md.append("| 置信 | 主名甲 | 全书章数 | 主名乙 | 全书章数 |")
        md.append("| :-: | --- | ---: | --- | ---: |")
        for a, b in same_person:
            conf = "高" if (a in b or b in a) else "待核"
            md.append(f"| {conf} | {a} | {g_char[a]['chapter_count']} |"
                      f" {b} | {g_char[b]['chapter_count']} |")
        md.append("\n**怎么修**：给 `pipeline/common/names.py` 的 `PRESETS` 补一条人工核实的身份，"
                  "然后重跑 s4 → s9 → s11 → s13。不要直接改 `index.json`——它是 `s4` 的产物，重跑就没了。")
    else:
        md.append("（无）")

    md.append(f"\n\n### B. 疑似把地点抽成了人（{len(name_is_scene)} 个）\n")
    md.append("这些名字**同时出现在角色索引和场景索引里**。"
              "多半是逐章分析把一个地方当成了人物，需要人工确认后从角色侧剔除。\n")
    md.append("　".join(f"{n}({g_char[n]['chapter_count']}章)" for n in name_is_scene)
              if name_is_scene else "（无）")

    md.append(f"\n\n### C. 其余存疑（{len(generic)} 个）\n")
    md.append("**以泛称为主**——「老人」「女人」「少年」这类，"
              "不同章节指的本来就是不同的人（`doc/09` 讲别名归并的一节记过这个坑）。"
              "它们留在清单里是**正确**的，但做资产时要按场次逐个确认指谁。\n")
    md.append("**但这一堆里也混着真名字**（被两个以上主名认领，却不构成 A 的互认配对）。"
              "看到眼熟的名字要单独查一下，别当成泛称放过去。\n")
    md.append("　".join(generic) if generic else "（无）")

    write_json(args.out_json, {"meta": {"seasons": len(out),
                                        "major_min": MAJOR, "minor_min": MINOR,
                                        "ambiguous_names": len(amb_used)},
                               "seasons": out})
    args.out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{len(out)} 季")
    for s in out:
        print(f"  第{s['season']}季《{s['name']}》 {s['episode_start']}–{s['episode_end']}"
              f"  角色 {s['character_count']:>3}（已建卡 {sum(1 for r in s['characters'] if r['has_card'])}）"
              f"  场景 {s['scene_count']:>3}（已建卡 {sum(1 for r in s['scenes'] if r['has_card'])}）")
    print(f"\n归属存疑的名字 {len(amb_used)} 个"
          f"（已按字面保留，未并入任何人）：{'、'.join(amb_used[:10])}"
          f"{' …' if len(amb_used) > 10 else ''}")
    print(f"\n{args.out_json.relative_to(paths.ROOT)}")
    print(f"{args.out_md.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
