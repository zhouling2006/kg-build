# 深度学习整章化文档流水线（PIPELINE）

> 实例：`《神经网络与深度学习》nndl-v2` → 章节图谱 → 每知识点一份自包含 Markdown 文档（可带原书插图）。
> 同一条流水线已在《计算机网络》《操作系统》《数据结构》等书复用，本文以 `deeplearning` 实例给出完整操作手册。

## 0. 一句话概括

```
PDF ──MinerU──▶ 整书 md ──切章──▶ 16 个章节 md ──S1骨架──▶ 知识单元树
   ──S2关系──▶ 章内依赖/并列边 ──S3物化──▶ 每章 uuid 图谱
   ──S4文档──▶ docs_all/<章>/*.md（每叶子一篇，含图）──S5/S6──▶ 全书图谱
   ──S7/S8──▶ deliver/（nodes.json + texts/ + images/ 交付）
```

## 1. 需要准备的东西

| 类别 | 内容 | 说明 |
|---|---|---|
| 原书 | `deeplearning/nndl-v2.pdf` | MinerU 转换输入；若 MinerU 产物还在则无需重转 |
| MinerU 产物 | `deeplearning/_mineru/nndl-v2/full.md` + `images/`（整书 md + 全部切图，sha 文件名） | 切章/识图/图片收集的源 |
| API Key | 环境变量 `DASHSCOPE_API_KEY` | 阿里云百炼（DashScope）OpenAI 兼容端点 |
| 文本模型 | `qwen3.7-plus` | S1 骨架 / S2 关系 / S4 文档，temperature 低 |
| 识图模型 | `qwen-vl-max` | S0.5 插图描述（可选，只跑一次、按图缓存） |
| Python 依赖 | `openai`、`requests` 等 | `mineru_client.py` / `run_pipeline_whole.py` 运行环境 |

## 2. 目录布局

```
deeplearning/
├── nndl-v2.pdf                    # 原书（输入）
├── _mineru/nndl-v2/
│   ├── full.md                    # MinerU 整书 md
│   └── images/<sha>.jpg           # 整书切图（1651 张，含跨 chunk 冗余）
├── run/                           # 整条主链路的全部中间产物（章节、图谱、文档、交付）
│   ├── chapters/01_绪论.md … 16_深度生成模型.md   # S0 切章产物（含识图写回描述）
│   ├── chapter_index.json         # 章边界表（每章起止行号/文件/字数）
│   ├── per_chapter/<no>_<章>/
│   │   ├── chapter_skeleton.json  # S1 骨架（树：index 分组 + topic 叶子）
│   │   ├── relations.json         # S2 关系（prerequisite_of / parallel_to / learn_path）
│   │   └── chapter_graph.json     # S3 物化图谱（nodes + tree_edges + rel_edges，权威 uuid）
│   ├── docs_all/<no>_<章>/        # S4 文档（每叶子一篇 .md，序号前缀 + 同名 images/）
│   ├── run_cfg/                   # 各阶段自动生成的 cfg json
│   ├── book_graph.json            # S6 全书合并图谱
│   └── deliver/                   # S8 交付（nodes.json/edges.json/index_edges.json/texts/images/README）
├── dl_cfg.json                    # （本项目早期残留，非主配置）
└── PIPELINE.md                    # 本文档
```

主配置：`config/dl_chapter.json`（顶层，流水线读它）。

## 3. 各阶段明细

### S0 切章（纯脚本，无 LLM）
- 脚本：`scripts/cut_chapters.py --config config/dl_chapter.json`
- 输入：整书 md（`source` 字段）；输出：`chapters/*.md` + `chapter_index.json`
- 要点：只认**最后一个章标题之后**出现的「附录/索引/参考文献」作为书末截断点，正文各章末尾的参考文献标题一律不切（防误截）。

### S0.5 识图（可选，LLM 看图，按图缓存只付一次）
- 脚本：`scripts/describe_book_images.py`
  - `--mode scan`：只扫描章节 md 中的图片引用，分普通图/公式图/未匹配，不调 VL；
  - `--mode all`：对普通图调 qwen-vl-max 生成中文描述 → 写回 md 图行后 `（描述：…）`。
- 作用：给 **S1 骨架 / S4 文档**的 LLM 提供"图是干什么的"的文字上下文。注意：后续 LLM **看不到像素**，只读得到这行文字 + 正文讲解；若教材正文已充分讲解某图，该图不带描述也可行。
- 公式图（MinerU 已内联为 LaTeX）自动跳过；未匹配图（源目录找不到文件）不硬编。

### S1 骨架（每章 1 次 LLM）
- 脚本：`scripts/extract_chapter.py --cfg run/run_cfg/extract_<no>.json`
- cfg：`chapter_md / chapter / out / model / tracker`
- 输出：`chapter_skeleton.json`（3 层树：分组 index → 叶子 topic，非叶子带导读 summary）。
- 成本参考：每章一次调用（整章原文 25~86K 字符入 prompt），约 ¥0.3~1。

### S2 关系（每章 1 次 LLM）
- 脚本：`scripts/relate_chapter.py --cfg run/run_cfg/relate_<no>.json`
- 输出：`relations.json`：叶子间 `prerequisite_of`（带 reason）、`parallel_to`、章内学习路径。
- 校验：叶子数与骨架一致、无缺失引用（materialize 阶段汇总报告）。

