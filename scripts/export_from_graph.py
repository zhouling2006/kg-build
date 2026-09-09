#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出交付（整章化流水线 Stage 8，纯脚本，无 LLM）：
  book_graph.json（全书图谱）+ docs（每 topic 一份 md）→ 交付目录（默认 deliver/）

字段契约对齐 export_ds_format.py（nodes.json / edges.json / aux_edges.json /
index_edges.json / texts/ / images/）：
  nodes.json      全部节点（course 根 + index + topic）
  edges.json      topic ↔ topic 知识关系（prerequisite_of / parallel_to）
  aux_edges.json  空（整章化无 question/application，保留空文件对齐约定）
  index_edges.json  index 树 contains（course→章→…→topic）
  texts/          每 topic 一个 md（文件名 = 节点 label，label 冲突时加短 id）
  images/         插图资源
  README.md       交付说明（简化版）

节点字段：node_id / node_kind / label / topic_id / index_content / question_id / note
- 全书根(level:-1) → node_kind=course（沿用其 id，供边挂载）
- 章根/主题/分组(index) → node_kind=index，index_content=骨架导读（summary；
  章根 level:0 无骨架 summary → 合成"覆盖主题：…"），note=直接子项摘要
- topic（叶子） → node_kind=topic，topic_id=node_id，note=summary

用法：
  python scripts/export_from_graph.py \
      --graph intermediate/cn_chapters/book_graph.json \
      --texts intermediate/cn_chapters/ch5_docs \
      --images intermediate/cn_chapters/images \
      --out intermediate/cn_chapters/ds
"""
import sys, io, json, re, shutil, uuid, argparse
from pathlib import Path
from collections import Counter, defaultdict

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = name.replace('：', '-').replace('，', ',')
    name = name.replace('（', '(').replace('）', ')')
    name = re.sub(r'[\\/*?:"<>|$`{}^]', '', name)
    name = name.replace(' ', '-')
    return (name[:80]).strip('.-') or 'untitled'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--graph', required=True)
    ap.add_argument('--texts', default='', help='docs 目录（每个 topic 一份 md）')
    ap.add_argument('--images', default='', help='图片资源目录（backfill_images 产物）')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    g = json.loads(Path(args.graph).read_text(encoding='utf-8'))
    course = g.get('domain', '') or '知识图谱'
    nodes, tree_edges, rel_edges = g['nodes'], g['tree_edges'], g['rel_edges']
    by_id = {n['id']: n for n in nodes}

    # 子树关系：父 → 直接子（tree_edges contains）
    children = defaultdict(list)
    for e in tree_edges:
        if e['rel'] == 'contains':
            children[e['from']].append(e['to'])

    # ---- 节点转换 ----
    out_nodes = []
    label_count = Counter(n.get('title', '') for n in nodes)
    for n in nodes:
        if n['id'] == g.get('root_id'):
            ch_titles = [by_id[c].get('title', '') for c in children.get(n['id'], [])]
            out_nodes.append({
                'node_id': n['id'], 'node_kind': 'course',
                'label': course, 'topic_id': '', 'index_content': '',
                'question_id': '',
                'note': f"{course}知识图谱 - 涵盖{'、'.join(ch_titles)}章", })
            continue
        kind = n['kind']  # index | topic
        kids = children.get(n['id'], [])
        kid_objs = [by_id[k] for k in kids]
        summary = n.get('summary') or ''
        # 清单信息（树结构已由 index_edges.json 表达，这里只做可读摘要）
        sub = ''
        if kid_objs:
            sub = '包含：' + '、'.join(o['title'] for o in kid_objs[:8])
        if kind == 'topic':
            m = {'node_id': n['id'], 'node_kind': 'topic',
                 'label': n.get('title', ''), 'topic_id': n['id'],
                 'index_content': '', 'question_id': '',
                 'note': summary}
        else:
            # index：index_content = 导读（骨架非叶子 summary：自然覆盖"讲什么/为什么学/学了有什么用"，自由组织）；
            # 章根（level 0，无骨架 summary）→ 合成"覆盖主题：…"作导读
            ic = summary
            if not ic and kid_objs:
                names = [o['title'] for o in kid_objs[:8]]
                prefix = '覆盖主题：' if n.get('level') == 0 else '包含：'
                ic = prefix + '、'.join(names)
                if len(kid_objs) > 8:
                    ic += f" 等{len(kid_objs)}项"
            m = {'node_id': n['id'], 'node_kind': 'index',
                 'label': n.get('title', ''), 'topic_id': '',
                 'index_content': ic, 'question_id': '',
                 'note': sub}
        out_nodes.append(m)

    id_set = {m['node_id'] for m in out_nodes}
    kind_of = {m['node_id']: m['node_kind'] for m in out_nodes}
    label_of = {m['node_id']: m['label'] for m in out_nodes}

    # ---- 边转换 ----
    def conv(e, rel_type=None):
        return {'edge_id': str(uuid.uuid4()),
                'source_node_id': e['from'], 'target_node_id': e['to'],
                'source_title': label_of.get(e['from'], ''),
                'target_title': label_of.get(e['to'], ''),
                'relation_type': rel_type or e.get('rel', ''),
                'note': e.get('note', '')}

    out_topic = [conv(e) for e in rel_edges
                 if kind_of.get(e['from']) == 'topic'
                 and kind_of.get(e['to']) == 'topic']
    out_index = [conv(e, 'contains') for e in tree_edges
                 if e['rel'] == 'contains']
    out_aux = []

    # ---- 写 json ----
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    def dump(obj, p):
        (out / p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                             encoding='utf-8')
    dump({'nodes': out_nodes}, 'nodes.json')
    dump({'edges': out_topic}, 'edges.json')
    dump({'edges': out_aux}, 'aux_edges.json')
    dump({'edges': out_index}, 'index_edges.json')

    # ---- texts/ ----
    n_texts = 0
    text_dir = out / 'texts'
    text_dir.mkdir(exist_ok=True)
    if args.texts:
        src_dir = Path(args.texts)
        md_candidates = list(src_dir.rglob('*.md'))  # docs 可能按章分目录
        topic_m = [m for m in out_nodes if m['node_kind'] == 'topic']
        used = Counter()
        for m in topic_m:
            # docs 文件名以序号开头；用 label 或 frontmatter 匹配
            target = sanitize_filename(m['label'])
            if label_count[m['label']] > 1:
                target = f"{target}-{m['node_id'][:8]}"
            dst = text_dir / f"{target}.md"
            if dst.exists():
                n_texts += 1  # 增量续跑：已存在视为已匹配
                continue
            # 找对应源文件：frontmatter id == node_id 优先
            found = None
            for f in md_candidates:
                try:
                    head = f.read_text(encoding='utf-8', errors='ignore')[:500]
                    if re.search(rf'^id:\s*{m["node_id"]}\s*$', head, re.M):
                        found = f
                        break
                except Exception:
                    continue
            if found is None:
                found = next((f for f in md_candidates
                              if m['label'] in f.stem or f.stem in m['label']), None)
            if found:
                shutil.copy2(found, dst)
                n_texts += 1
        # 清理陈旧 texts（上一轮产物中被删 topic 的残留）
        valid_ids = {m['node_id'] for m in topic_m}
        for f in list(text_dir.glob('*.md')):
            head = f.read_text(encoding='utf-8', errors='ignore')[:300]
            m = re.search(r'^id:\s*(\S+)\s*$', head, re.M)
            if not m or m.group(1) not in valid_ids:
                f.unlink(missing_ok=True)
        # 图片引用重写为交付相对形态：texts/*.md 位于 texts/，插图在 ../images/。
        # 源 docs 里是 images/xx.jpg（与文档同级），落到交付后必须 ../images/ 才可解析。
        # 幂等：仅匹配恰好 images/ 开头且未带 .. 的引用。
        img_re = re.compile(r'(!\[[^\]]*\])\((images/[^)\s]+)\)')
        n_rewrite = 0
        for f in list(text_dir.glob('*.md')):
            t = f.read_text(encoding='utf-8')
            nt = img_re.sub(r'\1(../\2)', t)
            if nt != t:
                f.write_text(nt, encoding='utf-8')
                n_rewrite += 1
        if n_rewrite:
            print(f'🔗 图片引用重写 {n_rewrite} 个 md（images/ → ../images/）')

    # ---- images/ ----
    n_images = 0
    img_dir = out / 'images'
    img_dir.mkdir(exist_ok=True)
    if args.images:
        src_img = Path(args.images)
        for f in src_img.iterdir():
            if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                dst = img_dir / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
                n_images += 1

    # ---- README（简化） ----
    kinds = Counter(m['node_kind'] for m in out_nodes)
    readme = f"""# {course} 知识图谱交付说明（整章化 v1）

