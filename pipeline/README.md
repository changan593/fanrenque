# pipeline —— 脚本管道

按编号顺序跑。每个脚本只做一件事，产物落到固定位置，可断点续跑。

```
s1_normalize_novel.py   原文 txt ──→ source/novel.json          （不调 API）
s2_analyze_chapters.py  逐章分析 ──→ data/chapters/chXXXX.json  （调 API，每章 3~4 次）
s3_validate_chapters.py 全量体检 ──→ data/plot/quality_report.json（不调 API）
s4_build_assets.py      资产聚合 ──→ data/characters/、data/scenes/（待第 3 阶段实现）

selftest.py             离线自测，用假模型跑通全流程，不花 API 额度
config.py               所有可调参数集中在这里
```

支撑模块：

```
common/paths.py      全项目路径的唯一来源，别在别处拼路径
common/jsonio.py     原子写 JSON、从模型回复里抠 JSON
common/novel.py      novel.json 的读取助手
common/llm.py        DeepSeek 客户端：强制 JSON、退避重试、限速、用量统计
common/verbatim.py   逐字核验器 —— 整条管道唯一不依赖模型判断的客观闸门
prompts/             四个提示词，纯文本，改了立即生效，不用动代码
schemas/             章节分析 JSON 的结构约定
```

## 快速开始

```bash
pip install -r requirements.txt

python pipeline/s1_normalize_novel.py      # ① 标准化原文（已跑过，产物已入库）
python pipeline/selftest.py                # ② 离线自测，确认管道通

export DEEPSEEK_API_KEY=sk-xxx
python pipeline/s2_analyze_chapters.py --smoke 3   # ③ 先跑 3 章，人工看一眼质量
python pipeline/s2_analyze_chapters.py             # ④ 跑全书，中断了重跑会自动续
python pipeline/s3_validate_chapters.py            # ⑤ 体检，拿到需要重跑的章节列表
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

## 常用参数

| 命令 | 作用 |
|---|---|
| `--smoke 3` | 只跑前 3 章，验管道 |
| `--range 100 200` | 只跑 seq 100~200 |
| `--force` | 覆盖已有结果（默认跳过已完成的章节） |
| `--workers 8` | 并发数，默认 4 |
| `--phase review` | 复用已有抽取结果，只重跑两道审查 |

`--phase review` 的用途：首轮并发跑的时候，靠后的章节可能读不到紧邻前几章的简介
（还没落盘），连贯性审查的上下文会略弱。全书跑完后再跑一次 `--phase review --force`，
此时全书简介都在，能拿到完整上下文的审查结论，成本只有一次审查的钱。

环境变量可临时覆盖配置：`DEEPSEEK_MODEL`、`PIPELINE_WORKERS`、`PIPELINE_QPS`、
`PIPELINE_REPAIR_ROUNDS`、`LLM_TIMEOUT`。

## 成本估算

全书 1200 章。每章输入约 3.5k token（原文 + 前文提要 + 解析结果回喂），
输出约 2.5k token，三次调用合计输入约 10k、输出约 4k。
按修订触发率 20% 估，全书约 1300 万输入 token、550 万输出 token。
具体费用按 deepseek-v4-flash 当时的计价算。

## 日志

```
.run/logs/s2_runs.jsonl     每章一行：分数、逐字命中率、修订轮数、耗时
.run/logs/s2_errors.jsonl   失败章节及原因
```

`.run/` 不入库。章节 json 里已经带了完整的审查留痕，日志只用于跑批时监控。
