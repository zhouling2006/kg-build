#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章结构自动探测（供 cut_chapters.py 的 chapter_mode 生成建议）：

把「切章正则」从拍脑袋假设变成数据驱动：扫整书 → 统计标题行编号形态 →
产出 title_re 建议 + 书末截断(truncate_book_at) / 章内剔除(strip_sections) 候选
→ 内置递增连续性自检 → 输出可直接贴进 cfg.chapter_mode 的 JSON。

用法：
  python scripts/detect_chapter_schema.py --source <整书full.md> [--lang auto] [--out <schema.json>]
  --lang auto|zh|en   标题词/附属词候选语言（auto 按正文 CJK 占比自动判）
  --out <json>        额外写一份机器可读 schema（含 title_re/chapter_mode/stats/warnings）

输出即报告 + 建议 JSON；拿到后用 cut_chapters --dry 复核即可铺量。
"""
import sys, io, json, re, argparse
from pathlib import Path
from collections import Counter

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 中文数字 → int（与 cut_chapters 一致）
CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn_to_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    total = section = 0
    for ch in s:
        if ch in CN_NUM:
            section += CN_NUM[ch]
        elif ch == '十':
            section = section * 10 if section else 10
        elif ch == '百':
            total += section * 100; section = 0
        elif ch == '千':
            total += section * 1000; section = 0
    return total + section


def roman_to_int(s: str) -> int:
    vals = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    t = 0
    for i, c in enumerate(s.upper()):
        v = vals.get(c, 0)
        if i + 1 < len(s) and vals.get(s[i + 1].upper(), 0) > v:
            t -= v
        else:
            t += v
    return t


def norm_num(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if all(c in '零一二三四五六七八九十百千' for c in raw):
        return cn_to_int(raw)
    if all(c.upper() in 'IVXLCDM' for c in raw):
        return roman_to_int(raw)
    return None


# 候选标题编号形态（有序）：返回 (family, kw, raw_num, rest)
NUM_CN = '[0-9]+|[一二三四五六七八九十百千零]+'
PATTERNS = [
    # 中文：第N章 / 第N篇 / 第N讲 …（章后缀字从命中里归纳）
    ('zh_chapter', re.compile(r'^\s*#{0,6}\s*第\s*(' + NUM_CN + r')\s*([章篇讲卷部])(?![\u4e00-\u9fff])'),
     '第N{章篇讲卷部}'),
    # 英文关键词前缀：Chapter/Unit/Lesson/Module/Part/Section N
    ('en_kw', re.compile(r'^\s*#{0,6}\s*(chapter|chap\.?|unit|lesson|module|part|section)\s*[:.]?\s*([0-9]+|[ivxlcdm]+)\b', re.I),
     'Chapter|Unit|Lesson… N'),
    # 中文无"第"：一、绪论 / 一 绪论
    ('cn_bare', re.compile(r'^\s*#{0,6}\s*([一二三四五六七八九十百千零]+)\s*[、.．，]\s*\S'),
     '一、绪论'),
    # 裸阿拉伯数字：1 绪论 / 1. 绪论 / 1．绪论（小节 "1.1 x" 由递增性自检淘汰）
    ('num_bare', re.compile(r'^\s*#{0,6}\s*([0-9]+)\s*[.、．]?\s*\S'),
     '1 绪论 / 1. 绪论'),
    # 罗马数字：I. Introduction（仅在无更强命中时采用）
    ('roman_bare', re.compile(r'^\s*#{0,6}\s*([ivxlcdm]+)\s*[.．]\s*\S', re.I),
     'I. Introduction'),
]

TRUNC_CAND = {
    'zh': ['附录', '答案', '参考文献', '索引', '术语表'],
    'en': ['References', 'Bibliography', 'Appendix', 'Index', 'Glossary', 'Solutions', 'Answer'],
}
STRIP_CAND = {
    'zh': ['习题', '练习', '思考题', '复习题', '自测题', '本章小结', '小结', '实验',
           '重要概念', '关键概念', '综合题', '应用题', '计算题'],
    'en': ['Exercises', 'Problems', 'Review', 'Questions', 'Summary', 'Key Terms',
           'Quiz', 'Test', 'Lab', 'Practice'],
}
MAX_LINE = 90  # 标题行长度上限（排除整段正文）


def guess_lang(text: str) -> str:
    sample = text[:20000]
    cjk = sum(1 for ch in sample if '\u4e00' <= ch <= '\u9fff')
    return 'zh' if cjk / max(len(sample), 1) > 0.01 else 'en'


def collect_hits(lines: list[str]):
    """对每族收集 (lineno, raw_num, line)。raw_num 取第一个编号捕获组。"""
    fam_hits = {}
    for fam, rx, _ in PATTERNS:
        hits = []
        for i, ln in enumerate(lines):
            if len(ln) > MAX_LINE or not ln.strip():
                continue
            m = rx.match(ln)
            if not m:
                continue
            groups = m.groups()
            raw = next((g for g in groups if g is not None), '')
            # 中文族第二组是后缀字，需排除在编号外
            if fam == 'zh_chapter':
                raw = groups[0]
            if fam == 'en_kw':
                raw = groups[1]
            n = norm_num(raw)
            if n is None or n < 1 or n > 10000:
                continue
            hits.append((i, n, ln.strip()))
        fam_hits[fam] = hits
    return fam_hits


def increasing_runs(hits: list[tuple[int, int, str]]):
    """按行序收集『编号连续 +1』的段（不要求行相邻——正文章标题之间隔着整章内容）。
    TOC 与正文各会形成一段（如 1..16 两次）。"""
    runs, cur = [], []
    for h in hits:
        if cur and h[1] == cur[-1][1] + 1:
            cur.append(h)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = [h]
    if len(cur) >= 3:
        runs.append(cur)
    return runs


def pick_body_run(runs: list[list]):
    """正文 run = 末尾行号最大的一段（正文最后一章的标题总在 TOC 之后）。"""
    return max(runs, key=lambda r: r[-1][0]) if runs else None


def scan_heads(lines, start, end):
    """区间内所有 md heading 行文本（去掉命中的正文标题不处理，交给上层过滤）。"""
    return [ln.strip() for ln in lines[start:end]
            if re.match(r'^#{1,6}\s+\S', ln) and len(ln.strip()) <= MAX_LINE]


def suggest_from_run(body_run, lines, lang: str):
    """由选中的正文标题 run 生成 (title_re, stats, notes)。"""
    fam = None
    # 反推最强 family：看首条命中匹配哪个模式族里最先的
    first_ln = body_run[0][2]
    kws = []
    for fam_i, (name, rx, _) in enumerate(PATTERNS):
        if rx.match(first_ln):
            fam = name
            break
    sufs = set()
    for _, _, ln in body_run:
        m = PATTERNS[0][1].match(ln)  # zh_chapter 后缀字
        if m:
            sufs.add(m.group(2))
        m2 = PATTERNS[1][1].match(ln)  # en_kw 关键词
        if m2:
            kws.append(m2.group(1).lower().rstrip('.'))
    # 用实际标题行判断编号用阿拉伯还是中文数字（两字符集可同时保留）
    has_cn_digit = any(re.search(r'[一二三四五六七八九十百千零]', h[2]) for h in body_run)
    has_arab = any(re.search(r'[0-9]', h[2]) for h in body_run)

    num_cls = []
    if has_arab:
        num_cls.append('[0-9]+')
    if has_cn_digit:
        num_cls.append('[一二三四五六七八九十百千零]+')
    num_union = '|'.join(num_cls) if num_cls else '[0-9]+'

    if fam == 'zh_chapter':
        suf = ''.join(sorted(sufs)) or '章'
        title_re = rf'^#{{0,6}}\s*第\s*({num_union})\s*[{re.escape(suf)}]'
    elif fam == 'en_kw':
        kw = '|'.join(dict.fromkeys(kws)) if kws else 'chapter'
        title_re = (rf'^#{{0,6}}\s*(?:{kw})\s*[:.]?\s*({num_union})'
                    r'\s*[:.\-–]?\s*')
    elif fam == 'cn_bare':
        title_re = rf'^#{{0,6}}\s*({num_union})\s*[、.．，]\s*'
    elif fam == 'num_bare':
        title_re = rf'^#{{0,6}}\s*({num_union})\s*[.、．]?\s+'
    elif fam == 'roman_bare':
        title_re = rf'^#{{0,6}}\s*([ivxlcdm]+)\s*[.．]\s+'
    else:
        title_re = ''

    start_l, end_l = body_run[0][0], body_run[-1][0]
    no_series = [h[1] for h in body_run]
    return {
        'title_re': title_re,
        'family': fam,
        'body_span': (start_l, end_l),
        'count': len(body_run),
        'no_from': no_series[0],
        'no_to': no_series[-1],
        'per_line_delta': (end_l - start_l) / max(len(body_run) - 1, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--lang', default='auto', choices=['auto', 'zh', 'en'])
    ap.add_argument('--out', default='', help='额外写 schema json')
    ap.add_argument('--cfg-template', default='',
                    help='以现有 cfg（如 config/dl_chapter.json）为基底合并出整份新书配置')
    ap.add_argument('--cfg-out', default='',
                    help='合并结果写出路径（与 --cfg-template 成对使用）')
    args = ap.parse_args()
    if (args.cfg_template or args.cfg_out) and not (args.cfg_template and args.cfg_out):
        ap.error('--cfg-template 与 --cfg-out 必须成对给出')

    def _resolve(p: str) -> Path:
        p = Path(p)
        return p if p.is_absolute() else Path(__file__).resolve().parent.parent / p

    src = _resolve(args.source)
    text = src.read_text(encoding='utf-8')
    lines = text.split('\n')
    lang = args.lang if args.lang != 'auto' else guess_lang(text)
    print(f"📖 源: {src}  ({len(text):,} 字符, {len(lines)} 行)")
    print(f"   判定语言: {'中文' if lang == 'zh' else '英文'}")

    hits = collect_hits(lines)
    fam_info = {f: len(h) for f, h in hits.items()}
    top = sorted(fam_info.items(), key=lambda kv: -kv[1])[:2]
    print(f"   编号形态命中: " + ', '.join(f'{f}={n}' for f, n in top))

    warnings = []
    chapter_mode = {}
    # 每族取它的 best run（末尾行号最大的一段），再按 (长度, 末尾行靠后, 族优先级)
    # 全局择优：正文段（如 zh 的 1..16）会赢过小节/目录噪声短段；平局时靠后=正文、zh 族优先。
    FAM_PRIO = {'zh_chapter': 0, 'en_kw': 1, 'cn_bare': 2, 'num_bare': 3, 'roman_bare': 4}
    cands = []
    for name, _, _ in PATTERNS:
        runs = increasing_runs(hits[name])
        if runs:
            r = pick_body_run(runs)
            cands.append((len(r), r[-1][0], -FAM_PRIO[name], name, r))
    if cands:
        _, _, _, used_fam, body = max(cands)
    else:
        # 退化：无族形成 ≥3 递增段 → 按命中最多的族直取整族，提示人工复核
        flat = [(len(hits[f]), f) for f, _ in fam_info.items() if hits[f]]
        if not flat:
            print('❌ 未识别到任何编号标题形态。请在 cfg.chapter_mode.title_re 手工给正则。')
            return 1
        _, used_fam = max(flat)
        body = hits[used_fam]
        warnings.append('未找到 ≥3 的连续递增段，按最高命中族直取，建议人工复核')

    # 目录诊断：同族往往另有一段更早、行距极密的连续段（目录）。
    # 若目录段编号范围 ≠ 正文段编号范围，说明正文有章标题在源文件缺失
    # （转换丢章首页/标题行），按章切分会缺章——必须提示而不是静默。
    runs_all = increasing_runs(hits[used_fam])
    body_first = body[0][0]
    catalog_runs = [r for r in runs_all if r[-1][0] < body_first]
    cat_info = ''
    if catalog_runs:
        cat = max(catalog_runs, key=len)
        cat_span_rows = (cat[-1][0] - cat[0][0]) / max(len(cat) - 1, 1)
        cat_info = (f"，另见疑似目录段 {len(cat)} 条"
                    f"（编号 {cat[0][1]}→{cat[-1][1]}，L{cat[0][0] + 1}~L{cat[-1][0] + 1}"
                    f"，平均 {cat_span_rows:.0f} 行/条）")
        if cat[-1][1] != body[-1][1]:
            warnings.append(
                f'疑似目录段编号到 {cat[-1][1]}，正文可切连续标题只到 {body[-1][1]}，'
                '二者不一致：正文可能存在章标题缺失（转换丢标题/章首页）或上下册各自起编。'
                '按当前 title_re 切分会缺章，请回源文件核查后补齐标题再切。'
            )

    sugg = suggest_from_run(body, lines, lang)
    print(f"\n   正文标题形态: {sugg['family']}  → 命中 {sugg['count']} 章"
          f"（编号 {sugg['no_from']} → {sugg['no_to']}）{cat_info}")
    print(f"   正文区: L{sugg['body_span'][0] + 1} ~ L{sugg['body_span'][1] + 1}")

    title_re = sugg['title_re']
    chapter_mode['title_re'] = title_re

    # 章内剔除候选：在正文区 heading 里统计（扫到文件尾；附录类词在截断库、不在此计）
    head_counter = Counter()
    for h in scan_heads(lines, sugg['body_span'][0], len(lines)):
        for c in STRIP_CAND[lang]:
            if h.startswith(c):
                head_counter[c] += 1
    strip = [c for c, n in head_counter.most_common() if n >= 2][:8]
    # 书末截断候选：正文 run 之后的标题
    tail_counter = Counter()
    for h in scan_heads(lines, sugg['body_span'][1] + 1, min(len(lines), sugg['body_span'][1] + 4000)):
        for c in TRUNC_CAND[lang]:
            if h.startswith(c):
                tail_counter[c] += 1
    trunc = [c for c, _ in tail_counter.most_common()][:5]

    chapter_mode['truncate_book_at'] = trunc or (TRUNC_CAND[lang][:4])
    chapter_mode['strip_sections'] = strip or (STRIP_CAND[lang][:4])
    chapter_mode['exclude_chapters'] = []

    if not trunc:
        warnings.append('书末未检出 附录/答案/参考文献/索引 类标题，已保留该语言通用候选，请留意')
    if sugg['per_line_delta'] < 30:
        warnings.append('正文标题行距过密，疑似把目录/子节混入，建议人工复核清单')
    if sugg['count'] < 5:
        warnings.append(f"章数偏少({sugg['count']})，若不是短篇教材请复核 title_re 是否过严")

    schema = {
        'lang': lang,
        'title_pattern': used_fam,
        'title_re': title_re,
        'chapter_stats': {
            'count': sugg['count'],
            'no_from': sugg['no_from'],
            'no_to': sugg['no_to'],
            'body_start_line': sugg['body_span'][0] + 1,
            'body_end_line': sugg['body_span'][1] + 1,
        },
        'chapter_mode': chapter_mode,
        'warnings': warnings,
    }

    print('\n' + '=' * 60)
    print('✅ 建议 chapter_mode（可直接贴进 cfg）：')
    print(json.dumps(chapter_mode, ensure_ascii=False, indent=2))
    if warnings:
        print('\n⚠️  警告：')
        for w in warnings:
            print(f'   - {w}')
    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nschema 已写: {out}')

    # 以现有 cfg 为基底，合并成可直接 run_pipeline_whole --config 的新书配置
    if args.cfg_template:
        tpl = json.loads(_resolve(args.cfg_template).read_text(encoding='utf-8'))
        tpl['source'] = src.as_posix()      # 指向本次探测的书
        if 'source_pdf' in tpl:
            tpl['source_pdf'] = ''          # 基底里上一本书的 pdf 不能误带，置空防 S-1 误触发
        tpl['chapter_mode'] = chapter_mode  # 用建议值覆盖
        out_cfg = _resolve(args.cfg_out)
        out_cfg.parent.mkdir(parents=True, exist_ok=True)
        out_cfg.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n✅ 已按基底 {_resolve(args.cfg_template)} 生成新书配置:')
        print(f'   {out_cfg}')
        print('   自动覆盖: chapter_mode（本次建议值）、source（指向探测书）、source_pdf（置空）')
        print('   需人工改: domain / subject_name / subject_tag / chapter_out_dir / model，')
        print('   image_config.mineru_dir 指向新书 MinerU 产物；新书未转 MinerU 请把 image_desc 置 false')
        if warnings:
            print('   ⚠️ 仍有探测警告未消除，先用 cut_chapters --dry 复核再铺量')

    print('复核：python scripts/cut_chapters.py --config 你的cfg --dry')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
