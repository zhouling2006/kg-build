# -*- coding: utf-8 -*-
"""关系边归一（纯脚本，无 LLM，幂等）。

不变量：任意一对知识点之间只保留一种关系。
判重键 = 无序点对 frozenset({from, to})：A→B、B→A、A↔B 都视为「同一对知识点」，
因此 prerequisite_of A→B 与 parallel_to A↔B 的冲突能被正确拦截（此前按
rel 区分有序/无序键会导致该冲突漏检）。

- 同 rel 同方向 → 去重，note 合并
- 同 rel 反方向：
    · 无向 rel（parallel_to/related）→ A↔B 与 B↔A 是同一条，直接合并 note
    · 有向 rel（prerequisite_of 等）→ 反向矛盾（疑似环），保留先出现者，
      在 note 里注明另一反向，并记入 dropped
- 跨 rel 同对（如既 prerequisite_of 又 parallel_to）→ 冲突，保留优先级更高者
  （先修 > 派生/泛化 > 并列对照），被弃用 rel 的 note 并入保留边（标注"另一视角"）
- 自环 (from == to) 一律丢弃

dropped 元素统一为 5 元组：
    ('conflict', winner_rel, loser_rel, from, to, loser_note)
    ('reverse',  rel, 'reverse-dir', from, to, '')     # 有向 rel 出现反向重复

用法：
    from rel_normalize import normalize_rel_edges
    clean, dropped = normalize_rel_edges(edges)
    # edges: [{'from':…, 'to':…, 'rel':…, 'note':…}, …]（note 可选，其它字段保留）
"""

REL_PRIORITY = {
    'prerequisite_of': 0,
    'derived_from': 1,
    'generalization_of': 1,
    'applies_to': 2,
    'parallel_to': 3,
    'related': 3,
}
UNDIRECTED = {'parallel_to', 'related'}


def _join_notes(base: str, extra: str) -> str:
    """把 extra 并进 base；extra 已含于 base 时不重复。"""
    base, extra = (base or '').strip(), (extra or '').strip()
    if not extra or (base and extra in base):
        return base or ''
    return base + ('；' if base else '') + extra


def normalize_rel_edges(edges):
    """返回 (clean, dropped)。clean 保持各对首次出现的稳定顺序。"""
    keep, order = {}, {}
    dropped = []
    for e in edges:
        if not e or not e.get('from') or not e.get('to') \
                or e['from'] == e['to']:
            continue
        pair = frozenset((e['from'], e['to']))
        prev = keep.get(pair)
        if prev is None:
            keep[pair] = dict(e)
            order.setdefault(pair, len(order))
            continue
        prev_rel, cur_rel = prev.get('rel'), e.get('rel')
        same_dir = (prev['from'] == e['from'] and prev['to'] == e['to'])
        if prev_rel == cur_rel:
            if same_dir or cur_rel in UNDIRECTED:
                prev['note'] = _join_notes(prev.get('note'), e.get('note'))
            else:
                # 有向 rel 反向重复（A→B 与 B→A）→ 疑似环，保留先出现者并提示
                dropped.append(('reverse', cur_rel, 'reverse-dir',
                                e['from'], e['to'], ''))
                prev['note'] = _join_notes(
                    prev.get('note'),
                    f'（注意：另出现反向 {cur_rel}：{e["to"]} → {e["from"]}，'
                    f'方向矛盾，已保留先出现方向）')
            continue
        # 跨 rel 冲突：保留优先级更高者
        winner, loser = (prev, e) if (
            REL_PRIORITY.get(prev_rel, 9) <= REL_PRIORITY.get(cur_rel, 9)
        ) else (e, prev)
        dropped.append(('conflict', winner.get('rel'), loser.get('rel'),
                        winner['from'], winner['to'],
                        loser.get('note', '')))
        winner = dict(winner)
        base = (winner.get('note') or '').strip()
        loser_note = (loser.get('note') or '').strip()
        if loser_note and loser_note not in base:
            winner['note'] = base + ('；' if base else '') \
                + '（另一视角：' + loser_note + '）'
        else:
            winner['note'] = base
        keep[pair] = winner
    clean = sorted(keep.values(), key=lambda e: order[frozenset((e['from'], e['to']))])
    return clean, dropped


def fmt_dropped(dropped, limit=10):
    """把 dropped 明细渲染成可读的多行文本。"""
    lines = []
    for d in dropped[:limit]:
        if d[0] == 'reverse':
            _, rel, _, frm, to, _ = d
            lines.append(f'   [!] 反向矛盾（{rel} {frm} → {to}），保留先出现方向')
        else:
            _, w, l, frm, to, note = d
            tip = f'（弃用视角：{note[:60]}）' if note else ''
            lines.append(f'   {w} 保留 vs {l} 弃用: {frm} → {to} {tip}')
    if len(dropped) > limit:
        lines.append(f'   … 其余 {len(dropped) - limit} 条省略')
    return '\n'.join(lines)
