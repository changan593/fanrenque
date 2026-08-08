#!/usr/bin/env python3
"""
步骤 13：按季汇总「这一季涉及哪些角色、哪些场景」。**不调 API。**

`s11` 回答的是「每一集要什么」，这一步回答上一级的问题：
**开一季的工，一共要备多少资产、哪些是重头、哪些只是路过。**
它是阶段四 4.2／4.3（角色与场景提示词深化）的工作队列。

## 为什么不直接汇总 episode_assets.json

因为那份数据的角色归属有个已知缺陷，直接汇总会错得很难看。

`s11` 把 `data/characters/index.json` 的别名表拉平成一个
`别名 → 主名` 的字典，后写覆盖先写。但这张别名表里有 **305 个歧义名**：
212 个被两个以上主名同时认领，155 个本身又是别的主名。
拉平之后，谁覆盖谁只取决于索引顺序。

后果是实打实的：`红儿` 被 `姚望舒`（380 章）和 `师姐`（26 章）同时认领，
拉平后落到了 `师姐` 头上——于是 `episode_assets.json` 里
**S01E01 的角色表有「师姐」而没有姚望舒**，而姚望舒是全书第二主角。
前 200 章里 31.5% 的角色出现都落在歧义名上。

`s4` 在**归并那一步**是设了防线的（泛称不参与归并、闸门建在簇上），
但 `s11` 事后把别名表当查找表再用一次，那道防线管不到这里。

## 这一步怎么定归属

三级，优先级从高到低，**级别不够就不并**：

| 级 | 依据 | 例 |
| --- | --- | --- |
| 1 | `s8` 的 `PRESETS`（14 份人工逐字核实过的身份） | `红儿 → 姚望舒` |
| 2 | 索引里**只有一个主名认领**的别名 | `三只眼 → 唐真` |
| 3 | 多方认领 / 本身也是主名 / 人工标了存疑 | `师姐` 原样保留，标存疑 |

第三级**不猜**。宁可清单里多出一个「师姐」让你自己判，
也不要悄悄把它算到某个人头上——那正是现在这个缺陷的来源。

用法：
    python pipeline/s13_season_roster.py              # 六季全出
    python pipeline/s13_season_roster.py --season 1   # 只出第一季的 md
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import read_json, write_json
from s8_character_dossier import PRESETS

MAJOR, MINOR = 10, 3          # 本季章数 ≥10 主要，3~9 次要，其余龙套
SCENE_KEY = 3                 # 场景 ≥3 章算重点


def build_resolver(index_chars):
    """别名 → 主名。返回 (resolve, ambiguity) —— resolve(name) -> (归属, 是否存疑)。"""
    canon = {c["canonical_name"] for c in index_chars}

    claims: dict[str, list[str]] = defaultdict(list)
    for c in index_chars:
        for a in c.get("aliases") or []:
            claims[a].append(c["canonical_name"])

    preset_map, preset_block = {}, set()
    for p in PRESETS.values():
        for a in p["aliases"]:
            preset_map[a] = p["canonical"]
        preset_block.update(p.get("ambiguous") or [])
        preset_block.update(p.get("exclude") or [])

    ambiguity = {a: sorted(set(o)) for a, o in claims.items()
                 if len(set(o)) > 1 or a in canon}

    def resolve(name: str) -> tuple[str, bool]:
        if name in preset_block:            # 人工明说过它跨人，别并
            return name, True
        if name in preset_map:              # 人工核实过的身份，最高优先
            return preset_map[name], False
        if name in canon:                   # 它自己就是主名，优先算它自己
            return name, name in ambiguity
        owners = sorted(set(claims.get(name, [])))
        if len(owners) == 1:
            return owners[0], False
        if len(owners) > 1:                 # 多方认领，不猜
            return name, True
        return name, False                  # 索引里没有，按字面

    return resolve, ambiguity


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


def main() -> int:
    ap = argparse.ArgumentParser(description="按季汇总角色与场景清单")
    ap.add_argument("--seasons", type=Path, default=paths.PLOT_DIR / "seasons.json")
    ap.add_argument("--episodes", type=Path, default=paths.PLOT_DIR / "episodes.json")
    ap.add_argument("--season", type=int, help="md 只输出这一季（json 始终全出）")
    ap.add_argument("--out-json", type=Path, default=paths.PLOT_DIR / "season_roster.json")
    ap.add_argument("--out-md", type=Path, default=paths.PLOT_DIR / "season_roster.md")
    args = ap.parse_args()

    for f in (args.seasons, args.episodes):
        if not f.exists():
            raise SystemExit(f"缺 {f}，先跑 s10_episode_plan.py")

    seasons = read_json(args.seasons)
    eps = read_json(args.episodes)["episodes"]
    cidx = read_json(paths.CHARACTERS_DIR / "index.json")["characters"]
    sidx = read_json(paths.SCENES_DIR / "index.json")["scenes"]

    resolve, ambiguity = build_resolver(cidx)
    g_char = {c["canonical_name"]: c for c in cidx}
    g_scene = {s["name"]: s for s in sidx}

    # seq -> 集号
    seq2code = {}
    for e in eps:
        for s in range(e["seq_start"], e["seq_end"] + 1):
            seq2code[s] = e["code"]

    # 已有的人工产物
    dossiers = {p.name.split("_角色深度档案")[0]
                for p in paths.CHARACTERS_DIR.glob("*_角色深度档案.md")}
    def carded(md: Path, prefix: str) -> set[str]:
        if not md.exists():
            return set()
        out = set()
        for line in md.read_text(encoding="utf-8").splitlines():
            if line.startswith("# ") and line[2:].startswith(prefix):
                out.add(line[2:].split(maxsplit=1)[-1].split("（")[0].strip(" ★"))
        return out
    c_cards = carded(paths.PRODUCTION_DIR / "s01" / "02_角色资产.md", "C")
    s_cards = carded(paths.PRODUCTION_DIR / "s01" / "03_场景资产.md", "S")

    out, md = [], []
    md.append("# 各季资产清单（角色与场景）\n")
    md.append("由 `pipeline/s13_season_roster.py` 生成，**不要手改**——"
              "改了会被下次重跑覆盖。\n")
    md.append("口径与已知问题见文末。\n")

    for i, s in enumerate(seasons, 1):
        lo, hi = s["seq_start"], s["seq_end"]
        chars, scenes = collect(lo, hi, resolve)
        codes = sorted({seq2code[q] for q in range(lo, hi + 1) if q in seq2code})

        def rows(tbl, gidx, cards, is_char):
            r = []
            for name, v in sorted(tbl.items(), key=lambda kv: (-kv[1]["chapters"], kv[0])):
                g = gidx.get(name) or {}
                r.append({
                    "name": name,
                    "season_chapters": v["chapters"],
                    "book_chapters": g.get("chapter_count"),
                    "first_seq": v["first_seq"],
                    "first_episode": seq2code.get(v["first_seq"]),
                    "debuts_this_season": g.get("first_seq") is not None
                                          and lo <= g["first_seq"] <= hi,
                    "tier": tier(v["chapters"]),
                    **({"ambiguous": v["doubt"],
                        "claimed_by": ambiguity.get(name) if v["doubt"] else None,
                        "has_dossier": name in dossiers,
                        "has_card": name in cards}
                       if is_char else
                       {"types": g.get("types") or {}, "has_card": name in cards}),
                })
            return r

        crows = rows(chars, g_char, c_cards, True)
        srows = rows(scenes, g_scene, s_cards, False)
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
                  f"{sum(1 for r in srows if r['season_chapters']>=SCENE_KEY)} 个）\n")

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
            md.append("出场 1~2 章。资产卡按 `doc/04` 4.1 用「特征卡」处理即可，"
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

    # 存疑名里混着两类完全不同的东西，分开报才有可操作性
    alias_of = {c["canonical_name"]: set(c.get("aliases") or []) for c in cidx}
    # 互相把对方列为别名的两个主名：s4 看出了关系却没并，多半是同一人被拆成两条
    same_person = sorted(
        {tuple(sorted((a, b))) for a in alias_of for b in alias_of[a]
         if b in alias_of and a != b and a in alias_of[b]},
        key=lambda p: (not (p[0] in p[1] or p[1] in p[0]),
                       -(g_char[p[0]]["chapter_count"] + g_char[p[1]]["chapter_count"])))
    name_is_scene = sorted(set(alias_of) & set(g_scene),
                           key=lambda n: -(g_char[n]["chapter_count"]))
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
    md.append(f"- **归属判定**：人工核实（`s8` 的 PRESETS，14 份）> "
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
        md.append("\n**怎么修**：给 `s8` 的 `PRESETS` 补一条人工核实的身份，"
                  "然后重跑本脚本。不要直接改 `index.json`——它是 `s4` 的产物，重跑就没了。")
    else:
        md.append("（无）")

    md.append(f"\n\n### B. 疑似把地点抽成了人（{len(name_is_scene)} 个）\n")
    md.append("这些名字**同时出现在角色索引和场景索引里**。"
              "多半是逐章分析把一个地方当成了人物，需要人工确认后从角色侧剔除。\n")
    md.append("　".join(f"{n}({g_char[n]['chapter_count']}章)" for n in name_is_scene)
              if name_is_scene else "（无）")

    md.append(f"\n\n### C. 其余存疑（{len(generic)} 个）\n")
    md.append("**以泛称为主**——「老人」「女人」「少年」这类，"
              "不同章节指的本来就是不同的人，`doc/10` 5.4 记过这个坑。"
              "它们留在清单里是**正确**的，但做资产时要按场次逐个确认指谁。\n")
    md.append("**但这一堆里也混着真名字**（被两个以上主名认领，却不构成 A 的互认配对）。"
              "看到眼熟的名字要单独查一下，别当成泛称放过去。\n")
    md.append("　".join(generic) if generic else "（无）")

    md.append(f"\n\n### D. `episode_assets.json` 的角色列不可信\n")
    md.append(f"**它有同类缺陷且更严重**"
              f"——它把别名表拉平成字典、后写覆盖，没有上面这三级判定。"
              f"典型后果是 `红儿` 被算成 `师姐` 而不是 `姚望舒`。"
              f"排产看每集需求时请以本表为准，或先修 `s11`。\n")

    write_json(args.out_json, {"meta": {"seasons": len(out),
                                        "major_min": MAJOR, "minor_min": MINOR,
                                        "ambiguous_names": len(amb_used)},
                               "seasons": out})
    args.out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{len(out)} 季")
    for s in out:
        print(f"  第{s['season']}季《{s['name']}》 {s['episode_start']}–{s['episode_end']}"
              f"  角色 {s['character_count']:>3}  场景 {s['scene_count']:>3}")
    print(f"\n归属存疑的名字 {len(amb_used)} 个"
          f"（已按字面保留，未并入任何人）：{'、'.join(amb_used[:10])}"
          f"{' …' if len(amb_used) > 10 else ''}")
    print(f"\n{args.out_json.relative_to(paths.ROOT)}")
    print(f"{args.out_md.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
