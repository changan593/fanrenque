# pipeline —— 脚本管道

编号即执行顺序。每个脚本只做一件事，产物落到固定位置，可断点续跑。

**只有 `s2` 和 `s6` 花 API 的钱**，其余全部是纯本地计算，随便重跑。
每个脚本的产物字段含义见 [`../doc/09_数据字典.md`](../doc/09_数据字典.md)。

| 脚本 | 干什么 | 产物 | 花钱 |
| --- | --- | --- | --- |
| `check_all.py` | **一键跑全部闸门**：selftest / s3 / s15 / s17 / s14 × 每集 | 终端报告，退出码 | |
| `s0_probe_text.py` | 纯原文结构探查（不依赖任何分析结果） | `data/plot/text_probe.json` | |
| `s1_normalize_novel.py` | 原文 txt → 标准化 | `source/novel.json` | |
| `s2_analyze_chapters.py` | **逐章分析**，每章 3 次调用 + 可选修订轮 | `data/chapters/chNNNN.json` | 💰 |
| `s2m_manual.py` | 把人工手写的分析稿装配成同格式 json | 同上 | |
| `s3_validate_chapters.py` | 全量体检，分 T1 / T2 / T3 三级 | `.run/reports/quality_report.json`、`rerun_seqs.txt` | |
| `s4_build_assets.py` | 聚合角色与场景资产 + 同框矩阵 | `data/characters/`、`data/scenes/`、`plot/cooccurrence.json` | |
| `s5_repair_quotes.py` | 把不逐字的引用**程序化对齐回原文** | 改写 `data/chapters/`，留痕 `plot/quote_repair_log.json` | |
| `s6_style_matrix.py` | 画风选型矩阵批量出图（选型已结束） | `production/style_test/out/` | 💰 |
| `s7_contact_sheet.py` | 把矩阵图拼成对比大图 | `production/style_test/sheets/` | |
| `s8_character_dossier.py` | 收集某角色的全书原文段落成卷宗 | `data/dossiers/dossier_*.md`（`.json` 不入库） | |
| `s9_plot_digest.py` | 把 1200 份分析压成剧情线的原材料 | `data/plot/digest_p0*.md`、`timeline.json` | |
| `s10_episode_plan.py` | 分集规划（约束求解）；**有剧本的集边界锁定** | `data/plot/episodes.json` | |
| `s11_episode_assets.py` | 算每集需要哪些角色与场景 | `data/plot/episode_assets.json` | |
| `s12_detect_arcs.py` | 从人物更替中检测卷段边界（软约束） | `data/plot/arcs.json` | |
| `s13_season_roster.py` | **按季汇总角色与场景清单**，做卡的工作队列 | `data/plot/season_roster.json/.md` | |
| `s14_narration_ledger.py` | **旁白承载账的闸门**：关键旁白有没有全部落到承载上 | 检查报告，`--out` 写承载账 md | |
| `s15_style_guard.py` | **画风金标准的闸门**：提示词里有没有与国风三维冲突的载体声明 | 检查报告 | |
| `s16_shot_pack.py` | **按幕装配出图包**：剧本＋幕提示词＋角色卡＋场景卡＋风格段 | 一份可直接开工的 md | |
| `s17_citation_check.py` | **原文引用的闸门**：每处 `【原】「…」` 的段号拿去和原文逐字对 | 检查报告，可自动修 | |
| `selftest.py` | 离线自测，196 条断言，用假模型跑通全流程 | | |
| `config.py` | 所有可调参数的唯一来源（读 `.env`） | | |

支撑模块 `common/`——**脚本之间不互相 import，公共逻辑只从这里拿**：

```
paths.py       全项目路径的唯一来源，别在别处拼路径
names.py       人物命名的唯一事实来源：PRESETS（人工核实的身份）、泛称判定（严/宽两口径）、
               三级别名解析 build_resolver、幕文档排行对照 CARD_ALIASES、更名裁定 RENAME_CANON
cite.py        取证记法：seqN[i] / [i-j] / [i][j] 解析、【原】引文提取、省略号分段比对、1 基定位
quality.py     质量块只有一份实现：measure / gate / refresh，s2 s2m s3 s5 共用
verbatim.py    逐字核验器 —— 整条管道唯一不依赖模型判断的客观闸门；台词覆盖率
production.py  production/ 卡目录索引：有没有卡、母版在哪、重号检测（s13 s16 共用）
novel.py       novel.json 的读取助手
jsonio.py      原子写 JSON、从模型回复里抠 JSON
llm.py         DeepSeek 客户端：显式声明思考模式、自适应降级重试、限速、用量统计
progress.py    跑批时的原地刷新看板
prompts/       四个提示词，纯文本，改了立即生效，不用动代码
schemas/       章节分析 JSON 的字段口径说明（不做运行时校验，校验在 s3）
```

