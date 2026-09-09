#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整章骨架提取（整章化流水线 Stage 1，LLM，一次一章）：
  chapter.md → 知识单元树 chapter_skeleton.json
  units 树（非叶子带三要素导读 summary——见 prompts/whole_chapter/skeleton_system.txt）

用法：
  python scripts/extract_chapter.py --cfg <json>
cfg：chapter_md / chapter / prompt(默认 prompts/whole_chapter/skeleton_system.txt) /
     out / tracker(可选) / model(可选)
CLI：--dry-run 只打印 prompt
"""
import sys, io, os, re, json, time, argparse
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

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

MODEL = os.environ.get("LLM_MODEL", "qwen3.7-plus")
# 说明：S1 整章骨架为单次大输出，不设 max_tokens 上限（放开给模型默认最大值），
# 避免骨架树/导读被截断导致 JSON 解析失败。
DEFAULT_PROMPT = ROOT / 'prompts' / 'whole_chapter' / 'skeleton_system.txt'


def fix_json_escapes(s: str) -> str:
    return re.sub(r'\\(?![\\"/bfnrtu])', r'\\\\', s)


def call_llm(system: str, user: str, tracker: str, desc: str):
    client = OpenAI(api_key=os.environ.get('DASHSCOPE_API_KEY', ''),
                    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    for attempt in range(1, 4):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{'role': 'system', 'content': system},
                          {'role': 'user', 'content': user}],
                temperature=0.1)
            content = resp.choices[0].message.content.strip()
            content = re.sub(r'^```(?:json)?\s*\n', '', content)
            content = re.sub(r'\n```\s*$', '', content)
            get_tracker(tracker).add_from_usage(model=MODEL,
                                                usage=extract_usage(resp),
                                                description=desc)
            return content
        except Exception as e:
            print(f'⚠️ 第{attempt}次失败: {type(e).__name__}: {e}')
            if attempt < 3:
                time.sleep(10 * attempt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cfg', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    cfg = json.loads(Path(args.cfg).read_text(encoding='utf-8'))
    if cfg.get('model'):
        global MODEL
        MODEL = cfg['model']
    chapter_md = Path(cfg['chapter_md'])
    if not chapter_md.is_absolute():
        chapter_md = ROOT / chapter_md
    seg = chapter_md.read_text(encoding='utf-8')
    chapter_title = cfg.get('chapter', chapter_md.stem)
    prompt = Path(cfg.get('prompt', DEFAULT_PROMPT))
    if not prompt.is_absolute():
        prompt = ROOT / prompt
    system = prompt.read_text(encoding='utf-8')
    user = f"【本章标题】{chapter_title}\n\n【整章原文】\n{seg}"
    print(f"🧠 extract《{chapter_title}》 原文 {len(seg)} 字符 → {cfg.get('out')}")
    if args.dry_run or cfg.get('dry_run'):
        (Path(cfg['out']).with_name('extract_prompt_preview.txt')
         ).write_text('=== SYSTEM ===\n' + system + '\n\n=== USER ===\n' + user,
                      encoding='utf-8')
        print(f"💾 dry-run：prompt 预览已写")
        return

    content = call_llm(system, user, cfg.get('tracker', 'extract_chapter'),
                       f'extract:{chapter_title[:20]}')
    if not content:
        raise SystemExit('❌ extract 失败（重试 3 次仍不可用）')
    out = Path(cfg['out'])
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.with_name(out.stem + '_raw.txt')).write_text(content, encoding='utf-8')

    data = None
    for raw in (content, fix_json_escapes(content)):
        try:
            data = json.loads(raw)
            break
        except Exception as e:
            print('JSON 解析失败:', e)
    if not data:
        raise SystemExit('❌ extract 输出无法解析，原文已存 raw')
    if isinstance(data, dict) and 'units' in data:
        units = data['units']
    elif isinstance(data, list):
        units = data
    else:
        units = None
    if not isinstance(units, list):
        raise SystemExit('❌ extract 输出结构异常（缺 units 列表）')
    out.write_text(json.dumps({'units': units}, ensure_ascii=False, indent=2),
                   encoding='utf-8')

    n_node, n_leaf = 0, 0
    empty = []
    def walk(us, depth=0):
        nonlocal n_node, n_leaf
        for u in us:
            kids = u.get('children') or []
            if not (u.get('title') or '').strip():
                empty.append('缺 title 节点')
            if not (u.get('summary') or '').strip():
                empty.append(u.get('title') or '?')
            if kids:
                walk(kids, depth + 1)
            else:
                n_leaf += 1
            n_node += 1
    walk(units)
    print(f"✅ 骨架已写: {out}   节点 {n_node} · 叶子 {n_leaf}")
    if empty:
        print(f"⚠️ summary 为空节点 {len(empty)} 个: {empty[:8]}")
    get_tracker(cfg.get('tracker', 'extract_chapter')).print_summary()


if __name__ == '__main__':
    main()
