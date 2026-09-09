#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章内关系构建（整章化流水线 Stage 2，LLM，一次一章）：
  chapter_skeleton.json → relations.json（leaves/prerequisites/related/learning_path）
  leaves 必须与骨架树叶子一致（见 prompts/whole_chapter/relate_system.txt）

用法：
  python scripts/relate_chapter.py --cfg <json>
cfg：skeleton / chapter / prompt(默认 relate_system.txt) /
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
MAX_OUTPUT_TOKENS = 20000
DEFAULT_PROMPT = ROOT / 'prompts' / 'whole_chapter' / 'relate_system.txt'


def fix_json_escapes(s: str) -> str:
    return re.sub(r'\\(?![\\"/bfnrtu])', r'\\\\', s)


def flatten(units, prefix=()):
    leaves = []
    for u in units:
        kids = u.get('children') or []
        path = prefix + (u['title'],)
        if kids:
            leaves += flatten(kids, path)
        else:
            leaves.append({'path': ' / '.join(path), 'title': u['title'],
                           'summary': u.get('summary') or ''})
    return leaves


def call_llm(system: str, user: str, tracker: str, desc: str,
             max_tokens: int = MAX_OUTPUT_TOKENS):
    client = OpenAI(api_key=os.environ.get('DASHSCOPE_API_KEY', ''),
                    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    for attempt in range(1, 4):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{'role': 'system', 'content': system},
                          {'role': 'user', 'content': user}],
                temperature=0.1, max_tokens=max_tokens)
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
    skel = Path(cfg['skeleton'])
    if not skel.is_absolute():
        skel = ROOT / skel
    data = json.loads(skel.read_text(encoding='utf-8'))
    units = data.get('units', data) if isinstance(data, dict) else data
    leaves = flatten(units)
    chapter_title = cfg.get('chapter', skel.stem)
    prompt = Path(cfg.get('prompt', DEFAULT_PROMPT))
    if not prompt.is_absolute():
        prompt = ROOT / prompt
    system = prompt.read_text(encoding='utf-8')
    user = (f"【章节标题】{chapter_title}\n\n【章级知识单元树】\n"
            + json.dumps(units, ensure_ascii=False, indent=1))
    print(f"🧠 relate《{chapter_title}》 {len(leaves)} 叶子 → {cfg.get('out')}")
    if args.dry_run or cfg.get('dry_run'):
        (Path(cfg['out']).with_name('relate_prompt_preview.txt')
         ).write_text('=== SYSTEM ===\n' + system + '\n\n=== USER ===\n' + user,
                      encoding='utf-8')
        print(f"💾 dry-run：prompt 预览已写")
        return

    content = call_llm(system, user, cfg.get('tracker', 'relate_chapter'),
                       f'relate:{chapter_title[:20]}/{len(leaves)}leaves')
    if not content:
        raise SystemExit('❌ relate 失败（重试 3 次仍不可用）')
    out = Path(cfg['out'])
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.with_name(out.stem + '_raw.txt')).write_text(content, encoding='utf-8')

    d = None
    for raw in (content, fix_json_escapes(content)):
        try:
            d = json.loads(raw)
            break
        except Exception as e:
            print('JSON 解析失败:', e)
    if not d:
        raise SystemExit('❌ relate 输出无法解析，原文已存 raw')
    leaves_l = d.get('leaves') or leaves
    valid = {l['title'] for l in leaves_l}
    pre = d.get('prerequisites', [])
    rel = d.get('related', [])
    lp = d.get('learning_path', [])
    bad_pre = [x for x in pre if x.get('from') not in valid or x.get('to') not in valid]
    bad_rel = [x for x in rel if x.get('a') not in valid or x.get('b') not in valid]
    out.write_text(json.dumps(
        {'chapter': chapter_title, 'leaves': leaves_l, 'prerequisites': pre,
         'related': rel, 'learning_path': lp}, ensure_ascii=False, indent=2),
        encoding='utf-8')

    # leaves 一致性核对（骨架树叶子）
    skel_titles = {l['title'] for l in leaves}
    drift = [l['title'] for l in leaves_l if l['title'] not in skel_titles]
    print(f"✅ relations 已写: {out}")
    print(f"   leaves {len(leaves_l)} · prereq {len(pre)} · related {len(rel)} · "
          f"learning_path {len(lp)}")
    if drift:
        print(f"⚠️ leaves 与骨架树不一致 {len(drift)} 个: {drift[:5]}（新 prompt 已约束原样照抄）")
    if bad_pre or bad_rel:
        print(f"⚠️ 名称越界: prereq {len(bad_pre)}, related {len(bad_rel)}")
    get_tracker(cfg.get('tracker', 'relate_chapter')).print_summary()


if __name__ == '__main__':
    main()