## 依赖关系

```
s1 ──→ s2 ──→ s3（体检）
       │        └─→ s5（修引用，改 chapters）
       ├──→ s4 ──→ index.json ──┬──→ s9 ──→ timeline ──→ s12 ──→ arcs ──→ s10 ──→ episodes ──→ s11
       │                        ├──→ s13（还读 episodes、production/ 卡目录）
       │                        └──→ s11
       ├──→ s8（只读 novel.json，与上面无关）
       └──→ s0（只读 novel.json）

剧本层：episodes + chapters ──→ s14　　卡与幕文档 ──→ s15 / s16 / s17

s6 ──→ s7      画风选型，与上面整条链路完全独立
```

**改了 `data/chapters/` 之后，`s4` → `s9` → `s12` → `s10` → `s11` → `s13` 要按序重跑**，否则下游拿的是旧资产。
改了 `common/names.PRESETS` 之后同样。`s10` 不会动已有剧本的集。完整链路见 [`../doc/10_交接说明.md`](../doc/10_交接说明.md) 第四节。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env                       # ① 填入 DEEPSEEK_API_KEY

python pipeline/check_all.py --quick       # ② 不花钱：selftest + s3 + s15 + s17
python pipeline/s2_analyze_chapters.py --doctor    # ③ 验密钥/模型/网络，一次最小请求

python pipeline/s2_analyze_chapters.py --smoke 3   # ④ 先跑 3 章，人工看一眼质量
python pipeline/s2_analyze_chapters.py             # ⑤ 跑全书，中断了重跑会自动续
python pipeline/s3_validate_chapters.py            # ⑥ 体检，拿到需要重跑的章节列表
python pipeline/s5_repair_quotes.py                # ⑦ 程序化修引用，不花钱
python pipeline/s4_build_assets.py                 # ⑧ 聚合角色与场景资产
```

以上第 ④~⑧ 步**都已经跑完，产物已入库**。现在拉下代码的人不需要重跑，跑一次 `check_all.py` 确认数据完好即可。
`s3` 当前会报 29 章 T2（审查分低于合格线），那是已知欠账，重跑要花钱：`s2 --redo-failed`。

## 密钥与配置

密钥放项目根目录的 `.env`，不用每次 `export`。`.env` 在 `.gitignore` 里，不会被提交。

```
优先级：真实环境变量  >  .env  >  config.py 里的默认值
```

可配项见 `.env.example`：模型、base_url、并发数、限速、修订轮上限、超时、重试次数。
临时改一次用环境变量覆盖即可，例如 `PIPELINE_WORKERS=8 python pipeline/s2_analyze_chapters.py`。

**阈值与魔法数全部在 `config.py`**，按脚本分节：质量闸门（结构 85 / 一致 90 / 逐字 95% / 台词覆盖 90%）、
s3 的简介与审查分析最短字数、s4 的主名门槛、s10 的时长模型与惩罚、s12 的卷段长度、s13 的档位、s14 的现身上限与删除率。
改阈值改那里，别改脚本。

## 并发与进度

`--workers` 控制并发章节数（默认 4，或 `.env` 里的 `PIPELINE_WORKERS`）。撞到 429 就设 `PIPELINE_QPS=3` 做全局限速。

跑批时终端会画一块原地刷新的看板，**总进度**、**章内进度**、**请求实时状态**都能看到：

```
总进度 [██████████░░░░░░░░░░░░░░░░░░░░] 413/1200  34.4%  ✓409 ✗4  用时 1:23:45  剩余 ~2:39:35
  seq414   第414章 丹炉火微明，心静志更坚      [1/5] 抽取       生成中 · ↓2847字            127s
  seq415   第415章 万金，万斤                  [3/5] 结构审查   生成中 · ↓641字              64s
  seq416   第416章 不夜楼，倒悬镜              [4/5] 一致性审查 退避重试 第2次 · HTTP 429     0s
  seq417   第417章 佛前有百尺，皇都第一人      [1/5] 抽取       请求中                        0s ⚠静默41s
