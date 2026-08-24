# pipeline —— 脚本管道

编号即执行顺序。每个脚本只做一件事，产物落到固定位置，可断点续跑。

**只有 `s2` 和 `s6` 花 API 的钱**，其余全部是纯本地计算，随便重跑。
每个脚本的产物字段含义见 [`../doc/09_数据字典.md`](../doc/09_数据字典.md)。

| 脚本 | 干什么 | 产物 | 花钱 |
| --- | --- | --- | --- |
| `s0_probe_text.py` | 纯原文结构探查（不依赖任何分析结果） | `data/plot/text_probe.json` | |
| `s1_normalize_novel.py` | 原文 txt → 标准化 | `source/novel.json` | |
| `s2_analyze_chapters.py` | **逐章分析**，每章 3~4 次调用 | `data/chapters/chNNNN.json` | 💰 |
| `s2m_manual.py` | 把人工手写的分析稿装配成同格式 json | 同上 | |
| `s3_validate_chapters.py` | 全量体检 | `data/plot/quality_report.json` | |
| `s4_build_assets.py` | 聚合角色与场景资产 | `data/characters/`、`data/scenes/` | |
| `s5_repair_quotes.py` | 把不逐字的引用**程序化对齐回原文** | 改写 `data/chapters/` | |
| `s6_style_matrix.py` | 画风选型矩阵批量出图 | `production/style_test/out/` | 💰 |
| `s7_contact_sheet.py` | 把矩阵图拼成对比大图 | `production/style_test/sheets/` | |
| `s8_character_dossier.py` | 收集某角色的全书原文段落成卷宗 | `data/characters/dossier_*.json/.md` | |
| `s9_plot_digest.py` | 把 1200 份分析压成剧情线的原材料 | `data/plot/digest_*.md`、`timeline.json` | |
| `s10_episode_plan.py` | 分集规划（约束求解） | `data/plot/episodes.json` | |
| `s11_episode_assets.py` | 算每集需要哪些角色与场景 | `data/plot/episode_assets.json` | |
| `s12_detect_arcs.py` | 从人物更替中检测卷段边界 | `data/plot/arcs.json` | |
| `s13_season_roster.py` | **按季汇总角色与场景清单**，归属判定比 `s11` 严格 | `data/plot/season_roster.json/.md` | |
| `s14_narration_ledger.py` | **旁白承载账的闸门**：关键旁白有没有全部落到承载上 | 检查报告，可选 md | |
| `s15_style_guard.py` | **画风金标准的闸门**：提示词里有没有与国风三维冲突的载体声明 | 检查报告 | |
| `selftest.py` | 离线自测，用假模型跑通全流程 | | |
| `config.py` | 所有可调参数的唯一来源（读 `.env`） | | |

支撑模块：

```
common/paths.py      全项目路径的唯一来源，别在别处拼路径
common/jsonio.py     原子写 JSON、从模型回复里抠 JSON
common/novel.py      novel.json 的读取助手
common/llm.py        DeepSeek 客户端：显式声明思考模式、自适应降级重试、限速、用量统计
common/verbatim.py   逐字核验器 —— 整条管道唯一不依赖模型判断的客观闸门
common/progress.py   跑批时的原地刷新看板
prompts/             四个提示词，纯文本，改了立即生效，不用动代码
schemas/             章节分析 JSON 的结构约定
```

## 依赖关系

```
s1 ──→ s2 ──→ s3 ──→ s5 ──┐
       │                   ├──→ s4 ──→ s8
       │                   └──→ s9 ──→ s12 ──┐
       │                          └──────────┴──→ s10 ──→ s11
       └──→（s0 与它并行，互不依赖，可当交叉校验源）

s6 ──→ s7      画风选型，与上面整条链路完全独立
```

**改了 `data/chapters/` 之后，`s4`、`s9` 必须重跑**，否则下游拿的是旧资产。
完整重跑链路见 [`../doc/10_交接说明.md`](../doc/10_交接说明.md) 第四节。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env                       # ① 填入 DEEPSEEK_API_KEY

python pipeline/s1_normalize_novel.py      # ② 标准化原文（已跑过，产物已入库）
python pipeline/selftest.py                # ③ 离线自测，确认管道通，不花额度
python pipeline/s2_analyze_chapters.py --doctor    # ④ 验密钥/模型/网络，一次最小请求

