#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
物化（整章化流水线 Stage 2.5，纯脚本，无 LLM）：
  骨架树(chapter_skeleton.json 的 units) + 章内关系(relations.json)
  → 每章唯一权威产物 chapter_graph.json

要点（见 整章化流水线改造设计.md §4.4）：
- 章根 = kind:index, level:0，带独立 chapter_id；最终并入 index（无独立 chapter kind）
- 节点/边全部 uuid4 由本脚本分配（禁止 LLM 生成）
- 关系边解析：prerequisites→prerequisite_of(from=先修)，related→parallel_to
- 校验：title 全局唯一（index 与 topic 不得同名）；边端点全在 nodes 内；
  叶子数 = relations.leaves 数；relations 引用全部可解析（否则报错不静默）

用法：
  python scripts/materialize_chapter.py \
      --book 计算机网络 --chapter "第5章 运输层" \
      --skeleton experiments/.../chapter_skeleton.json \
      --relations experiments/.../relations.json \
      --chapter-md experiments/.../chapter.md \
      --out intermediate/cn_chapters/ch5_graph.json
"""
import sys, io, json, uuid, argparse, re
from pathlib import Path
from rel_normalize import normalize_rel_edges, fmt_dropped

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def load_json(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def walk_units(units, parent_id, parent_path, chapter_id, nodes, tree_edges,
               level=1):
    """先序：非叶子→index，叶子→topic。返回 (本层节点列表)。"""
    for u in units:
        title = u.get('title') or ''
        summary = u.get('summary') or ''
        kids = u.get('children') or []
        nid = str(uuid.uuid4())
        path = f"{parent_path}/{title}" if parent_path else title
        is_leaf = not kids
        nodes.append({
            'id': nid, 'kind': 'topic' if is_leaf else 'index',
            'title': title, 'summary': summary, 'level': level,
            'path': path, 'parent_id': parent_id, 'chapter_id': chapter_id,
        })
        if parent_id is not None:
            tree_edges.append({'from': parent_id, 'to': nid, 'rel': 'contains'})
        if kids:
            walk_units(kids, nid, path, chapter_id, nodes, tree_edges, level + 1)


def parse_title_refs(rel_edges_spec, title2id):
    """把 relations 的 title 引用转 uuid 边。"""
    out, missing = [], []
    for e in rel_edges_spec:
        # prerequisites: {from, to, reason}; related: {a, b, reason}
        if 'from' in e and 'to' in e:
            f, t, rel = e['from'], e['to'], 'prerequisite_of'
        else:
            f, t, rel = e['a'], e['b'], 'parallel_to'
        fid, tid = title2id.get(f), title2id.get(t)
        if fid is None or tid is None:
            missing.append((f, t))
            continue
        out.append({'from': fid, 'to': tid, 'rel': rel, 'note': e.get('reason', '')})
    return out, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skeleton', required=True)
    ap.add_argument('--relations', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--book', default='')
    ap.add_argument('--chapter', default='')
    ap.add_argument('--chapter-md', default='', help='章节原文路径（source 溯源用）')
    ap.add_argument('--chapter-index', default='', help='cut_chapters 产出的 chapter_index.json')
    ap.add_argument('--chapter-no', type=int, default=0)
    args = ap.parse_args()

    skel = load_json(args.skeleton)
    rel = load_json(args.relations)
    units = skel.get('units', skel) if isinstance(skel, dict) else skel

    # 书/章标题：优先 CLI；缺省从 chapter_index.json 取（脚本内 UTF-8，避免命令行中文乱码）
    chapter_title = args.chapter
    source_md = args.chapter_md
    book = args.book
    if args.chapter_index:
        idx = load_json(args.chapter_index)
        if not book:
            book = idx.get('book', '')
        if args.chapter_no:
            for c in idx.get('chapters', []):
                if c.get('no') == args.chapter_no:
                    chapter_title = chapter_title or f"第{c['no']}章 {c['title']}"
                    if not source_md and c.get('md_file'):
                        p = Path(args.chapter_index).parent / c['md_file']
                        source_md = str(p)
                    break

    chapter_id = str(uuid.uuid4())
    nodes, tree_edges = [], []
    # 章根
    root_id = str(uuid.uuid4())
    nodes.append({'id': root_id, 'kind': 'index', 'title': chapter_title,
                  'summary': '', 'level': 0, 'path': chapter_title,
                  'parent_id': None, 'chapter_id': chapter_id})
    walk_units(units, root_id, chapter_title, chapter_id, nodes, tree_edges, level=1)

    # ── 压层（无损去重）：index 若只有 1 个同名 topic 子节点，说明该分组冗余
    #    （骨架偶发的"分组与叶子同名"违反唯一性），让叶子上移顶替分组；
    #    分组 summary 非空时并入叶子，避免导读信息丢失。
    child_ids: dict[str, list[str]] = {}
    for e in tree_edges:
        child_ids.setdefault(e['from'], []).append(e['to'])
    collapse = []
    for idx_n in nodes:
        if idx_n['kind'] != 'index':
            continue
        kids = child_ids.get(idx_n['id'], [])
        if len(kids) == 1:
            kid = next((n for n in nodes if n['id'] == kids[0]), None)
            if kid and kid['kind'] == 'topic' and kid['title'] == idx_n['title']:
                collapse.append(idx_n['id'])
    if collapse:
        keep = set(collapse)
        new_tree = []
        for e in tree_edges:
            if e['to'] in keep:
                idx_n = next(n for n in nodes if n['id'] == e['to'])
                kid = next(n for n in nodes if n['id'] == child_ids[e['to']][0])
                kid['level'] = idx_n['level']
                kid['parent_id'] = idx_n['parent_id']
                kid['path'] = idx_n['path']            # 同名同层级，path 前缀一致
                if idx_n.get('summary') and not kid.get('summary'):
                    kid['summary'] = idx_n['summary']  # 分组导读兜底并入叶子
                if e['from'] is not None:
                    new_tree.append({'from': e['from'], 'to': kid['id'], 'rel': 'contains'})
            elif e['from'] in keep:
                continue                               # 分组→叶子的旧边删除
            else:
                new_tree.append(e)
        nodes = [n for n in nodes if n['id'] not in keep]
        tree_edges = new_tree
        print(f"↕️ 压层合并 {len(collapse)} 个冗余分组（唯一同名叶子顶替）")

    # relations 叶子数核对
    leaves_rel = rel.get('leaves') or []
    leaves_in_tree = [n for n in nodes if n['kind'] == 'topic']
    if len(leaves_rel) != len(leaves_in_tree):
        print(f"⚠️ 叶子数不一致: relations={len(leaves_rel)}  树={len(leaves_in_tree)}")

    # relations.leaves 若带了更完整的 summary/path，且 title 命中则回填（树为结构权威，叶子信息以 relations 为准）
    rel_by_title = {}
    for lf in rel.get('leaves', []):
        rel_by_title.setdefault(lf.get('title', ''), lf)
    for n in leaves_in_tree:
        rl = rel_by_title.get(n['title'])
        if rl:
            if not n.get('summary') and rl.get('summary'):
                n['summary'] = rl['summary']

    # title 唯一性校验（章内含章根）
    dup = {}
    for n in nodes:
        dup.setdefault(n['title'], []).append(n['id'])
    dups = {t: ids for t, ids in dup.items() if len(ids) > 1 and t}
    if dups:
        print(f"❌ title 重复 {len(dups)} 组（index/topic 同名或树内重复）:")
        for t, ids in list(dups.items())[:10]:
            print(f"   «{t}»  → {len(ids)} 个节点")
        # 不中断：进入报告，由上层处理；仍写出图谱

    title2id = {}
    for n in nodes:
        title2id.setdefault(n['title'], n['id'])

    # 关系边解析（title 引用 → uuid）
    rel_edges = []
    spec = []
    for e in rel.get('prerequisites', []):
        spec.append({'from': e['from'], 'to': e['to'], 'reason': e.get('reason', '')})
    for e in rel.get('related', []):
        spec.append({'a': e['a'], 'b': e['b'], 'reason': e.get('reason', '')})
    rel_edges, missing = parse_title_refs(spec, title2id)
    # 同对多关系归一（同 rel 去重 / 跨 rel 保留先修并合并 note），幂等
    rel_edges, dropped = normalize_rel_edges(rel_edges)
    if dropped:
        print(f"↔️ 章内同对多关系消解 {len(dropped)} 条：\n{fmt_dropped(dropped)}")
    if missing:
        print(f"❌ 关系边 title 引用无法解析 {len(missing)} 条:")
        for f, t in missing[:10]:
            print(f"   «{f}» → «{t}»")

    graph = {
        'schema': 'chapter_graph/1.0',
        'book': book,
        'chapter': chapter_title,
        'chapter_id': chapter_id,
        'source': source_md,
        'nodes': nodes,
        'tree_edges': tree_edges,
        'rel_edges': rel_edges,
        'learning_path': rel.get('learning_path', []),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding='utf-8')

    kinds = {}
    for n in nodes:
        kinds[n['kind']] = kinds.get(n['kind'], 0) + 1
    print(f"✅ chapter_graph 已写: {out}")
    print(f"   节点 {len(nodes)}（{kinds.get('index',0)} index / {kinds.get('topic',0)} topic）"
          f"· 树边 {len(tree_edges)} · 关系边 {len(rel_edges)}")
    print(f"   校验: title 重复 {len(dups)} · 引用缺失 {len(missing)} · "
          f"relations 叶子 {len(leaves_rel)} vs 树叶子 {len(leaves_in_tree)}")

    report = {
        'book': book, 'chapter': chapter_title, 'out': str(out),
        'n_index': kinds.get('index', 0), 'n_topic': kinds.get('topic', 0),
        'n_tree_edges': len(tree_edges), 'n_rel_edges': len(rel_edges),
        'dup_titles': [{'title': t, 'n': len(v)} for t, v in dups.items()],
        'missing_refs': [{'from': f, 'to': t} for f, t in missing],
        'leaf_mismatch': len(leaves_rel) != len(leaves_in_tree),
    }
    rep = out.with_name(out.stem + '_report.json')
    rep.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if dups or missing:
        print("⚠️ 存在校验问题，请查看 report")


if __name__ == '__main__':
    main()
