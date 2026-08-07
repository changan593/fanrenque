#!/usr/bin/env python3
"""
步骤 8：把某个角色在全书里的所有相关段落，连同上下文，收成一份卷宗。**不调 API。**

写角色文档之前必须先做这一步。直接凭印象写人物，等于把没验证过的东西
往下游传——违反原则二（严格遵守原文）。这个脚本只做一件事：
**把证据一条不漏地摆出来，并且每一条都带着章节号和段号**，
之后的分析只能在这份卷宗上做，不能凭空添。

产出两份：

    data/characters/dossier_<名>.json    完整卷宗，机器读
    data/characters/dossier_<名>.md      阅读稿，人读

## 三个设计要点

1. **别名分三档**，不是一锅烩：
   - `aliases`   确定是本人（红儿 / 姚红儿 / 姚望舒）
   - `ambiguous` 可能是本人也可能是别人，单独收、单独标（姚姑娘 在前期
     指姚安饶，后期才可能指姚望舒）。静默并进去就会把两个人的戏搅成一个人。
   - `exclude`   包含别名但另有所指的词，直接排除

2. **最长匹配优先**。「姚红儿」里含「红儿」、「姚望舒」里含「望舒」，
   逐个别名各扫一遍会把同一处算好几次，统计就废了。

3. **上下文窗口**。只截命中段会丢掉「谁在跟她说话、她在回应什么」，
   前后各带 N 段才能还原场景。窗口段落带 `hit` 标记，区分证据与背景。

用法：
    python pipeline/s8_character_dossier.py --preset 姚望舒
    python pipeline/s8_character_dossier.py --name 某某 --aliases 甲,乙 --window 3
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import write_json
from common.novel import load_novel

# 预置角色。别名边界是查过原文才敢写的，理由记在 note 里。
PRESETS = {
    "姚望舒": {
        "canonical": "姚望舒",
        "aliases": ["姚望舒", "姚红儿", "红儿", "望舒", "小红儿"],
        "ambiguous": ["姚姑娘", "姚小姐"],
        "note": "身份链：红儿（丫鬟，seq2 首现，无姓）→ 姚红儿（被赐姓，"
                "seq22 姚安饶当面直呼）→ 姚望舒（seq320 改名，"
                "「那我看，你就叫望舒吧！姚望舒！」）。"
                "「姚姑娘」是跨人歧义：seq18 唐真对姚姑娘说「我与红儿只是好友」，"
                "该处指姚安饶而非本人；后期才可能指姚望舒，故单列存疑。"
                "姚安饶是另一个人（同姓），全书 2129 次，不并入。",
    },
    "唐真": {
        "canonical": "唐真",
        "aliases": ["唐真", "求法真君", "真君", "唐苟安", "苟安", "狗安",
                    "三只眼", "小乞丐", "唐小友", "唐先生"],
        "ambiguous": ["大师兄"],
        "note": "主角。「真君」查过：全书「X真君」的名号前缀只有「求法」112 次，"
                "其余全是「这位/那位/还请」这类指示词，故 真君 可确定为本人。"
                "「三只眼」源自额头竖椭圆黑印，「狗安/苟安/唐苟安」是化名，"
                "红儿一直叫他狗安。",
    },
    "齐渊": {
        "canonical": "齐渊",
        "aliases": ["齐渊", "人魔尊", "落魄书生", "北山"],
        "ambiguous": ["魔尊"],
        "note": "全书大反派。「齐渊！！人魔尊！」（seq8）、「人魔尊齐渊」（seq17/162）"
                "可证同一人。但**裸「魔尊」不并入**：seq198「他是魔尊中排行第二的"
                "人魔尊」说明魔尊有多位，全书 2236 次里大量指别人。",
    },
    "老拐子": {
        "canonical": "老拐子",
        "aliases": ["老拐子", "拐子"],
        "ambiguous": [],
        "note": "开篇的老乞丐。「拐子」196 次中 192 次即「老拐子」，裸用极少。",
    },
    "南季礼": {
        "canonical": "南季礼",
        "aliases": ["南季礼", "紫华圣人", "紫华", "紫云仙宫宫主"],
        "ambiguous": [],
        "note": "唐真的师父。seq94「一位是宫主，紫华圣人南季礼」、"
                "seq1012「紫华圣人南季礼，紫云仙宫宫主」可证三名一人。"
                "「紫华X」另有 紫华道人13 / 紫华道长6，应为同人早期称呼；"
                "「紫华观」1 次是地名，量小可忽略。",
    },
    "葛道人": {
        "canonical": "葛道人",
        "aliases": ["葛道人"],
        "ambiguous": [],
        "note": "⚠ 葛道人**不是**执法堂长老，是两个人，详见二者档案的考据节。"
                "葛道人是紫云仙宫六位准圣之首（seq476），"
                "seq387 起登场，为人和善，对唐真是回护态度。",
    },
    "执法堂长老": {
        "canonical": "执法堂长老",
        "aliases": ["执法堂长老"],
        "ambiguous": ["执法堂"],
        "note": "⚠ 与葛道人**不是**同一人。本人只出现在 seq1/5/61/116，"
                "是开篇宣判唐真、并被唐真偷袭放倒的紫云仙宫长老，"
                "seq61 明写「恨不得师兄自生自灭」；而葛道人 seq388 说"
                "「他能再次开始修行，已是万幸」，态度相反。全书未给他名字。",
    },
    "南红枝": {
        "canonical": "南红枝",
        "aliases": ["南红枝", "红枝"],
        "ambiguous": [],
        "note": "南季礼之女。经由桃花面螺生而来（seq1166），"
                "被南季礼「直接掐断了命河」。",
    },
    "姚安饶": {
        "canonical": "姚安饶",
        "aliases": ["姚安饶", "姚安恕"],
        "ambiguous": ["姚姑娘", "大小姐", "小姐"],
        "note": "北阳城城主之女，红儿的小姐与义姐，后出家法号姚安恕（seq219 起）。"
                "**「二小姐」不是她而是红儿**（seq19「你如今是城主府的二小姐」、"
                "「轮不到红儿这个未出阁的二小姐」），故排除。"
                "「姚姑娘」前期指她、后期可能指姚望舒，列为存疑。",
    },
    "姜羽": {
        "canonical": "姜羽",
        "aliases": ["姜羽", "羽儿", "小羽", "姜师妹"],
        "ambiguous": ["师妹"],
        "note": "唐真的师妹，南季礼之徒，南红枝的师妹。"
                "「师妹」133 次是跨人歧义——同时可能指南红枝，故单列存疑。",
    },
    "尉天齐": {
        "canonical": "尉天齐",
        "aliases": ["尉天齐", "天齐", "尉师弟"],
        "ambiguous": [],
        "exclude": ["寿与天齐", "与天齐", "齐天"],
        "note": "被各方推出来取代唐真的少年（seq245）。"
                "⚠ 裸「天齐」首现于 seq164 的成语「寿与天齐」，"
                "早于本人登场（seq236）72 章，故必须排除该词组，"
                "否则会把成语当成人物出场。",
    },
    "王玉屏": {
        "canonical": "王玉屏",
        "aliases": ["王玉屏", "屏姐"],
        "ambiguous": ["山主", "小师妹"],
        "exclude": ["玉屏山", "玉屏观", "玉屏峰"],
        "note": "玉屏山山主。⚠ 两个坑："
                "①「玉屏」889 次里 768 次是地名（玉屏山 522 / 玉屏观 246），必须排除；"
                "②**「小胖」不是她**，是同门吴师兄（seq70「别叫什么吴师兄，叫我小胖就行」，"
                "「一个穿着常服的小胖子，面相憨厚」），全书 307 次，绝不并入。"
                "「山主」85 次未必都指她，列为存疑。",
    },
    "魏成": {
        "canonical": "魏成",
        "aliases": ["魏成", "魏师兄"],
        "ambiguous": [],
        "note": "玉蟾宫遗孤，把白玉珠交给红儿的人，望舒宫的实际操盘者。",
    },
    "吕藏锋": {
        "canonical": "吕藏锋",
        "aliases": ["吕藏锋", "藏锋", "吕师兄"],
        "ambiguous": [],
        "exclude": ["藏锋于袖", "藏锋于", "不露藏锋"],
        "note": "⚠ 裸「藏锋」首现于 seq77 的「藏锋于袖中，出之则见血」，"
                "是形容剑而非人名，必须排除。",
    },
}

# 抽取用的关键词。命中即打标签，方便写文档时按主题捡证据。
TAGS = {
    "外貌": ["容貌", "脸", "眉", "眼", "唇", "发", "手", "身段", "衣", "裙", "钗",
             "簪", "指", "肌肤", "白皙", "个子", "身高", "模样", "长相", "打扮"],
    "情绪": ["笑", "哭", "泪", "怒", "怕", "慌", "急", "喜", "悲", "委屈", "心疼",
             "红了脸", "咬牙", "颤", "呆", "怔", "叹"],
    "修行": ["筑基", "练气", "金丹", "元婴", "修为", "境界", "灵根", "法力", "仙胎",
             "道基", "破境", "结丹", "神魂", "灵力"],
    "身份": ["丫鬟", "婢女", "侍女", "小姐", "姑娘", "身世", "出身", "姓", "名",
             "父亲", "母亲", "娘", "爹"],
    "关系": ["唐真", "姚安饶", "老拐子", "尉天齐", "姜羽", "吕藏锋", "齐渊",
             "南红枝", "程百尺", "闻人哭"],
}

QUOTE_RE = re.compile(r"[“\"]([^“”\"]{1,})")


def build_matcher(aliases: list[str]):
    """最长匹配优先，避免「姚红儿」被「红儿」重复计数。"""
    ordered = sorted(set(aliases), key=len, reverse=True)
    return re.compile("|".join(re.escape(a) for a in ordered)) if ordered else None


def build_excluder(patterns: list[str]):
    """
    含别名但另有所指的词组。不排掉就会把成语和地名当成人物出场：
    「寿与天齐」不是尉天齐、「藏锋于袖」不是吕藏锋、
    「玉屏山」「玉屏观」是地名不是王玉屏。
    """
    ordered = sorted(set(patterns), key=len, reverse=True)
    return re.compile("|".join(re.escape(p) for p in ordered)) if ordered else None


def scan(text: str, matcher, excluder=None) -> Counter:
    if not matcher:
        return Counter()
    if excluder:
        # 先把干扰词整体抹成等长占位，再匹配。
        # 抹成等长是为了不打乱后面按位置做的判断（比如台词在名字前还是后）。
        text = excluder.sub(lambda m: "　" * len(m.group(0)), text)
    return Counter(m.group(0) for m in matcher.finditer(text))


def tag_of(text: str) -> list[str]:
    return [t for t, words in TAGS.items() if any(w in text for w in words)]


def speech_in(text: str, matcher) -> list[dict]:
    """
    段内引号台词。是不是她说的，靠「别名出现在引号之前还是之后」粗判——
    中文小说里「X道：“……”」和「“……”X道」两种都常见，所以只给线索不下结论，
    最终由人看上下文判定。宁可标成待定，也不能替原文替角色分配台词。
    """
    out = []
    for m in QUOTE_RE.finditer(text):
        before, after = text[:m.start()], text[m.end():]
        hit_before = bool(matcher.search(before)) if matcher else False
        hit_after = bool(matcher.search(after)) if matcher else False
        out.append({
            "quote": m.group(1).strip(),
            "name_before_quote": hit_before,
            "name_after_quote": hit_after,
            "likely_speaker": ("很可能" if hit_before and not hit_after
                               else "可能" if hit_after else "待定"),
        })
    return out


def collect(units, canon: str, aliases: list[str], ambiguous: list[str],
            window: int, exclude: list[str] | None = None) -> dict:
    m_sure = build_matcher(aliases)
    m_amb = build_matcher(ambiguous)
    m_ex = build_excluder(exclude or [])

    chapters, tag_index = [], defaultdict(list)
    alias_total, alias_first = Counter(), {}
    amb_total = Counter()

    for u in units:
        paras = u["paragraphs"]
        hits, amb_hits = [], []
        for i, p in enumerate(paras):
            c = scan(p, m_sure, m_ex)
            if c:
                alias_total.update(c)
                for a in c:
                    alias_first.setdefault(a, {"seq": u["seq"], "para": i})
                tags = tag_of(p)
                hits.append({"para": i, "text": p, "aliases": dict(c), "tags": tags,
                             "speech": speech_in(p, m_sure)})
                for t in tags:
                    tag_index[t].append({"seq": u["seq"], "para": i})
            ca = scan(p, m_amb, m_ex)
            if ca and not c:          # 已确认命中的段不用再进存疑
                amb_total.update(ca)
                amb_hits.append({"para": i, "text": p, "aliases": dict(ca)})
        if not hits and not amb_hits:
            continue

        # 上下文窗口：命中段前后各 window 段，合并重叠区间
        keep = set()
        for h in hits + amb_hits:
            keep.update(range(max(0, h["para"] - window),
                              min(len(paras), h["para"] + window + 1)))
        hit_set = {h["para"] for h in hits}
        amb_set = {h["para"] for h in amb_hits}
        chapters.append({
            "seq": u["seq"], "title": u["title"],
            "hit_count": sum(sum(h["aliases"].values()) for h in hits),
            "hit_paras": [h["para"] for h in hits],
            "ambiguous_paras": [h["para"] for h in amb_hits],
            "hits": hits,
            "ambiguous": amb_hits,
            "context": [{"para": i, "text": paras[i],
                         "hit": i in hit_set, "ambiguous": i in amb_set}
                        for i in sorted(keep)],
        })

    dense = sorted(chapters, key=lambda c: -c["hit_count"])[:40]
    return {
        "character": canon,
        "aliases": aliases,
        "ambiguous_aliases": ambiguous,
        "stats": {
            "chapters_with_mention": len(chapters),
            "total_mentions": sum(alias_total.values()),
            "by_alias": dict(alias_total.most_common()),
            "alias_first_seen": alias_first,
            "ambiguous_by_alias": dict(amb_total.most_common()),
            "first_seq": chapters[0]["seq"] if chapters else None,
            "last_seq": chapters[-1]["seq"] if chapters else None,
            "densest_chapters": [{"seq": c["seq"], "title": c["title"],
                                  "hits": c["hit_count"]} for c in dense],
            "by_tag": {t: len(v) for t, v in sorted(tag_index.items())},
        },
        "tag_index": {t: v for t, v in sorted(tag_index.items())},
        "chapters": chapters,
    }


def write_digest(d: dict, path: Path, quote_min: int = 2) -> None:
    """
    阅读稿。完整卷宗几百万字没法通读，这份只留证据本身：
    每章的命中段原文 + 该段里的台词候选，去掉背景窗口。
    """
    st = d["stats"]
    L = [f"# {d['character']} 原文卷宗（阅读稿）", "",
         f"- 别名：{'、'.join(d['aliases'])}",
         f"- 存疑别名：{'、'.join(d['ambiguous_aliases']) or '无'}",
         f"- 出现 {st['total_mentions']} 次，跨 {st['chapters_with_mention']} 章"
         f"（seq{st['first_seq']} ~ seq{st['last_seq']}）",
         f"- 各别名：{st['by_alias']}",
         f"- 各别名首现：" + "；".join(
             f"{k} seq{v['seq']}段{v['para']}" for k, v in st["alias_first_seen"].items()),
         f"- 主题命中：{st['by_tag']}", "",
         "戏份最重的章节：",
         "".join(f"\n  - seq{c['seq']} {c['title']}（{c['hits']} 次）"
                 for c in st["densest_chapters"][:20]), "", "---", ""]

    for c in d["chapters"]:
        L.append(f"## seq{c['seq']}　{c['title']}　［{c['hit_count']} 次］")
        L.append("")
        for h in c["hits"]:
            tags = f"　{'/'.join(h['tags'])}" if h["tags"] else ""
            L.append(f"**[{h['para']}]**{tags}　{h['text']}")
            for s in h["speech"]:
                if len(s["quote"]) >= quote_min:
                    L.append(f"    └ 台词（{s['likely_speaker']}是她）：「{s['quote']}」")
            L.append("")
        if c["ambiguous"]:
            L.append(f"> 存疑（{'、'.join(d['ambiguous_aliases'])}，未必是本人）：")
            for h in c["ambiguous"]:
                L.append(f"> [{h['para']}] {h['text']}")
            L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="收集某个角色的全书原文卷宗")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--name")
    ap.add_argument("--aliases", help="逗号分隔")
    ap.add_argument("--ambiguous", default="", help="逗号分隔，可能指本人也可能指别人")
    ap.add_argument("--exclude", default="", help="逗号分隔，含别名但另有所指的词组")
    ap.add_argument("--window", type=int, default=2, help="命中段前后各带几段上下文")
    ap.add_argument("--out", type=Path, default=paths.CHARACTERS_DIR)
    args = ap.parse_args()

    if args.preset:
        p = PRESETS[args.preset]
        canon, aliases = p["canonical"], p["aliases"]
        ambiguous, note = p["ambiguous"], p.get("note", "")
        exclude = p.get("exclude", [])
    elif args.name and args.aliases:
        canon, note = args.name, ""
        aliases = [x.strip() for x in args.aliases.split(",") if x.strip()]
        ambiguous = [x.strip() for x in args.ambiguous.split(",") if x.strip()]
        exclude = [x.strip() for x in args.exclude.split(",") if x.strip()]
    else:
        raise SystemExit("给 --preset，或同时给 --name 与 --aliases")

    nv = load_novel()
    units = nv["chapters"] if isinstance(nv, dict) else nv
    d = collect(units, canon, aliases, ambiguous, args.window, exclude)
    d["note"] = note
    d["window"] = args.window
    d["exclude"] = exclude

    args.out.mkdir(parents=True, exist_ok=True)
    js, md = args.out / f"dossier_{canon}.json", args.out / f"dossier_{canon}.md"
    write_json(js, d)
    write_digest(d, md)

    st = d["stats"]
    print(f"{canon}：{st['total_mentions']} 次 / {st['chapters_with_mention']} 章"
          f"（seq{st['first_seq']} ~ seq{st['last_seq']}）")
    print(f"  各别名 {st['by_alias']}")
    if st["ambiguous_by_alias"]:
        print(f"  存疑   {st['ambiguous_by_alias']}（单独收，未并入）")
    print(f"  主题   {st['by_tag']}")
    print(f"  卷宗   {js.relative_to(paths.ROOT)}　{js.stat().st_size/1024/1024:.1f}MB")
    print(f"  阅读稿 {md.relative_to(paths.ROOT)}　{md.stat().st_size/1024/1024:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
