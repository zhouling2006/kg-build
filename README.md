# 整章化知识图谱流水线（可独立运行副本 `new/`）

把一本**技术教材**变成可交付的 **知识图谱 + 逐知识点文档** 学习包：

```
源侧(可选 S-1 MinerU) → S0 切章 → S0.5 识图(默认开) → S1 知识单元树 → S2 章内关系
→ S3 图谱物化(uuid) → S4 逐知识点文档 → S5 跨章关系 → S6 全书合并 → S7 图片收集 → S8 交付导出
```

- 实例书：邱锡鹏《神经网络与深度学习》(nndl-v2)，交付在 `deeplearning/run/deliver/`。
- 本目录是从 `f:/xschem` 复制的链路完整快照；改脚本需**双份同步**（见 §9）。
- 实例级操作手册（面向主工作区路径）：`deeplearning/PIPELINE.md`。

---

## 1. 快速上手

```powershell
cd f:/xschem/new
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --dry-run  # 预览：不调 LLM
python scripts/run_pipeline_whole.py --config config/dl_chapter.json           # 一键全链（断点续跑）
```

产物即 `deeplearning/run/deliver/`：`nodes.json / edges.json`（图谱）+ `texts/*.md`（每知识点一篇自包含文档）+ `images/`（插图）。

> 一键全链会跑很久（16 章约 50 次 LLM 调用），且中途可任意断——同一条命令重跑即从断点续。推荐按 §6 的分段跑法。

## 2. 链路完成度：组件全齐，缺的只是执行

| 环节 | 组件 | 实跑情况 |
|---|---|---|
| S-1 源侧 MinerU（可选） | `pdf_to_full_md.py` | 缺 `full.md` 时 cfg 给 `source_pdf` 自动转；本书已转好不触发 |
| S0 切章 | `cut_chapters.py` | ✅ 16 章已切 |
| S0.5 识图（默认开） | `describe_book_images.py` | ✅ 第 1/4 章插图已带描述 |
| S1 骨架 / S2 关系 / S3 物化 | `extract/relate/materialize` | ✅ 第 1、4 章；其余 14 章待跑 |
| S4 逐知识文档 | `gen_docs.py` | ✅ 第 1、4 章 18+18 篇；其余待跑 |
| S5 跨章 / S6 合并 / S7 图片 / S8 交付 | 纯脚本 | ✅ 已出 `deliver/`（当前=第 1+4 章子集） |

**结论：一条命令即可从本书跑到交付；16 章整链编排已 dry-run 验证无错。** 未完成 = 把 14 章喂进去跑一遍（约数小时 + LLM 费用），不是缺组件。

## 3. 书要长什么样才能喂进来？（切章自适应）

**默认适配「第 N 章」形态的教材**：章标题为行首（可带 1~6 个 `#` 或没有）`第 N 章`，编号支持阿拉伯或中文数字（"第3章""第三章"均可），章字后可直接跟标题。自动防御两处常见噪音：

- 书末「附录 / 答案 / 参考文献 / 索引」段只从**最后一个正文章之后**开始截断——每章末尾自带的"参考文献"不会误伤全书；
- 章内「习题 / 练习 / 思考题 / 小结 / 实验…」区段自动剔除。

**章命名不是"第 N 章"的书**（如 `Chapter 1 / Unit 3 / 1.1 绪论`），不改脚本，在 cfg 的 `chapter_mode` 里覆盖即可：

```json
"chapter_mode": {
  "title_re": "^#{1,6}\\s*第\\s*([0-9]+|[一二三四五六七八九十百]+)\\s*章",  // 章标题正则（默认值）
  "truncate_book_at": ["附录", "答案", "参考文献", "索引"],                  // 书末附属区起点关键词
  "strip_sections": ["习题", "练习", "思考题", "复习题", "自测题", "小结", "实验"], // 章内剔除区段
  "exclude_chapters": []                                                    // 排除整章：章号 int 或标题关键词
}
```

| 字段 | 作用 | 适配场景 |
|---|---|---|
| `title_re` | 覆盖整个章标题正则（必须含一个捕获组给章编号） | `Chapter 1`、`UNIT I`、`第壹讲`… |
| `truncate_book_at` | 书末截断：取最后一个正文章之后最先出现的这些标题 | 附录/词汇表/索引叫法不同 |
| `strip_sections` | 章内剔除：匹配到的标题起至章末移除 | 章末练习、关键术语表等 |
| `exclude_chapters` | 跳过指定章 | 序言章/选读章 |

诚实边界：默认「第 N 章」路径已被 16 章实跑验证；`title_re` 自定义路径**接口已就位但尚未用真实第二本书跑过**，首次换"非第N章"书时建议先 `--dry-run` 预览切章清单再放量。

## 4. S0.5 识图：干什么、是不是"能跑"

不是静态描述——它**真实调用视觉模型** `qwen-vl-max` 逐图"看图说话"，三步：

