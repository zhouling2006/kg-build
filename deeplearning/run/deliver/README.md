# 深度学习 知识图谱交付说明（整章化 v1）

## 1. 概述
本交付为整章化流水线产物：按章抽取骨架 → 物化(uuid 图谱) → 叶子文档 → 全书合并 → 导出。

- 节点总数：**66**（course 1 / index 29 / topic 36）
- 边总数：**119**（edges 54 + index_edges 65）
- 关系类型：`prerequisite_of` / `parallel_to`（edges.json）、`contains`（index_edges.json）

## 2. 目录结构
```
deliver/
├── nodes.json          # 全部节点（course/index/topic）
├── edges.json          # topic↔topic 知识关系边
├── aux_edges.json      # 空（整章化无 question/application）
├── index_edges.json    # index 树 contains（course→章→…→topic）
├── texts/              # 每个 topic 一份 md（文件名=label，label 冲突时带短 id 后缀）
├── images/             # 插图（21 个）
└── README.md
```

## 3. 节点字段
| 字段 | 说明 |
|------|------|
| node_id | uuid，与 docs 文档 frontmatter 的 id 一致（topic 可互查） |
| node_kind | course / index / topic |
| label | 节点标题（= 目录索引项 / 知识点标题） |
| index_content | index：导读文本（骨架非叶子 summary；章根 level0 为"覆盖主题：…"合成） |
| note | topic：叶子 summary；index：直接子项摘要（树结构见 index_edges.json） |

## 4. texts/ 与 nodes 的对应
texts 内每个 md 的 frontmatter `id` = nodes.json 中对应 topic 的 node_id；
index 层（章/主题/分组）无独立文档，其导读内容在 nodes.json 的 note / index_content。

## 5. 快速使用
```python
import json
nodes = json.load(open('deliver/nodes.json', encoding='utf-8'))['nodes']
edges = json.load(open('deliver/edges.json', encoding='utf-8'))['edges']
index = json.load(open('deliver/index_edges.json', encoding='utf-8'))['edges']
topics = [n for n in nodes if n['node_kind'] == 'topic']
```