python pipeline/s2_analyze_chapters.py --smoke 3   # ⑤ 先跑 3 章，人工看一眼质量
python pipeline/s2_analyze_chapters.py             # ⑥ 跑全书，中断了重跑会自动续
python pipeline/s3_validate_chapters.py            # ⑦ 体检，拿到需要重跑的章节列表
python pipeline/s5_repair_quotes.py                # ⑧ 程序化修引用，不花钱
python pipeline/s4_build_assets.py                 # ⑨ 聚合角色与场景资产
```

以上第 ①~⑨ 步**都已经跑完，产物已入库**。现在拉下代码的人不需要重跑，
只需要跑一次 `selftest.py` + `s3_validate_chapters.py` 确认数据完好。

`s0_probe_text.py` 独立于以上流程，任何时候都能跑，用来在分析跑完前先摸清全书结构，
以及在跑完后当独立交叉校验源。

## 密钥与配置

密钥放项目根目录的 `.env`，不用每次 `export`。`.env` 在 `.gitignore` 里，不会被提交。

```
优先级：真实环境变量  >  .env  >  config.py 里的默认值
```

可配项见 `.env.example`：模型、base_url、并发数、限速、修订轮上限、超时、重试次数。
临时改一次用环境变量覆盖即可，例如 `PIPELINE_WORKERS=8 python pipeline/s2_analyze_chapters.py`。

## 并发与进度

`--workers` 控制并发章节数（默认 4，或 `.env` 里的 `PIPELINE_WORKERS`）。
撞到 429 就设 `PIPELINE_QPS=3` 做全局限速。

跑批时终端会画一块原地刷新的看板，**总进度**、**章内进度**、**请求实时状态**都能看到：

```
总进度 [██████████░░░░░░░░░░░░░░░░░░░░] 413/1200  34.4%  ✓409 ✗4  用时 1:23:45  剩余 ~2:39:35
  seq414   第414章 丹炉火微明，心静志更坚      [1/4] 抽取       生成中 · ↓2847字            127s
  seq415   第415章 万金，万斤                  [3/4] 结构审查   生成中 · ↓641字              64s
  seq416   第416章 不夜楼，倒悬镜              [4/4] 一致性审查 退避重试 第2次 · HTTP 429     0s
  seq417   第417章 佛前有百尺，皇都第一人      [1/4] 抽取       请求中                        0s ⚠静默41s
```

三层信息：

- **总进度**：完成数/总数、百分比、成功失败、用时、预计剩余
- **章内进度**：`[步骤/总步数]` + 当前环节。固定 4 步（抽取 → 逐字核验 → 结构审查 →
  一致性审查），触发修订轮时分母自动上调（每轮 +2：修订 + 复核）
- **请求实时状态**：`生成中 · ↓2847字` 里的字数每 0.3 秒刷新一次。
  **字数在涨就是活的。** 重试、退避、HTTP 错误码也都显示在这一列

末列两个时间：本章总耗时，以及超过 20 秒没收到任何新状态时出现的 `⚠静默Ns`。
静默计时一直涨才是真卡住了。

看板宽度自适应，窄终端下会先截章节名保住判活信息。输出重定向到文件时自动退化成
逐行日志，不吐 ANSI 转义符；也可以用 `--plain` 强制。

## 断点续传

**默认行为，不用加参数。** 每章跑完立刻单独落盘，且用的是原子写
（先写临时文件再 rename），Ctrl+C 或断网都不会留下半个坏文件。
重跑同一条命令就从断点继续。

续传时每章会判成四种状态之一：

| 状态 | 含义 | 默认处理 |
| --- | --- | --- |
| `done` | 跑完且过了质量闸门 | 跳过 |
| `missing` | 没跑过 | 跑 |
| `incomplete` | 文件在但内容不全（上次跑到一半被打断 / 写坏了） | 自动重跑 |
| `failed` | 跑完了但没过质量闸门 | 跳过，`--redo-failed` 才重跑 |

启动时会先打印续传情况：

```
续传状态：已完成 408 | 未跑 787 | 残缺 1 | 未达标 4
  提示：4 章未达标，本次不重跑；要重跑加 --redo-failed
本次待处理 788 章
```

## s2 的每章流程

```
              ┌─────────────────────────────────────────┐
  原文 ──────→│ ① extract 抽取                          │
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
│   出 score + 详细分析 │                    │   出 missing_items 待补   │
└──────────┬───────────┘                    └────────────┬─────────────┘
           └───────────────────┬─────────────────────────┘
                               ↓
                    ┌──────────────────────┐
                    │ 质量闸门              │
                    │ 结构≥85 且 一致≥90    │──通过──→ 落盘
                    │ 且 逐字命中≥95%       │
                    │ 且 臆造条数=0         │
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