### S3 物化（纯脚本）
- 脚本：`scripts/materialize_chapter.py --chapter-index … --chapter-no N --skeleton … --relations … --chapter-md … --out …`
- 输出：`chapter_graph.json`（nodes 全 uuid4，tree_edges contains，rel_edges 关系边）。
- 内置清洗：title 去重校验、引用缺失校验；「分组与叶子同名」时无损压层（index 只有一个同名 topic 子节点 → 叶子上移顶替）。

### S4 文档（每叶子 1 次 LLM，章内并发）
- 脚本：`scripts/gen_docs.py --cfg run/run_cfg/docs_<章>.json [--only 1,2,3] [--skip …] [--max-workers 3]`
- 输出：`docs_all/<章>/<seq>-<title>.md`
- 每篇结构：YAML frontmatter（`title/id/aliases/tags/description/terms/source`）+ `# 标题` + `##` 小节 + 关键段 `<!-- @anchor-begin … -->` 锚点 + 末尾 `## 相关知识点`。
- **相关知识点**：候选来自图谱边（前置基础·先学 / 直接应用·后学 / 并列对比·关联），每条 `[[标题]]{type=topic, label=标题, render=link}` 后附一句"为什么相关"。
- **插图保留**：整章原文中与本叶相关的插图行（`![](images/xxx.jpg)`）必须保留，引用路径一字不改；`（描述：…）` 辅助说明可留可略；图题（如"图4.2 …"）独立成行时保留；禁止伪造图片路径。
- 成本参考：每篇约 ¥0.1（整章原文作为上下文）；第 4 章 18 篇 ≈ ¥1.8 / 8 分钟。

### S5 跨章关系（可选，LLM）
- 脚本：`scripts/cross_chapter_links.py --graphs 'per_chapter/*/chapter_graph.json' --out run/cross_links.json`
- 跨章同名/同义 topic 之间补 prerequisite/parallel 边。

### S6 全书合并（纯脚本）
- 脚本：`scripts/merge_books.py --graphs 'per_chapter/*/chapter_graph.json' [--cross …] --out run/book_graph.json`
- 输出全书唯一节点 + 树/关系边。

### S7 图片收集（纯脚本）
- 脚本：`scripts/backfill_images.py --docs <docs目录> --images-src <mineru images 目录> --out-images <输出>`
- 幂等收集 docs 引用到的图。
- ⚠️ 本项目自定义：docs 里引用是 `images/xxx.jpg`（相对文档所在章目录），需用
  `scripts/_tmp_collect_imgs.py`（一次性工具）按章把图复制到 `docs_all/<章>/images/`，
  使文档本地预览可解析。书籍交付（deliver/images）另由 S8 统一落图。

### S8 交付导出（纯脚本）
- 脚本：`scripts/export_from_graph.py --graph book_graph.json --texts docs_all --images run/images --out run/deliver`
- 字段对齐 ds 交付约定：`nodes.json`（course/index/topic）/ `edges.json`（topic↔topic）/ `index_edges.json`（contains 树）/ `aux_edges.json`（空）/ `texts/` / `images/` / `README.md`。

## 4. 一条命令跑全书（断点续跑）

```powershell
cd f:/xschem
# S1–S3：全部 16 章骨架+关系+物化（已有产物自动跳过）
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 1 --to 3
# S4：全部 16 章文档（长任务，可按章分批：--only 5,6,7）
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 4 --to 4 --only 1,2,3
# S5–S8：跨章 → 合并 → 图片 → 交付
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 5 --to 8
```

参数：`--from/--to` 阶段范围；`--only 章号` 限定章；`--force` 强制重跑已有 LLM 产物；
`--dry-run` 不调 LLM、LLM 阶段只落 prompt 预览。

顶层配置字段（`config/dl_chapter.json`）：

```json
{
  "domain": "深度学习",
  "pipeline": "whole_chapter",
  "source": "deeplearning/_mineru/nndl-v2/full.md",
  "model": "qwen3.7-plus",
  "subject_name": "深度学习",
  "subject_tag": "神经网络",
  "chapter_out_dir": "deeplearning/run",
  "chapter_mode": { "exclude_chapters": [] },
  "image_config": { "image_desc": false, "mineru_dir": "deeplearning/_mineru/nndl-v2/images" }
}
```

## 5. 计费与速率

- 每阶段账单在运行日志尾部 `💰 API 计费摘要 [tracker]` 打印（累计到 `run/run_cfg/*.json` 的 tracker 名）。
- S4 是主要开销（每叶子一次调用、整章原文入上下文）；S0/S3/S6/S7/S8 免费。
- 识图 S0.5 按图缓存：同一张图只付一次 VL 费用，重跑不再扣费。

## 6. 常见坑

1. **PowerShell 中文路径乱码**：命令行参数（argv）中出现中文路径会被 GBK 转码破坏。
   规避：命令行只传 ASCII 路径（如 `config/dl_chapter.json`）；含中文的路径写死在 Python
   脚本内部（UTF-8 源码）再执行；`run_pipeline_whole.py` 内部 subprocess 用宽字符传参不受影响。
2. **同名 topic/分组**：S1 骨架偶发分组与叶子同名（违反唯一性），已由 S3 压层修复。
3. **每章末尾的「参考文献」**：不能当书末截断点，截断逻辑只认最后一个章标题之后的附录区。
4. **图引用断链**：docs 引用的图需收集到 `docs_all/<章>/images/` 才能本地渲染；
   缺失图（MinerU 未切出的 32 张）保留引用但无法解析，属已知上限。
