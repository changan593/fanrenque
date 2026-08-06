#!/usr/bin/env python3
"""
步骤 4：把 1200 份逐章分析聚合成角色资产与场景资产。**不调 API。**

这是阶段三的主力脚本。逐章分析一跑完，跑一次它就能拿到：

  data/characters/index.json      角色总表（按戏份排序）
  data/characters/cNNN_名字.json  单角色资产：别名、出场章节、原文描写汇总、
                                  状态变化、行为、同框关系、常驻场景
  data/scenes/index.json          场景总表
  data/scenes/sNNN_名字.json      单场景资产：原文描写汇总、到过的人、时段
  data/plot/cooccurrence.json     角色同框矩阵，用于理清人物关系网

设计要点：

1. **原文描写逐字汇总**。角色的 appearance_quotes、场景的 description_quotes
   全部带章节号原样收进来，一个字不改。阶段四写角色/场景提示词时，
   这些就是唯一依据——「原文怎么描写就怎么呈现」靠的就是它。

2. **别名归并**。同一个人在不同章可能叫「唐真」「三只眼」。用并查集把
   name↔aliases 连起来归成一个角色。但归并有风险：「老人」「那人」这类泛称
   会把不同人误并成一个。所以泛称不参与归并，可疑簇会被标记出来交人工复核，
   绝不静默合并。

3. **对残缺数据容忍**。真实模型输出难免有字段缺失或类型不对，
   这里一律跳过并计数，不让一章的脏数据把整次聚合搞崩。

用法：
    python pipeline/s4_build_assets.py
    python pipeline/s4_build_assets.py --min-chapters 3   # 只保留出场≥3章的角色
    python pipeline/s4_build_assets.py --no-merge         # 不做别名归并
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import read_json, write_json

# 泛称：不参与别名归并，否则会把不同人误并成一个
GENERIC = set("""老人 女人 男人 那人 此人 众人 有人 少年 少女 中年 青年 老者 老头 老妇
孩子 童子 弟子 长老 掌门 宫主 城主 护卫 侍女 丫鬟 和尚 道人 书生 乞丐 掌柜 伙计
对方 双方 两人 三人 几人 大家 旁人 路人 某人 他 她 它 你 我""".split())

SLUG_BAD = re.compile(r'[\\/:*?"<>|\s]')


def slug(name: str, idx: int, prefix: str) -> str:
    return f"{prefix}{idx:03d}_{SLUG_BAD.sub('_', name)[:20]}"


class Union:
    """并查集：把 name 和它的各个别名连成同一个角色。"""

    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def load_chapters() -> tuple[list[dict], list[str]]:
    docs, problems = [], []
    for p in sorted(paths.CHAPTERS_DIR.glob("ch*.json")):
        try:
            d = read_json(p)
        except Exception as e:
            problems.append(f"{p.name} 读取失败：{type(e).__name__}")
            continue
        if not isinstance(d.get("analysis"), dict):
            problems.append(f"{p.name} 缺 analysis")
            continue
        docs.append(d)
    return docs, problems


def _items(analysis: dict, key: str) -> list[dict]:
    """安全取列表字段，脏数据直接跳过。"""
    v = analysis.get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _strs(obj: dict, key: str) -> list[str]:
    v = obj.get(key)
    return [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def build_alias_map(docs: list[dict], merge: bool) -> tuple[dict[str, str], list[dict]]:
    """
    返回 (任意称呼 -> 归并后的主名, 可疑簇列表)。

    主名选取原则：簇内出现章节数最多的那个称呼。这样「唐真」会赢过「三只眼」，
    符合直觉。
    """
    seen = Counter()                     # 称呼 -> 出现章节数
    uf = Union()
    for d in docs:
        for ch in _items(d["analysis"], "characters"):
            name = (ch.get("name") or "").strip()
            if not name:
                continue
            seen[name] += 1
            uf.find(name)
            if not merge:
                continue
            for a in _strs(ch, "aliases"):
                a = a.strip()
                # 泛称不参与归并：它们会把不相干的人连成一团
                if not a or a in GENERIC or name in GENERIC:
                    continue
                seen[a] += 0
                uf.union(name, a)

    clusters: dict[str, list[str]] = defaultdict(list)
    for n in list(uf.parent):
        clusters[uf.find(n)].append(n)

    alias_map, suspicious = {}, []
    for members in clusters.values():
        canon = max(members, key=lambda n: (seen.get(n, 0), len(n)))
        for m in members:
            alias_map[m] = canon
        # 一个簇里出现多个「本身就很常见」的称呼，说明可能误并了
        strong = [m for m in members if seen.get(m, 0) >= 10]
        if len(strong) > 1:
            suspicious.append({"canonical": canon, "members": sorted(members),
                               "strong_members": sorted(strong),
                               "reason": "簇内有多个高频称呼，可能把不同角色并到了一起"})
    return alias_map, suspicious


def build_characters(docs: list[dict], alias_map: dict[str, str]) -> list[dict]:
    acc: dict[str, dict] = {}
    for d in docs:
        seq = d.get("seq")
        for ch in _items(d["analysis"], "characters"):
            raw = (ch.get("name") or "").strip()
            if not raw:
                continue
            canon = alias_map.get(raw, raw)
            a = acc.setdefault(canon, {
                "canonical_name": canon, "aliases": Counter(), "chapters": set(),
                "roles": Counter(), "appearance_quotes": [], "states": [],
                "actions": [], "mentioned_only_count": 0,
            })
            a["chapters"].add(seq)
            if raw != canon:
                a["aliases"][raw] += 1
            for al in _strs(ch, "aliases"):
                if al != canon:
                    a["aliases"][al] += 1
            role = ch.get("role_in_chapter")
            if isinstance(role, str):
                a["roles"][role] += 1
            if ch.get("mentioned_only") is True:
                a["mentioned_only_count"] += 1
            # 原文描写逐字收集 —— 阶段四写提示词的唯一依据
            for q in _strs(ch, "appearance_quotes"):
                a["appearance_quotes"].append({"seq": seq, "quote": q})
            st = ch.get("state")
            if isinstance(st, str) and st.strip():
                a["states"].append({"seq": seq, "state": st.strip()})
            acts = _strs(ch, "actions")
            if acts:
                a["actions"].append({"seq": seq, "actions": acts})

    out = []
    for a in acc.values():
        chapters = sorted(x for x in a["chapters"] if isinstance(x, int))
        out.append({
            "canonical_name": a["canonical_name"],
            "aliases": [n for n, _ in a["aliases"].most_common()],
            "first_seq": chapters[0] if chapters else None,
            "last_seq": chapters[-1] if chapters else None,
            "chapter_count": len(chapters),
            "chapters": chapters,
            "role_distribution": dict(a["roles"]),
            "mentioned_only_count": a["mentioned_only_count"],
            "appearance_quotes": a["appearance_quotes"],
            "states": a["states"],
            "actions": a["actions"],
        })
    out.sort(key=lambda c: (-c["chapter_count"], c["first_seq"] or 0))
    return out


def build_scenes(docs: list[dict], alias_map: dict[str, str]) -> list[dict]:
    acc: dict[str, dict] = {}
    for d in docs:
        seq = d.get("seq")
        for sc in _items(d["analysis"], "scenes"):
            name = (sc.get("name") or "").strip()
            if not name:
                continue
            a = acc.setdefault(name, {
                "name": name, "chapters": set(), "types": Counter(),
                "times": Counter(), "description_quotes": [],
                "characters": Counter(), "events": [],
            })
            a["chapters"].add(seq)
            if isinstance(sc.get("type"), str):
                a["types"][sc["type"]] += 1
            if isinstance(sc.get("time_of_day"), str) and sc["time_of_day"].strip():
                a["times"][sc["time_of_day"].strip()] += 1
            for q in _strs(sc, "description_quotes"):
                a["description_quotes"].append({"seq": seq, "quote": q})
            for p in _strs(sc, "present_characters"):
                a["characters"][alias_map.get(p, p)] += 1
            ev = _strs(sc, "events")
            if ev:
                a["events"].append({"seq": seq, "events": ev})

    out = []
    for a in acc.values():
        chapters = sorted(x for x in a["chapters"] if isinstance(x, int))
        out.append({
            "name": a["name"],
            "first_seq": chapters[0] if chapters else None,
            "chapter_count": len(chapters),
            "chapters": chapters,
            "types": dict(a["types"]),
            "times_of_day": dict(a["times"]),
            "description_quotes": a["description_quotes"],
            "characters": dict(a["characters"].most_common()),
            "events": a["events"],
        })
    out.sort(key=lambda s: (-s["chapter_count"], s["first_seq"] or 0))
    return out


def build_cooccurrence(docs: list[dict], alias_map: dict[str, str]) -> dict:
    """同框统计：同一章里一起出现过多少次。人物关系网的基础。"""
    pair = Counter()
    for d in docs:
        names = sorted({alias_map.get((c.get("name") or "").strip(),
                                      (c.get("name") or "").strip())
                        for c in _items(d["analysis"], "characters")
                        if (c.get("name") or "").strip()})
        for i, x in enumerate(names):
            for y in names[i + 1:]:
                pair[(x, y)] += 1
    return {"pairs": [{"a": a, "b": b, "chapters": n}
                      for (a, b), n in pair.most_common(3000)]}


def main() -> None:
    ap = argparse.ArgumentParser(description="聚合角色与场景资产（不调 API）")
    ap.add_argument("--min-chapters", type=int, default=1,
                    help="出场章节数低于此值的角色/场景不单独出文件（仍留在总表）")
    ap.add_argument("--no-merge", action="store_true", help="不做别名归并")
    args = ap.parse_args()

    paths.ensure_dirs()
    docs, problems = load_chapters()
    if not docs:
        print(f"{paths.CHAPTERS_DIR.relative_to(paths.ROOT)} 里没有可用的章节分析。")
        print("请先跑：python pipeline/s2_analyze_chapters.py")
        return

    alias_map, suspicious = build_alias_map(docs, merge=not args.no_merge)
    characters = build_characters(docs, alias_map)
    scenes = build_scenes(docs, alias_map)
    cooc = build_cooccurrence(docs, alias_map)

    # 清掉上一轮的单体文件，避免改名后留下孤儿
    for d, pat in ((paths.CHARACTERS_DIR, "c*.json"), (paths.SCENES_DIR, "s*.json")):
        for old in d.glob(pat):
            old.unlink()

    for i, c in enumerate(characters, 1):
        c["id"] = slug(c["canonical_name"], i, "c")
        if c["chapter_count"] >= args.min_chapters:
            write_json(paths.CHARACTERS_DIR / f"{c['id']}.json", c)
    for i, s in enumerate(scenes, 1):
        s["id"] = slug(s["name"], i, "s")
        if s["chapter_count"] >= args.min_chapters:
            write_json(paths.SCENES_DIR / f"{s['id']}.json", s)

    light = ["id", "canonical_name", "aliases", "first_seq", "last_seq",
             "chapter_count", "role_distribution"]
    write_json(paths.CHARACTERS_DIR / "index.json", {
        "built_from_chapters": len(docs),
        "character_count": len(characters),
        "alias_merge": not args.no_merge,
        "suspicious_merges": suspicious,
        "data_problems": problems,
        "characters": [{k: c[k] for k in light} for c in characters],
    })
    write_json(paths.SCENES_DIR / "index.json", {
        "built_from_chapters": len(docs),
        "scene_count": len(scenes),
        "scenes": [{k: s[k] for k in ("id", "name", "first_seq",
                                      "chapter_count", "types")} for s in scenes],
    })
    write_json(paths.PLOT_DIR / "cooccurrence.json", cooc)

    print(f"读入章节分析 {len(docs)} 份" + (f"（{len(problems)} 份有问题）" if problems else ""))
    print(f"角色 {len(characters)} 个 | 场景 {len(scenes)} 个 | "
          f"同框关系 {len(cooc['pairs'])} 对")
    print(f"\n戏份最重的 10 个角色：")
    for c in characters[:10]:
        al = f"（{'、'.join(c['aliases'][:3])}）" if c["aliases"] else ""
        print(f"  {c['canonical_name']}{al} 出场 {c['chapter_count']} 章，"
              f"seq {c['first_seq']}~{c['last_seq']}，"
              f"原文描写 {len(c['appearance_quotes'])} 条")
    print(f"\n出现最多的 10 个场景：")
    for s in scenes[:10]:
        print(f"  {s['name']} {s['chapter_count']} 章，原文描写 {len(s['description_quotes'])} 条")
    if suspicious:
        print(f"\n⚠ {len(suspicious)} 组别名归并可疑，需人工复核"
              f"（详见 characters/index.json 的 suspicious_merges）：")
        for s in suspicious[:5]:
            print(f"  {s['canonical']} ← {'、'.join(s['strong_members'])}")
    if problems:
        print(f"\n⚠ {len(problems)} 份章节数据有问题：{problems[:3]}")
    print(f"\n产物：{paths.CHARACTERS_DIR.relative_to(paths.ROOT)}/、"
          f"{paths.SCENES_DIR.relative_to(paths.ROOT)}/、"
          f"{(paths.PLOT_DIR / 'cooccurrence.json').relative_to(paths.ROOT)}")


if __name__ == "__main__":
    main()