## s2 常用参数

| 命令 | 作用 |
|---|---|
| `--smoke 3` | 只跑前 3 章，验管道 |
| `--range 100 200` | 只跑 seq 100~200 |
| `--workers 8` | 并发章节数，默认 4 |
| `--redo-failed` | 连同上次没过质量闸门的章节一起重跑 |
| `--force` | 无视已有结果全部重跑 |
| `--plain` | 关掉进度看板，改逐行输出 |
| `--phase review` | 复用已有抽取结果，只重跑两道审查 |
| `--doctor` | 只发一次最小请求验密钥/模型/网络，不跑章节 |

`--phase review` 的用途：首轮并发跑的时候，靠后的章节可能读不到紧邻前几章的简介
（还没落盘），连贯性审查的上下文会略弱。全书跑完后再跑一次 `--phase review --force`，
此时全书简介都在，能拿到完整上下文的审查结论，成本只有一次审查的钱。

环境变量可临时覆盖配置：`DEEPSEEK_MODEL`、`PIPELINE_WORKERS`、`PIPELINE_QPS`、
`PIPELINE_REPAIR_ROUNDS`、`LLM_TIMEOUT`。

## 跑得慢 / 像卡住了怎么办

先跑 `--doctor`，它会一次性告诉你密钥对不对、模型名存不存在、网络通不通、首字节多久。

然后看看板的**请求实时状态列**：

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

1. **「思考模式默认打开，且 effort 默认为 high」**——不显式关掉，每次调用都会先跑
   一大段思维链。实测 398 次成功调用输出了 210 万 token，**平均每次 5,300 token**，
   而每章 JSON 撑死 3~5k token，多出来的全在 `reasoning_content` 里。
   思维链和正式回复共用 `max_tokens`，长章节直接把 `content` 挤成空。
2. **「使用 JSON Output 功能时，API 有概率会返回空的 content」**——官方已载明的已知问题。

两者叠加，空回复率极高。而且思考模式下 **`temperature` 不生效**，
抽取环节最需要的稳定性反而拿不到。

所以 `.env` 里 **`LLM_THINKING=0` 是默认值，保持不动**。
逐章分析是「照着原文抄成结构化 JSON」的抽取活，不是推理题，思维链在这里只有坏处。
只有想让模型对某个环节多想时才临时打开，比如只重跑审查：

```bash
LLM_THINKING=1 python pipeline/s2_analyze_chapters.py --phase review --force
```

客户端**永远显式声明** `thinking`，不依赖服务端默认值——默认值会变，代码里的不会。

### 重试是自适应的，不会原样空转

对确定性错误原样重试毫无意义。上一版对空回复重试 5 次、每次都空，
一章白烧十几分钟。现在每次重试都会**改一个参数**：

| 症状 | 下一次怎么改 |
| --- | --- |
| 空回复 | 先摘掉 JSON 模式（官方已知问题），再关掉思考模式 |
| `finish_reason=length` 截断 | `max_tokens` × 1.5，直到 `LLM_MAX_TOKENS_CAP` 封顶 |
| JSON 解析失败且开着 JSON 模式 | 摘掉 JSON 模式，改由提示词约束 + `extract_json_block` 兜底 |
| 5xx / 网络异常 | 原样退避重试（这类是真的可能自己好） |

降级到底还是空，才带完整证据失败：

```
模型返回空回复（content 为空）。finish_reason=length，思维链 9840 字，输出 8192 tok，max_tokens=8192
  原因：思维链把 max_tokens 吃光了，没剩下额度写正式回复。
  处置（改 .env 后重跑）：
    1) 关掉思考模式：LLM_THINKING=0（推荐，抽取任务不需要思维链）
    2) 或调大额度：LLM_MAX_TOKENS=32768
```

自适应过程会记进章节 json 的 `adapted` 字段，事后能查这一章是怎么救回来的。

另一个隐蔽点：`content` 只有一个 BOM 或零宽字符时，Python 的 `str.strip()` **不认它们**，
`"﻿".strip()` 仍然为真。这会绕过空回复判定，一路跌到 JSON 解析才报错，
且错误信息打印出来是一片空白，完全没法定位。客户端现在会先剥掉这类不可见字符。

单章正常耗时参考：三次调用串行，抽取输出 3~5k token 是大头，
关掉思考模式后整章 1~4 分钟属正常范围。并发 4 的话全书约 1200 章 ≈ 5~20 小时。