1. **scan**：扫章 md 的插图引用，用 MinerU 的 `*_content_list.json` 把公式图排除（公式已由 MinerU 转 LaTeX 内联，无需看图）；
2. **vl**：每张普通图连同**它前后各 N 行教材原文**发给 VL，判定 `relevant`（给一句话"类型+关键特征"）或 `irrelevant`（水印/二维码/logo/装饰）；
3. **write**：相关图 → 图片行后写回 `（描述：…）`；无关图 → 替换成 `<!-- 已忽略… -->` 占位。

第 1 章已实跑，章 md 里真实长这样（截图自 `run/chapters/01_绪论.md`）：

```markdown
![](images/9109….jpg)（描述：时间轴图，展示了人工智能从1940年到2025年的发展历程，
分为推理期、知识期、学习期和大模型期，并标注了关键事件和里程碑）
![](images/cb3e….jpg)（描述：流程图，展示传统机器学习的数据处理流程，
包括原始数据到结果的各个步骤，重点标注了特征处理和浅层学习环节）
![](images/19de….jpg)（描述：三维one-hot向量空间与二维嵌入空间的对比示意图，
展示三个样本在不同空间中的位置关系）
```

关键语义：图说**写回章原文**后，S1 骨架抽取、S4 文档生成时 LLM 能"看懂"每张图，做更准的知识关联与插图引用。最终文档里是否带出这句说明由生成端按需保留/精简，**不强制透传**（这是文档提示词的既定语义）。

开关与成本：

| 项 | 值 |
|---|---|
| 开关 | cfg `image_config.image_desc`。**可整块省略**：`mineru_dir` 未配/无效时自动从 `source` 推导（MinerU 布局固定：`full.md` 的父目录即 MinerU 根）；`image_desc` 未给则按"推导出的图源是否真实存在"自动开/关；`desc_out` 缺省 = 运行目录 `image_meta` |
| 显式覆盖 | 仍可显式写 `image_config`：`mineru_dir`（MinerU 产物，根或 `…/images` 均可，已归一）、`desc_out`（缓存/报告目录）、`desc_workers`（并发）；显式给了就尊重，无效才回落推导 |
| 计费 | 按实际调 VL 的图张数；`desc_out/vl_cache.json` 按 `文件名:大小` 缓存——重跑/补章命中缓存不扣费 |
| 触发时机 | 编排挂在 S0 之后，`--from 0` 的范围才跑（重扫幂等，已带图说的行原样保留不重复写） |
| 直跑 | `python scripts/describe_book_images.py --mode all --target <md> --mineru-dir <MinerU根> --out-dir <缓存目录>` |

## 5. 各部分输入 / 输出

> 编排器 `run_pipeline_whole.py` 负责装配；脚本均可 `python scripts/<名>.py --help` 看参数。

| # | 脚本 | 输入 → 输出 | LLM | 触发 / 费用 |
|---|---|---|---|---|
| S-1 | `pdf_to_full_md.py` | `source_pdf` → `full.md` + `images/` | 外部(MinerU) | 换新书且没先转时自动触发 |
| S0 | `cut_chapters.py` | 整书 `full.md` → `chapters/NN_章.md` + `chapter_index.json` | 否 | index 缺失或 `--force` |
| S0.5 | `describe_book_images.py` | 章 md + MinerU 根 → 图行后写回 `（描述：…）` | qwen-vl-max | 图源自推导（`source` 同目录 `images/`），开关缺省随图源自动开；阶段含 0 才跑；按图缓存 |
| S1 | `extract_chapter.py` | 章 md → `chapter_skeleton.json`（分组 index + topic 叶子，带导读） | 是，1 次/章 | 骨架缺失；按整章字符计费 |
| S2 | `relate_chapter.py` | skeleton → `relations.json`（先修/并列/学习路径） | 是，1 次/章 | relations 缺失 |
| S3 | `materialize_chapter.py` | skeleton+relations+章 md+index → `chapter_graph.json`（uuid 节点/树/关系边） | 否 | 内置：标题重复、引用缺失、同名压层、关系边归一校验 |
| S4 | `gen_docs.py` | chapter_graph + 章 md → `docs_all/<章>/seq-title.md` | 是，批式 | 编排默认 `--skip-existing` 逐叶断点；`--force` 全量重写 |
| S5 | `cross_chapter_links.py` | 全部章图 → `cross_links.json` | 是，1 次 | 章数 >1 才跑；句柄越界报错防编造 |
| S6 | `merge_books.py` | 章图 + cross → `book_graph.json` | 否 | 关系边全局归一（同对唯一边） |
| S7 | `backfill_images.py` | docs 图引用 + MinerU images → `run/images/` | 否 | 幂等，只收 docs 实际引用到的图 |
| S8 | `export_from_graph.py` | book_graph + docs + images → `run/deliver/` | 否 | 插图引用自动重写为 `../images/`，交付自包含 |

辅助脚本：`rel_normalize.py`（关系边归一工具，被 S3/S5/S6 import）、`billing.py`（计费账本）、`_tmp_collect_imgs.py`（把图收到 `docs_all/<章>/images/` 供本地预览）。