```

- **总进度**：完成数/总数、百分比、成功失败、用时、预计剩余
- **章内进度**：`[步骤/总步数]` + 当前环节。固定 5 步（抽取 → 逐字核验 → 结构审查 → 一致性审查 → 写入），
  触发修订轮时分母自动上调（每轮 +2：修订 + 复核）
- **请求实时状态**：`生成中 · ↓2847字` 里的字数每 0.3 秒刷新一次。**字数在涨就是活的。**

末列两个时间：本章总耗时，以及超过 20 秒没收到任何新状态时出现的 `⚠静默Ns`。静默计时一直涨才是真卡住了。
输出重定向到文件时自动退化成逐行日志；也可以用 `--plain` 强制。

## 断点续传

**默认行为，不用加参数。** 每章跑完立刻单独落盘，且用的是原子写（先写临时文件再 rename，权限按 umask），
Ctrl+C 或断网都不会留下半个坏文件。重跑同一条命令就从断点继续。

| 状态 | 含义 | 默认处理 |
| --- | --- | --- |
| `done` | 跑完且过了质量闸门 | 跳过 |
| `missing` | 没跑过 | 跑 |
| `incomplete` | 文件在但内容不全（上次跑到一半被打断 / 写坏了） | 自动重跑 |
| `failed` | 跑完了但没过质量闸门 | 跳过，`--redo-failed` 才重跑 |

`s3` 的 T2 与这里的 `failed` 是同一批章——两个脚本用的是同一份闸门实现 `common/quality.py`。

## s2 的每章流程

```
              ┌─────────────────────────────────────────┐
  原文 ──────→│ ① extract 抽取                          │  留痕 reviews[stage=extract_meta]：用量 / 重试 / 降级
  前几章简介   │   人物/场景/对话/独白/旁白/节拍/简介     │
  已知人名表   └─────────────────┬───────────────────────┘
                                ↓
              ┌─────────────────────────────────────────┐
              │ ★ 程序逐字核验（不调模型）               │
              │   每条引用回原文做精确匹配                │
              │   exact 逐字 / near 改写 / miss 臆造     │
              │   另算台词覆盖率，专盯遗漏                │
              └─────────────────┬───────────────────────┘
                                ↓
       ┌────────────────────────┴────────────────────────┐
       ↓                                                 ↓
┌──────────────────────┐                    ┌──────────────────────────┐
│ ② structure_review   │                    │ ③ fidelity_review        │
│   结构完整性          │                    │   臆造/改写/遗漏/张冠李戴 │
│   跨章命名一致        │                    │   喂入★的客观结论作证据   │
│   上下文衔接          │                    │   出 score + 详细分析     │
│   出 score + 详细分析 │                    │   出 missing_items 给修订用│
└──────────┬───────────┘                    └────────────┬─────────────┘
           └───────────────────┬─────────────────────────┘
                               ↓
                    ┌──────────────────────┐
                    │ 质量闸门 quality.gate │
                    │ 结构≥85 且 一致≥90    │──通过──→ 落盘
                    │ 且 逐字命中≥95%       │
                    │ 且 臆造条数=0         │
                    │ 且 台词覆盖≥90%       │
                    └──────────┬───────────┘
                            不通过
                               ↓
                    ┌──────────────────────┐
                    │ ④ repair 修订         │
                    │ 删臆造/改回原文/补遗漏 │──→ 重跑★和③ ──→ 落盘
                    └──────────────────────┘