**如果单章动辄十几分钟，先查 `LLM_THINKING` 是不是被打开了**——
实测开着思考模式时每次调用平均输出 5,300 token，其中绝大部分是思维链，
单章能拖到 500~1100 秒，且大概率以空回复告终。

## s2 成本估算

全书 1200 章。每章输入约 3.5k token（原文 + 前文提要 + 解析结果回喂），
输出约 2.5k token，三次调用合计输入约 10k、输出约 4k。
按修订触发率 20% 估，全书约 1300 万输入 token、550 万输出 token。
具体费用按 deepseek-v4-flash 当时的计价算。

## 日志

```
.run/logs/s2_runs.jsonl     每章一行：分数、逐字命中率、修订轮数、耗时
.run/logs/s2_calls.jsonl    每次 HTTP 请求一行：章节、环节、第几次、状态码、耗时、收到字数
.run/logs/s2_errors.jsonl   失败章节及原因
```

`s2_calls.jsonl` 是排查卡顿的第一手材料——哪一章哪个环节慢、有没有在偷偷重试，
一看便知：

```bash
tail -f .run/logs/s2_calls.jsonl
```

`.run/` 不入库。章节 json 里已经带了完整的审查留痕，日志只用于跑批时监控。

---

# 其余脚本

上面全是 `s2` 的内容，因为只有它是长跑批、会出各种幺蛾子。
下面这些都是几秒到几分钟跑完的本地计算，没有故障排查一说。

## s5_repair_quotes —— 把不逐字的引用对齐回原文

体检报告里「逐字命中率不到 100%」的章节，绝大多数不是模型编造，
而是五类**机械性错误**。这个脚本逐类识别、逐条对齐，**不调 API**：

| 修复类型 | 症状 |
| --- | --- |
| 拆分跨段 | 一条引用横跨原文两个自然段，被当成一条 |
| 拆分拼接 | 把同一人相邻两句话拼成了一句 |
| 剥离旁白 | 台词里混进了「他说道」这类叙述 |
| 对齐原文 | 顺手改通顺了几个字（最常见） |
| 补漏台词与人物 | 原文有这句话但没被抽出来 |

```bash
python pipeline/s5_repair_quotes.py --dry-run     # 只报会改什么，不落盘
python pipeline/s5_repair_quotes.py               # 修全书
python pipeline/s5_repair_quotes.py --seqs 9,90   # 只修指定章
```

每次修复都记进 `data/plot/quote_repair_log.json`，含改前改后原文，可逐条复核。

> **对齐算法有个反直觉的坑**：单纯按相似度找最佳匹配会**奖励删字**——
> `不知他怎么了` 与原文的相似度高于 `楼主不知他怎么了`，于是主语被吃掉。
> 所以匹配时在 ±8 字窗口内搜索，并对「落在完整句读边界上」额外加分。
> 改这个脚本前先读代码里 `_boundary_bonus` 的注释。

## s6 / s7 —— 画风选型矩阵

`s6` 出图（**花钱**），`s7` 把图拼成人眼可比的大图。配置在
`production/style_test/matrix.json`：10 个画面目标 × 8 个画风 × 每格 3 张 = 240 张。

```bash
python pipeline/s6_style_matrix.py --doctor    # ① 阶梯式体检：连通/鉴权/模型/尺寸，四级里第一个失败的就是病因
python pipeline/s6_style_matrix.py --dump      # ② 只导出 80 条提示词，人眼过一遍
python pipeline/s6_style_matrix.py --targets T03 --styles S1,S6   # ③ 先试一格
python pipeline/s6_style_matrix.py             # ④ 全量 240 张
python pipeline/s7_contact_sheet.py            # ⑤ 拼对比大图
```

`s7 --only` 可选 `master`（总表）/ `target`（按画面目标）/ `style`（按画风）/ `all`。

> **要改提示词请改 `matrix.json`，不要改 `prompts.md`**。
> `prompts.md` 是 `--dump` 生成的产物，改了会被下次 dump 覆盖。

> **最容易踩的是尺寸**：`1792x1024` 是 dall-e-3 的档位，gpt-image 系列不认，
> 服务端可能直接回 502 而不是明确的参数错误。gpt-image 只接受
> `1024x1024` / `1536x1024` / `1024x1536` / `auto`。脚本会在发图前先做一次
> 尺寸预检并直接告诉你该填什么。

## s8_character_dossier —— 角色卷宗