## 6. 运行方法与断点

| 参数 | 作用 |
|---|---|
| `--config` | 顶层配置（必填；相对路径基于 `new/` 根） |
| `--from / --to` | 阶段范围：S0=0 … S8=8；不含 0 则不触发 S0/S0.5 |
| `--only 章[,章]` | 只处理指定章 |
| `--force` | LLM/纯脚本产物已存在也重跑（S4 将全量重写全部叶子） |
| `--skip-cross` | 跳过 S5 跨章关系 |
| `--dry-run` | 不调 LLM：纯脚本照跑；LLM 阶段只写 prompt 预览后退出 |

**铺全书的推荐顺序**（每步断点续跑互不干扰）：

```powershell
# ① 16 章 骨架 + 关系 + 物化（最耗时，LLM 2 次/章）
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 1 --to 3

# ② 逐知识文档，按章分批跑（第 1/4 章已有，自动跳过）
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 4 --to 4 --only 5,6
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 4 --to 4 --only 7,8   # …以此类推

# ③ 跨章 → 全书合并 → 图片 → 交付
python scripts/run_pipeline_whole.py --config config/dl_chapter.json --from 5 --to 8
```

费用量级（供规划）：S4 实测第 1 章（18 篇 / 25.6k 字符原文）≈ ¥0.6，厚章按原文字符线性上浮；S1 与 S4 同级量级（整章原文一次入模）；S0.5 按实际插图张数，缓存后为 0；S0/S3/S6/S7/S8 免费。总预算按 16 章建议先跑 1~2 个厚章评估再放量。

## 7. 交付契约（`run/deliver/`）

| 文件 | 内容 |
|---|---|
| `nodes.json` | 全部节点：`index`（分组/章目录）与 `topic`（知识点），含 uuid/id/title/summary |
| `edges.json` | topic↔topic 关系边（先修/并列；reason 入 note；**同对唯一边**） |
| `index_edges.json` | contains 树边（章 → 分组 → topic） |
| `aux_edges.json` | 预留（本链路为空） |
| `texts/*.md` | 每 topic 一篇文档；`frontmatter id` = `nodes.json` 节点 id（权威关联） |
| `images/` | 插图全集（texts 内 `../images/` 相对引用，交付自包含） |
| `README.md` | 交付说明 |

质量闸点内建：S3 校验叶子数/引用缺失/标题重复 → merge 后边端点零缺失 → S8 导出自检 → `nodes ↔ texts` 双向一一对应。

## 8. 换一本新书（复用性）

**主链路 9 个脚本零代码改动即可换书**——prompts 无领域残留、scripts 无 `f:/xschem` 等绝对路径硬编码、领域信息全收在顶层 cfg。换书只需 4 步：

| # | 做什么 | 说明 |
|---|---|---|
| 1 | MinerU 产出新书 `full.md` + `images/` | `scripts/pdf_to_full_md.py`（需 MinerU token）；或让 S-1 自动转：cfg 只给 `source_pdf` |
| 2 | 复制 `config/dl_chapter.json` 改名 | 只改 `domain/source/source_pdf/chapter_out_dir/subject_name/subject_tag` 五处即可——**`image_config` 整块可删**：图源（MinerU `images/`）会自动从 `source` 同目录推导，识图开关随图源真实可用自动开，缓存落在 `运行目录/image_meta`。想关识图再补 `image_config: {"image_desc": false}`；想自定义图源/并发再写 `mineru_dir`/`desc_workers` |
| 3 | 按 §6 跑 `run_pipeline_whole.py --config 新配置.json` | `chapter_out_dir` 用新目录，与原书互不污染 |
| 4 | 可选微调 `chapter_mode` | 章标题非「第 N 章」给 `title_re`；书末/章内区段叫法不同给 `truncate_book_at`/`strip_sections`（见 §3） |

## 9. 已知边界与坑

1. **双份同步**：`new/` 是 `f:/xschem` 的副本，脚本改动须两边一致（历史修复均主 + `new` 双写）。
2. **PowerShell 中文路径乱码**：命令行 argv 含中文会被 GBK 破坏。规避：命令行只传 ASCII 路径（`config/dl_chapter.json`）；中文路径写在 cfg/脚本内部（UTF-8），不经 argv。
3. **文档本地预览断图**：docs 引用 `images/xxx.jpg`，先跑 `scripts/_tmp_collect_imgs.py` 收图到 `docs_all/<章>/images/` 即可预览；交付渲染不受影响（S7/S8 已处理）。
4. **缺图上限**：MinerU 有约 32 张图未切出（引用保留但无法解析），属已知上限，不影响链路。
5. **识图缓存目录共用**：`desc_out` 是全书共用的 VL 缓存；`--only 章` 识图时只处理该章、缓存累积在同一个 `vl_cache.json`，换章不重复扣费。
6. **残留命名**：计费 tracker 里的 `cn` 字样、`describe` 直跑不带 `--mineru-dir` 的兜底默认，均为旧链路的无害残留（经编排器跑时总是显式传参）。