```

**为什么要夹一道程序核验**：deepseek-v4-flash 是小模型，最容易犯的错是「顺手把台词改通顺」
和「凭印象补一句没有的话」。让模型自己审自己抓不住这两类错，但字符串精确匹配一抓一个准。
把这个客观结论喂给一致性审查员当证据，审查质量会显著高于让它裸审。

**审查员的 `missing_items` 不是闸门**，只作修订轮的输入。实测 1200 章里 415 章的审查员列了遗漏项，
多为「是！」「蜜语」这类噪音，而程序台词覆盖率全部 100%——盯漏靠覆盖率，不靠模型印象。

## s2 常用参数

| 命令 | 作用 |
|---|---|
| `--smoke 3` | 只跑前 3 章，验管道 |
| `--range 100 200` | 只跑 seq 100~200 |
| `--seqs 9,90,111` | 只跑这些 seq（体检后精确重跑用，比 `--range` 省钱） |
| `--workers 8` | 并发章节数，默认 4 |
| `--redo-failed` | 连同上次没过质量闸门的章节一起重跑 |
| `--force` | 无视已有结果全部重跑 |
| `--plain` | 关掉进度看板，改逐行输出 |
| `--phase review` | 复用已有抽取结果，只重跑两道审查。**在原记录上追加**，旧审查与调用数保留 |
| `--doctor` | 只发一次最小请求验密钥/模型/网络，不跑章节 |

`--range` / `--seqs` / `--smoke` 互斥。`--phase review` 的用途：首轮并发跑的时候，靠后的章节可能读不到紧邻前几章的简介，
连贯性审查的上下文会略弱。全书跑完后再跑一次 `--phase review --force`，成本只有一次审查的钱。

环境变量可临时覆盖配置：`DEEPSEEK_MODEL`、`PIPELINE_WORKERS`、`PIPELINE_QPS`、`PIPELINE_REPAIR_ROUNDS`、`LLM_TIMEOUT`。

## 跑得慢 / 像卡住了怎么办

先跑 `--doctor`，它会一次性告诉你密钥对不对、模型名存不存在、网络通不通、首字节多久。然后看看板的**请求实时状态列**：

| 现象 | 含义 | 处理 |
|---|---|---|
| `生成中 · ↓字数`在涨 | 正常，只是生成慢 | 等；嫌慢就调小 `LLM_MAX_TOKENS` |
| 停在`请求中`且`⚠静默`在涨 | 连上了但服务端不吐数据 | 到 `LLM_STALL_TIMEOUT`（默认 90s）会自动重试 |
| 反复`退避重试 · HTTP 429` | 撞限流了 | 调小 `PIPELINE_WORKERS`，或设 `PIPELINE_QPS=3` |
| 停在`推理中 · ↓字数` | 推理模型在跑思维链，还没开始写正式回复 | 正常，等它转到`生成中` |
| 立刻报 HTTP 400/404 | 模型名不对 | 检查 `.env` 里的 `DEEPSEEK_MODEL` |
| 立刻报 HTTP 401/403 | 密钥不对或没权限 | 检查 `.env` 里的 `DEEPSEEK_API_KEY` |
| 报`模型返回空回复` | 见下 | 按报错里的处置改 `.env` |

### 「模型返回空回复」——先确认思考模式是关的

这是目前为止最贵的一个坑：一次 500 章的跑批耗了 **6 小时 45 分，只成功 9 章**，
其余全是 `回复中找不到合法 JSON` 或 `Expecting ',' delimiter`。

根因是两条官方文档叠在一起：

1. **「思考模式默认打开，且 effort 默认为 high」**——不显式关掉，每次调用都会先跑一大段思维链。
   实测 398 次成功调用输出了 210 万 token，**平均每次 5,300 token**，而每章 JSON 撑死 3~5k token，
   多出来的全在 `reasoning_content` 里。思维链和正式回复共用 `max_tokens`，长章节直接把 `content` 挤成空。
2. **「使用 JSON Output 功能时，API 有概率会返回空的 content」**——官方已载明的已知问题。

两者叠加，空回复率极高。而且思考模式下 **`temperature` 不生效**，抽取环节最需要的稳定性反而拿不到。

所以 `.env` 里 **`LLM_THINKING=0` 是默认值，保持不动**。只有想让模型对某个环节多想时才临时打开：

```bash
LLM_THINKING=1 python pipeline/s2_analyze_chapters.py --phase review --force
```

客户端**永远显式声明** `thinking`，不依赖服务端默认值——默认值会变，代码里的不会。

### 重试是自适应的，不会原样空转

| 症状 | 下一次怎么改 |
| --- | --- |
| 空回复 | 先摘掉 JSON 模式（官方已知问题），再关掉思考模式 |
| `finish_reason=length` 截断 | `max_tokens` × 1.5，直到 `LLM_MAX_TOKENS_CAP` 封顶 |
| JSON 解析失败且开着 JSON 模式 | 摘掉 JSON 模式，改由提示词约束 + `extract_json_block` 兜底 |
| 5xx / 网络异常 | 原样退避重试（这类是真的可能自己好） |

降级到底还是空，才带完整证据失败，并给出可直接执行的处置（改 `.env` 的哪一项）。
自适应过程会记进章节 json 每条 `reviews` 的 `adapted` 字段，事后能查这一章是怎么救回来的。

另一个隐蔽点：`content` 只有一个 BOM 或零宽字符时，Python 的 `str.strip()` 不认它们，会绕过空回复判定。客户端会先剥掉这类不可见字符。

单章正常耗时参考：三次调用串行，关掉思考模式后整章 1~4 分钟。并发 4 的话全书 1200 章 ≈ 5~20 小时。
**如果单章动辄十几分钟，先查 `LLM_THINKING` 是不是被打开了。**

## s2 成本估算

全书 1200 章。每章输入约 3.5k token（原文 + 前文提要 + 解析结果回喂），输出约 2.5k token，
三次调用合计输入约 10k、输出约 4k。按修订触发率 20% 估，全书约 1300 万输入 token、550 万输出 token。

## 日志

```
.run/logs/llm_calls.jsonl   每次 HTTP 请求一行：章节、环节、第几次、状态码、耗时、收到字数（所有调 LLM 的脚本共用）
.run/logs/s2_runs.jsonl     每章一行：分数、逐字命中率、修订轮数、耗时
.run/logs/s2_errors.jsonl   失败章节及原因
.run/logs/s6_images.jsonl   每次出图请求一行
.run/reports/               s3 的体检报告与重跑清单，每次重写
```

`llm_calls.jsonl` 是排查卡顿的第一手材料：`tail -f .run/logs/llm_calls.jsonl`。`.run/` 不入库。

---

# 其余脚本

上面全是 `s2` 的内容，因为只有它是长跑批、会出各种幺蛾子。下面这些都是几秒到几分钟跑完的本地计算。

## check_all —— 一键跑全部闸门

```bash
python pipeline/check_all.py            # selftest + s3 + s15 + s17 + s14 × 每一集有剧本的集，约 70 秒
python pipeline/check_all.py --quick    # 跳过 s14
```

任一失败退出码 1，失败项列在最后。`s3` 只在 T1 / T2 非空时算失败（T3 只提示）。改完任何东西——代码、卡、剧本、文档——跑一次。

## s3_validate_chapters —— 体检

不调 API。重算逐字命中与台词覆盖（不信任落盘值），查必填字段、简介长度与截断、段号越界、
人物条目重复、场景里出现未登记人物、说话人不在名单（宽口径泛称放行）、审查留痕齐全、审查分是否过合格线。
结论与 `s2` 的续传状态一致。枚举外取值（`role_in_chapter` 等）只统计分布不判错——模型并不守枚举，schema 里的是建议值。

```bash
python pipeline/s3_validate_chapters.py                # 全量，报告写 .run/reports/
python pipeline/s3_validate_chapters.py --rerun-list   # 只打印 T1+T2 的 seq，可直接喂 s2 --seqs
```

## s5_repair_quotes —— 把不逐字的引用对齐回原文

体检报告里「逐字命中率不到 100%」的章节，绝大多数不是模型编造，而是五类**机械性错误**。这个脚本逐类识别、逐条对齐，**不调 API**：

| 修复类型 | 症状 |
| --- | --- |
| 拆分跨段 | 一条引用横跨原文两个自然段，被当成一条 |
| 拆分拼接 | 把同一人相邻两句话拼成了一句 |
| 剥离旁白 | 台词里混进了「他说道」这类叙述 |
| 对齐原文 | 顺手改通顺了几个字（最常见） |
| 补漏台词与人物 | 原文有这句话但没被抽出来；补登的人物 `role_in_chapter` 记「参与」、带 `_filled` |

```bash
python pipeline/s5_repair_quotes.py --dry-run     # 只报会改什么，不落盘
python pipeline/s5_repair_quotes.py               # 修全书
python pipeline/s5_repair_quotes.py --seqs 9,90   # 只修指定章
```

修完调 `quality.refresh` 重算整块质量（此前只回写 verbatim 不回写 coverage / passed，留下过 374 章的旧值）。
每次修复都记进 `data/plot/quote_repair_log.json`，含改前改后原文。

> **对齐算法有个反直觉的坑**：单纯按相似度找最佳匹配会**奖励删字**——`不知他怎么了` 与原文的相似度高于
> `楼主不知他怎么了`，于是主语被吃掉。所以匹配时在 ±8 字窗口内搜索，并对「落在完整句读边界上」额外加分。
> 改这个脚本前先读代码里 `_boundary_bonus` 的注释。

## s2m_manual —— 人工分析装配器

把手写的分析稿装配成与 `s2` 完全同格式的章节 json，产出**人工基准集**（`production/s01/manual_analysis/` 的 8 章）。
引用用**位置引用**（`{"para": N, "q": K}` ＝ 第 N 段的第 K 条引号内容，**都是 1 基**）而不是抄原文，
所以逐字命中率天然是 100%。闸门用 `quality.gate(manual=True)`：不看模型分数，不允许改写，看台词覆盖。

## s4_build_assets —— 角色与场景资产

```bash
python pipeline/s4_build_assets.py               # 全量
python pipeline/s4_build_assets.py --no-merge    # 不做别名归并，看原始名单
```

别名归并用并查集，三道防线：泛称（严口径 `names.is_generic`）不参与；**闸门建在簇上**——一个簇里出现两个各自在
≥ 5 章里当过正名的名字就拒绝合并并留痕（`index.json` 的 `rejected_merges`）；`names.verified_pairs()`（PRESETS）优先落地。
一条错别名曾把 973 个角色塌成一个「唐真」，这三道就是为它建的。

## s8_character_dossier —— 角色卷宗

**写任何角色文档之前必须先跑这一步。** 把某角色在全书的每一次出现连同上下文收成一份卷宗。

```bash
python pipeline/s8_character_dossier.py --preset 唐真
python pipeline/s8_character_dossier.py --name 某某 --aliases 甲,乙 --ambiguous 丙 --exclude 丁 --window 3
```

产两份到 `data/dossiers/`：`dossier_名字.json`（机器读，不入库）和 `dossier_名字.md`（人读，入库；含别名词频、首现位置、戏份最重的章节、逐段命中）。
**段号 1 基。**

别名分三档：`aliases` 确定是本人；`ambiguous` 可能是本人也可能是别人，单独统计不混入（「大师兄」）；
`exclude` 含别名字样但另有所指，先抹掉再匹配（「寿与天齐」）。17 个核实过的角色在 `common/names.PRESETS`，
每个都带 `note` 写明别名边界是怎么核实的。新增角色时**必须**照这个格式写清依据。

## s9 / s12 / s10 / s11 / s13 —— 剧情线与分集

```bash
python pipeline/s9_plot_digest.py        # 1200 份分析 → 6 块分段摘要 + timeline.json
python pipeline/s12_detect_arcs.py       # 检测卷段边界 → arcs.json（软约束）
python pipeline/s10_episode_plan.py      # 分集求解 → episodes.json（267 集）
python pipeline/s11_episode_assets.py    # 每集要哪些角色场景 → episode_assets.json
python pipeline/s13_season_roster.py     # 按季汇总 → season_roster.json/.md
```

`s10` 是个最短路求解器，目标函数 = 时长偏差惩罚 + 断点惩罚，时长模型 `语音字数 ÷ 4.5 字/秒 × 1.20`
（1.20 由 E01/E02 两集人工试点标定）。**已有 `剧本.md` 的集从上一版分集表原样搬过来、边界锁定**，
只重排其余部分；锁定的集会被重新编号时直接报错。`--dry-run` 只看时长分布。

`s12` 的产物**只作软约束**，它找的是「核心阵容换人的位置」，不是「一个故事讲完的位置」——
人工回读第一季的 10 个边界只有约六成站得住。硬断只认「有主要角色从此不再出现」。

`s9` / `s11` / `s13` 的别名 → 主名全部走 `names.build_resolver` 三级判定（PRESETS > 唯一认领 > 不猜）。
`s13` 判「有没有卡」看 `production/characters/`、`scenes/` 目录里有没有母版提示词（按名字匹配），
判「有没有深度档案」看 `production/characters/_深度档案/`。

## s14_narration_ledger —— 旁白承载账闸门

```bash
python pipeline/s14_narration_ledger.py --episode S01E03
python pipeline/s14_narration_ledger.py --episode S01E03 --out production/s01/E03/旁白承载账.md
```

读剧本声音栏的 `【现】【白】【卡】【画】【删】【台】【心·】` 标记，逐条比对该集各章的 `analysis.narration`，
五道闸门：全部上账、无重复承载、删除率 ≤ 15%、现身 ≤ 6 次、白描留声有 `※` 理由。
标记与段号的写法见 [`../doc/13_剧本格式规范.md`](../doc/13_剧本格式规范.md)，六道机械检查的来历见 `doc/15` 第二节。

## s15_style_guard —— 画风金标准闸门

```bash
python pipeline/s15_style_guard.py                       # 全量，有违规退出码 1
python pipeline/s15_style_guard.py --show-legacy         # 连留痕豁免文件一起列
python pipeline/s15_style_guard.py --path production/s01/E01
```

**它只扫会被喂给模型的区域**——围栏代码块与引用块。**负面写法是对的，不报**（按句子看否定词在不在词前面）。
另外逐字比对每张卡的渲染锁与 `style_assets` 短版、`C02` / `C04` 的眉眼同源锁与 `_眉眼同源锁.md`，改一个字都指出第几个字。
**留痕豁免**在脚本的 `LEGACY_ALLOW` 里（`doc/archive/`、`style_test/`），每条都写了理由。

## s16_shot_pack —— 按幕装配出图包

```bash
python pipeline/s16_shot_pack.py --list s01/E01              # 这一集有哪些幕
python pipeline/s16_shot_pack.py s01/E01/01_第一幕_北阳城城隍庙 -o pack.md
python pipeline/s16_shot_pack.py s01/E01/01_第一幕_北阳城城隍庙 --refs-only
```

从幕文档的「本幕人物与状态」表里读角色名（排行经 `names.CARD_ALIASES` 换算），从正文里读场景码 `SNN`，
再去 `production/characters/` 与 `production/scenes/` 找卡（`common/production.py`），按 `style_assets/README.md` 的九步顺序装配。
**匹配不上的卡会明确报出来，不会静默跳过**；`NO_CARD_BY_DESIGN` 里的名字（唐假、人魔尊、老赵……）单独说明为什么不建卡。

## s17_citation_check —— 原文引用闸门

原则二此前只有 `s3` 在管**章节分析**那一层；这道闸门管人写的取证文档。
一行里同时出现 `seqN[i]` 和 `【原】「…」` 时，把引文拿去和 `source/novel.json` 的第 i 段（**1 基**）逐字比对
（归一化口径：去空白与标点，中英全半角等价）。只认 `【原】` 之后的引号。
`seq2[1][18]`（一行挂两段）、`seq3[18-19]`（区间）、「前半句……后半句」（省略号跳过）都认得——解析在 `common/cite.py`。

```bash
python pipeline/s17_citation_check.py                     # 全量，当前 1431 条全部命中
python pipeline/s17_citation_check.py --path production/characters
python pipeline/s17_citation_check.py --fix               # 只改「差一位」
python pipeline/s17_citation_check.py --relocate          # 引文全书唯一落点时改段号
python pipeline/s17_citation_check.py --show-ok           # 把命中的也列出来
```

它抓到的第一个真问题是 `s8` 卷宗曾输出 0 基段号（照着卷宗写卡的人会整张偏移一位）。已修，全项目段号一律 1 基。

## s6 / s7 —— 画风选型矩阵（已结束）

配置在 `production/style_test/matrix.json`：10 个画面目标 × 11 个画风 × 每格 3 张。

```bash
python pipeline/s6_style_matrix.py --doctor    # 阶梯式体检：连通/鉴权/模型/尺寸
python pipeline/s6_style_matrix.py --dump      # 只导出提示词，人眼过一遍
python pipeline/s6_style_matrix.py --targets T03 --styles S1,S6   # 先试一格
python pipeline/s7_contact_sheet.py            # 拼对比大图
```

> 要改提示词请改 `matrix.json`，不要改 `prompts.md`（那是 `--dump` 生成的产物）。
> 尺寸最容易踩：`1792x1024` 是 dall-e-3 的档位，gpt-image 系列只接受 `1024x1024 / 1536x1024 / 1024x1536 / auto`，
> 拿错了服务端可能直接回 502。脚本会先做尺寸预检。

选型结论是国风三维，落在 `production/style_assets/`；这两个脚本留着是为了以后复盘或重跑对比。