**写任何角色文档之前必须先跑这一步。** 凭印象写人物等于把没验证过的东西往下游传，
违反原则二。这个脚本把某角色在全书的每一次出现连同上下文收成一份卷宗。

```bash
python pipeline/s8_character_dossier.py --preset 唐真
python pipeline/s8_character_dossier.py --name 某某 --aliases 甲,乙 --window 3
```

产两份：`dossier_名字.json`（机器读，含每段的 seq / 段号 / 上下文）和
`dossier_名字.md`（人读，含别名词频、首现位置、戏份最重的章节）。

别名分三档，这个分层是这个脚本的关键：

| 档位 | 含义 | 例 |
| --- | --- | --- |
| `aliases` | 确定是本人 | 唐真、求法真君、狗安 |
| `ambiguous` | 可能是本人也可能是别人，单独统计不混入 | 「大师兄」 |
| `exclude` | 含别名字样但另有所指，先抹掉再匹配 | 含「魔尊」但不是齐渊的词组 |

`PRESETS` 里已有 14 个核实过的角色，每个都带 `note` 写明别名边界是怎么核实的。
新增角色时**必须**照这个格式写清依据——两个坑已经踩过：
「执法堂长老」和「葛道人」是两个人不是一个人；「二小姐」指红儿不指姚安饶。

## s9 / s10 / s11 / s12 —— 剧情线与分集

```bash
python pipeline/s9_plot_digest.py        # 1200 份分析 → 34.9 万字摘要 + timeline.json
python pipeline/s12_detect_arcs.py       # 检测卷段边界 → arcs.json（软约束）
python pipeline/s10_episode_plan.py      # 分集求解 → episodes.json（268 集）
python pipeline/s11_episode_assets.py    # 每集要哪些角色场景 → episode_assets.json
python pipeline/s13_season_roster.py     # 按季汇总 → season_roster.json/.md
```

`s10` 是个最短路求解器，目标函数 = 时长偏差惩罚 + 断点惩罚，
时长模型是 `语音字数 ÷ 4.5 字/秒 × 1.20`（1.20 由 E01/E02 两集人工试点标定）。
`--dry-run` 只看时长分布不落盘。

`s12` 的产物**只作软约束**，它找的是「核心阵容换人的位置」，
不是「一个故事讲完的位置」——人工回读第一季的 10 个边界只有约六成站得住。
它的能力边界写在脚本头部的 docstring 里，用它的产物前请先读那段。

`s9` 的分块摘要 `digest_p01..p06.md` 是给人（或模型）读来做卷段划分的原材料，
`digest_all.md` 是全书合并版。方法与结论见
[`../doc/08_全书剧情线.md`](../doc/08_全书剧情线.md)。

## s2m_manual —— 人工分析装配器

把手写的分析稿装配成与 `s2` 完全同格式的章节 json，用来产出**人工基准集**
（现有 `production/s01/manual_analysis/` 的 8 章）。它的引用用**位置引用**
（`{"para": N, "q": K}` ＝ 第 N 段的第 K 条引号内容）而不是抄原文，
所以逐字命中率天然是 100%——人抄原文一定会抄错，让程序去取就不会。

## s15_style_guard —— 画风金标准闸门

`doc/04_风格规范.md` 3.1 把画风载体定为**国风三维**，这是全项目的金标准。
`s15` 把「不许和它冲突」这条规矩做成每次都能重跑的检查，不靠人肉 grep。

```bash
python pipeline/s15_style_guard.py                       # 全量，有违规退出码 1
python pipeline/s15_style_guard.py --show-legacy         # 连留痕豁免文件一起列
python pipeline/s15_style_guard.py --path production/s01/prompts/E01
```

**它只扫会被喂给模型的区域**——围栏代码块与引用块。散文、表格、说明性文字不扫：
`doc/04` 里「厚涂的笔触每次重画都是一次重新解释」是定稿理由，不是指令，报它是噪音。

**负面写法是对的，不报。** 判据是按句子看否定词在不在词的前面：
「不做蜡像、塑料、二维厚涂、水墨」整句受「不做」管辖，全句放行；
「风格：半写实厚涂，笔触可见」没有否定，报。

**留痕豁免**在脚本的 `LEGACY_ALLOW` 里，每条都写了理由——
选型过程的文档与配置故意保留旧载体的名字，那是证据不是指令。
加新的豁免要连理由一起写，不要只加路径。

新写角色卡、场景卡、分镜提示词之后跑一次；改风格段之后也跑一次。