#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨章关系构建（整章化流水线 Stage 5，LLM）：
  输入若干章 chapter_graph.json（glob 顺序 = 学习顺序）
  输出跨章关系 cross_links.json —— 端点已解析为 uuid（LLM 引用句柄"章号.叶子序"，
  句柄到 uuid 的映射全部由本脚本完成并校验，越界即报错，防止 LLM 编造名称）

- 只列跨章边（同章关系已由 relate 阶段产出）
- 判定依据用每叶子的 title + summary（跨章判断无需整章正文）
- 不同书章号冲突时句柄自动加"分册.章号.序"前缀（例如 2.1.05）
- 叶子总数超 MAX_LEAVES 时截断末尾章并明确提示

用法：
  python scripts/cross_chapter_links.py \
      --graphs "intermediate/cn_chapters/ch*_graph.json" \
      --out intermediate/cn_chapters/cross_links.json [--dry-run]
"""
import sys, io, os, re, json, time, argparse, glob as globmod
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
from openai import OpenAI
from billing import get_tracker, extract_usage
from rel_normalize import normalize_rel_edges, fmt_dropped

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

MODEL = os.environ.get("LLM_MODEL", "qwen3.7-plus")
MAX_OUTPUT_TOKENS = 6000
MAX_LEAVES = 600
CHAPTER_NO_RE = re.compile(r'第\s*([0-9]+|[一二三四五六七八九十百]+)\s*章')


def cn_to_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
          '六': 6, '七': 7, '八': 8, '九': 9}
    total = section = 0
    for ch in s:
        if ch in CN:
            section += CN[ch]
        elif ch == '十':
            section = section * 10 if section else 10
        elif ch == '百':
            total += section * 100
            section = 0
    return total + section


SYSTEM = """你是跨章学习路径设计专家。给你某门课程若干章的知识叶子清单，
每片叶子带唯一句柄（格式：章号.叶子序，如 5.07）与一句话摘要。
你的任务：找出【跨章】的依赖与对照关系（同章内的关系不需要，已有专门环节处理）：
一、prerequisites：学习 to 之前，最好/必须先掌握 from（两片叶子在不同章）。
   只列直接先修，不要传递展开；同一个 to 最多 3 条。
二、related：复习对照关系——易混概念、相似机制、前置思想在某协议/机制中的体现等（两片叶子在不同章）。
   宁精勿滥，只列真正值得对照的。
