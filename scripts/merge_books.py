#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全书合并（整章化流水线 Stage 5.5，纯脚本，无 LLM）：
  若干章 chapter_graph.json（物化产物，已含 uuid/树边/关系边）→ 单张 book_graph.json

- 章根（level:0 的 index）作为"章"挂到全书根之下（root → 章根 contains），
  不新增中间"书"层；节点带 book 字段与 chapter_id
- 学习顺序 = --graphs 展开并自然排序后的顺序（chapter_id_order）
- 可选 --cross 注入跨章边产物（cross_chapter_links.py 输出，uuid 端点已校验好）
- 校验：跨章 rel_edges 端点存在；跨章边确实跨 chapter_id（防止误并入）

用法：
  python scripts/merge_books.py \
      --graphs "intermediate/cn_chapters/ch*_graph.json" \
      [--cross intermediate/cn_chapters/cross_links.json] \
      --out intermediate/cn_chapters/book_graph.json
"""
import sys, io, json, uuid, argparse, glob as globmod
from pathlib import Path
from rel_normalize import normalize_rel_edges, fmt_dropped

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--graphs', required=True, help='章图 glob，自然排序为学习顺序')
    ap.add_argument('--out', required=True)
    ap.add_argument('--cross', default='', help='cross_chapter_links.py 产物（可选）')
    args = ap.parse_args()

    paths = sorted(Path(p) for p in globmod.glob(args.graphs))
    if not paths:
        raise SystemExit(f'glob 无匹配: {args.graphs}')
    print(f"📚 合并 {len(paths)} 个章图:")

    nodes, tree_edges, rel_edges = [], [], []
    books = set()
    chapter_order = []
    seen_ids = set()

    for p in paths:
        g = json.loads(p.read_text(encoding='utf-8'))
        book = g.get('book', '')
        books.add(book)
        root = next((n for n in g['nodes'] if n['level'] == 0), None)
        if not root:
            print(f"  ❌ {p}: 缺 level:0 章根，跳过")
            continue
        dup = [n for n in g['nodes'] if n['id'] in seen_ids]
        if dup:
            raise SystemExit(f"uuid 冲突（异常，不应出现）: {p}")
        for n in g['nodes']:
            n = dict(n)
            n['book'] = book
            nodes.append(n)
            seen_ids.add(n['id'])
        for e in g['tree_edges']:
            tree_edges.append(e)
        for e in g['rel_edges']:
            e = dict(e)
            e['book'] = book  # 来源章（章内边）
            e['cross'] = False
            rel_edges.append(e)
        chapter_order.append(root['id'])
        print(f"  ✓ {book} 《{root['title']}》  nodes={len(g['nodes'])} "
              f"· rel={len(g['rel_edges'])}  <- {p.name}")

    # 全书根
    root_id = str(uuid.uuid4())
    nodes.append({'id': root_id, 'kind': 'index', 'title': '知识图谱',
                  'summary': '', 'level': -1, 'path': '',
                  'parent_id': None, 'chapter_id': '', 'book': ''})
    for cid in chapter_order:
        tree_edges.append({'from': root_id, 'to': cid, 'rel': 'contains'})

    # 跨章边合并（端点已 uuid，校验跨章且端点存在）
    n_cross = 0
    if args.cross:
        cross = json.loads(Path(args.cross).read_text(encoding='utf-8'))
        ch_of = {n['id']: n['chapter_id'] for n in nodes}
        for e in cross.get('rel_edges', []):
            f, t = e['from'], e['to']
            if f not in ch_of or t not in ch_of:
                print(f"  ❌ 跨章边端点缺失，跳过: {f} → {t}")
                continue
            if ch_of[f] == ch_of[t]:
                print(f"  ⚠️ 同章边混入跨章文件，跳过: {f} → {t}")
                continue
            rel_edges.append({'from': f, 'to': t, 'rel': e['rel'],
                              'note': e.get('note', ''), 'cross': True,
                              'book': ''})
            n_cross += 1

    # 同对多关系归一（章内边 + 跨章边统一收口，幂等）
    rel_edges, dropped = normalize_rel_edges(rel_edges)
    if dropped:
        print(f"↔️ 同对多关系消解 {len(dropped)} 条：\n{fmt_dropped(dropped)}")

    # 校验
    ids = {n['id'] for n in nodes}
    bad = [e for e in rel_edges
           if e['from'] not in ids or e['to'] not in ids]
    tree_bad = [e for e in tree_edges
                if e['from'] not in ids or e['to'] not in ids]
    if bad:
        print(f"❌ rel_edges 端点缺失 {len(bad)} 条，见 report")
    if tree_bad:
        print(f"❌ tree_edges 端点缺失 {len(tree_bad)} 条，见 report")

    kinds = {}
    for n in nodes:
        kinds[n['kind']] = kinds.get(n['kind'], 0) + 1
    graph = {
        'schema': 'book_graph/1.0',
        'domain': ' / '.join(sorted(books)),
        'root_id': root_id,
        'chapter_id_order': chapter_order,
        'nodes': nodes,
        'tree_edges': tree_edges,
        'rel_edges': rel_edges,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f"\n✅ book_graph 已写: {out}")
    print(f"   节点 {len(nodes)}（index {kinds.get('index',0)} / topic "
          f"{kinds.get('topic',0)}）· 树边 {len(tree_edges)} · 关系边 "
          f"{len(rel_edges)}（其中跨章 {n_cross}）· 章序 {len(chapter_order)} 章")
    if bad or tree_bad:
        rep = out.with_name(out.stem + '_report.json')
        rep.write_text(json.dumps({
            'bad_rel_edges': bad[:20], 'n_bad_rel': len(bad),
            'bad_tree_edges': tree_bad[:20], 'n_bad_tree': len(tree_bad),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"⚠️ 校验有问题，报告: {rep}")


if __name__ == '__main__':
    main()