## 1. 概述
本交付为整章化流水线产物：按章抽取骨架 → 物化(uuid 图谱) → 叶子文档 → 全书合并 → 导出。

- 节点总数：**{len(out_nodes)}**（course {kinds.get('course',0)} / index {kinds.get('index',0)} / topic {kinds.get('topic',0)}）
- 边总数：**{len(out_topic) + len(out_index)}**（edges {len(out_topic)} + index_edges {len(out_index)}）
- 关系类型：`prerequisite_of` / `parallel_to`（edges.json）、`contains`（index_edges.json）

## 2. 目录结构
```
{out.name}/
├── nodes.json          # 全部节点（course/index/topic）
├── edges.json          # topic↔topic 知识关系边
├── aux_edges.json      # 空（整章化无 question/application）
├── index_edges.json    # index 树 contains（course→章→…→topic）
├── texts/              # 每个 topic 一份 md（文件名=label，label 冲突时带短 id 后缀）
├── images/             # 插图（{n_images} 个）
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
nodes = json.load(open('{out.name}/nodes.json', encoding='utf-8'))['nodes']
edges = json.load(open('{out.name}/edges.json', encoding='utf-8'))['edges']
index = json.load(open('{out.name}/index_edges.json', encoding='utf-8'))['edges']
topics = [n for n in nodes if n['node_kind'] == 'topic']
```
"""
    (out / 'README.md').write_text(readme, encoding='utf-8')

    # ---- 校验 ----
    all_edges = out_topic + out_index
    bad = [e for e in all_edges
           if e['source_node_id'] not in id_set or e['target_node_id'] not in id_set]
    dup = len(all_edges) - len({(e['source_node_id'], e['target_node_id'],
                                 e['relation_type']) for e in all_edges})
    print(f"✅ deliver 交付已写: {out}")
    print(f"   节点 {len(out_nodes)}（index {kinds.get('index',0)} / topic "
          f"{kinds.get('topic',0)}）· edges {len(out_topic)} · index_edges "
          f"{len(out_index)} · texts {n_texts} · images {n_images}")
    print(f"   校验: 端点缺失 {len(bad)} · 重复边 {dup}")
    if bad:
        print("   bad:", [(e['source_title'], e['target_title'])
                          for e in bad[:5]])


if __name__ == '__main__':
    main()