同一对叶子只允许一种关系：若既像先修又像对照，放进 prerequisites（先修优先），
把对照视角并入该条 reason；同一对不得在 prerequisites 与 related 中重复出现。
只列信息上确凿的关系，不确定就不列；句柄必须是清单中存在的，不得编造。
输出：仅输出 JSON，不要 markdown 代码块，不要多余文字：
{"prerequisites":[{"from":"5.07","to":"6.03","reason":"..."}],
 "related":[{"a":"5.07","b":"9.11","reason":"..."}]}"""


def call_llm(user: str, tracker: str, desc: str):
    client = OpenAI(api_key=os.environ.get('DASHSCOPE_API_KEY', ''),
                    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    for attempt in range(1, 4):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{'role': 'system', 'content': SYSTEM},
                          {'role': 'user', 'content': user}],
                temperature=0.1, max_tokens=MAX_OUTPUT_TOKENS)
            content = resp.choices[0].message.content.strip()
            content = re.sub(r'^```(?:json)?\s*\n', '', content)
            content = re.sub(r'\n```\s*$', '', content)
            get_tracker(tracker).add_from_usage(model=MODEL,
                                                usage=extract_usage(resp),
                                                description=desc)
            return json.loads(content)
        except json.JSONDecodeError:
            print(f'⚠️ 第{attempt}次 JSON 解析失败，重试')
        except Exception as e:
            print(f'⚠️ 第{attempt}次失败: {type(e).__name__}: {e}')
        time.sleep(8 * attempt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--graphs', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    paths = sorted(Path(p) for p in globmod.glob(args.graphs))
    if not paths:
        raise SystemExit(f'glob 无匹配: {args.graphs}')

    # 读各章：no / title / topics
    chapters = []
    for gi, p in enumerate(paths, 1):
        g = json.loads(p.read_text(encoding='utf-8'))
        m = CHAPTER_NO_RE.search(g.get('chapter', ''))
        no = cn_to_int(m.group(1)) if m else gi * 100 + 1
        chapters.append({
            'file': str(p), 'title': g.get('chapter', ''), 'no': no,
            'topics': [n for n in g['nodes'] if n['kind'] == 'topic']})

    total_leaves = sum(len(c['topics']) for c in chapters)
    if total_leaves > MAX_LEAVES:
        dropped = []
        while total_leaves > MAX_LEAVES and chapters:
            c = chapters.pop()
            total_leaves -= len(c['topics'])
            dropped.append(c['title'])
        print(f"⚠️ 叶子总数超限({MAX_LEAVES})，已截断末尾章: {'; '.join(dropped)}")

    # 章号冲突检测 → 句柄加"分册序"前缀
    no_groups = defaultdict(list)
    for c in chapters:
        no_groups[c['no']].append(c)
    need_book_pref = any(len(v) > 1 for v in no_groups.values())

    handle2node = {}
    for bi, c in enumerate(chapters, 1):
        for si, n in enumerate(c['topics'], 1):
            h = f"{c['no']}.{si:02d}"
            if need_book_pref:
                h = f"{bi}.{h}"
            handle2node[h] = n
            n['__handle__'] = h

    print(f"📖 {len(chapters)} 章 · {total_leaves} 叶子 · "
          f"{'句柄已加册前缀' if need_book_pref else '句柄无冲突'} · 模型 {MODEL}")

    lines = []
    for c in chapters:
        lines.append(f"### {c['title']}")
        for n in c['topics']:
            summary = (n.get('summary') or '').replace('\n', ' ')[:140]
            lines.append(f"- {n['__handle__']}｜{n['title']}：{summary}")
    user = ("下面是本课程全部知识叶子（句柄｜叶子名：摘要）。"
            "请输出跨章 prerequisites 与 related（见系统要求）。\n\n"
            + '\n'.join(lines))

    if args.dry_run:
        prev = Path(args.out).parent / 'cross_prompt_preview.txt'
        prev.write_text('=== SYSTEM ===\n' + SYSTEM + '\n\n=== USER ===\n' + user,
                        encoding='utf-8')
        print(f"💾 dry-run：prompt 预览 → {prev}")
        return

    data = call_llm(user, 'cross_chapter', f'cross: {len(chapters)} 章')
    if data is None:
        raise SystemExit('❌ 跨章关系生成失败（重试 3 次仍不可用）')

    edges, errors = [], []
    for e in data.get('prerequisites', []):
        f = handle2node.get(e.get('from', ''))
        t = handle2node.get(e.get('to', ''))
        if f is None or t is None:
            errors.append(f"prereq 句柄缺失: {e.get('from')} → {e.get('to')}")
            continue
        edges.append({'from': f['id'], 'to': t['id'],
                      'rel': 'prerequisite_of', 'note': e.get('reason', '')})
    for e in data.get('related', []):
        f = handle2node.get(e.get('a', ''))
        t = handle2node.get(e.get('b', ''))
        if f is None or t is None:
            errors.append(f"related 句柄缺失: {e.get('a')} ~ {e.get('b')}")
            continue
        edges.append({'from': f['id'], 'to': t['id'],
                      'rel': 'parallel_to', 'note': e.get('reason', '')})

    # 同对多关系归一：同 rel 去重；同对跨 rel（prereq vs parallel）保留先修、
    # 弃用视角并入 note；有向 rel 反向重复（疑似环）保留先出现方向并提示。
    clean, dropped = normalize_rel_edges(edges)
    if dropped:
        print(f"↔️ 跨章同对多关系消解 {len(dropped)} 条：\n{fmt_dropped(dropped)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {'schema': 'cross_links/1.0', 'n_chapters': len(chapters),
         'rel_edges': clean, 'errors': errors},
        ensure_ascii=False, indent=2), encoding='utf-8')
    kinds = {}
    for e in clean:
        kinds[e['rel']] = kinds.get(e['rel'], 0) + 1
    print(f"✅ 跨章边 {len(clean)} 条（{kinds}）→ {out}"
          + (f"\n⚠️ 无法解析 {len(errors)} 条: {errors[:5]}" if errors else ''))


if __name__ == '__main__':
    main()
