# fanrenque

把网络小说《穿越后，系统变成白噪音了怎么办》（码字的画家 著）改编成 **AI 漫剧**。

约 308 集，分 6 季，每季 45~55 集，每集 3~5 章、8~12 分钟。

## 三大原则

1. **最终目标是高质量成片** —— 所有环节谨慎细致，人物场景高一致性
2. **严格遵守原文** —— 原文有多少人物场景就多少，怎么描写就怎么呈现，
   对话、心理活动、关键旁白一个不漏
3. **画面风格朴素真实** —— 严禁浮夸

展开见 [`doc/00_项目总纲.md`](doc/00_项目总纲.md)。

## 目录

```
README.md              本文件
.env.example           配置模板，复制成 .env 填密钥（.env 不入库）
source/                源数据
  ├── *.txt            原文（GB18030 编码）
  ├── novel.json       标准化原文，1200 个分析单元 ★后续一切的输入
  └── novel.index.json 轻量目录（不含正文），做规划时加载这个
data/                  数据资产
  ├── chapters/        逐章深度分析，一章一个 json（阶段二产出）
  ├── characters/      角色资产（阶段三产出）
  ├── scenes/          场景资产（阶段三产出）
  ├── plot/            全书剧情线、伏笔表、质量报告
  └── seasons/         六季划分与分集表
pipeline/              脚本管道 —— 见 pipeline/README.md
  ├── s1_normalize_novel.py    原文标准化
  ├── s2_analyze_chapters.py   逐章分析（调 API）
  ├── s3_validate_chapters.py  全量体检（不调 API）
  ├── selftest.py              离线自测
  ├── config.py                所有可调参数（读 .env）
  ├── common/                  路径、JSON、API 客户端、逐字核验器、进度看板
  ├── prompts/                 四个提示词，改了立即生效
  └── schemas/                 章节分析 JSON 结构约定
production/            具体剧集工作目录（s01/ s02/ …：分析、脚本、草稿、成片）
doc/                   文档
  ├── 00_项目总纲.md
  ├── 01_原文数据说明.md   ★ 含已知缺陷，动手前必读
  └── 02_章节分析规范.md
```

## 进度

| 阶段 | 状态 |
| --- | --- |
| ① 原文标准化 | ✅ 完成 |
| ② 逐章独立分析 | 🔧 脚本就绪，离线自测通过，待本地跑 |
| ③ 资产与结构规划（角色/场景/剧情线/六季分集） | ⏳ 待 ② |
| ④ 风格定稿与资产深化 | ⏳ 待 ③ |
| ⑤ 第一季深化 | ⏳ 待 ④ |
| ⑥ 第一季制作 | ⏳ 待 ⑤ |

## 现在该做什么

```bash
pip install -r requirements.txt
cp .env.example .env                               # 填入 DEEPSEEK_API_KEY
python pipeline/selftest.py                        # 离线验管道，不花 API 额度

python pipeline/s2_analyze_chapters.py --smoke 3   # 先跑 3 章，人工逐字比对
python pipeline/s2_analyze_chapters.py             # 确认质量后跑全书
python pipeline/s3_validate_chapters.py            # 体检
python pipeline/s2_analyze_chapters.py --phase review --force   # 建议：补一轮完整上下文审查
```

跑全书支持**并发**（`--workers 8`）和**断点续传**（默认行为，中断后重跑同一条命令即可续上），
终端会显示总进度和每章内部进度。细节见 [`pipeline/README.md`](pipeline/README.md)。

## ⚠ 两件必须先知道的事

**一、全书不是 1232 章，是 1222 个章节标题、1200 个分析单元。**
作者自己有 14 处重号、5 处跳号；站点又把部分章节合并在一页。
所以主键用文件顺序 `seq`（1~1200），不用作者章号。

**二、第 1115~1213 章（结尾段）内容有缺失，约 22 章的正文没抓下来。**
影响第六季（大结局），前五季原文完整、不影响开工。
明细和处理建议见 [`doc/01_原文数据说明.md`](doc/01_原文数据说明.md) 第三部分。
