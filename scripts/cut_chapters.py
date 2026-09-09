#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章级切分（整章化流水线 Stage 0.5）：
  book_md  单本整 md（如 mineru full.md / normalized 整本）→ 按章标题切成章级 md
  chapter_dirs 已分章目录（如 操作系统/split/<章目录>）→ 每章聚合为一个 chapter.md（接口预留）

产出：
  <out>/chapter_index.json   章元信息：no/no_raw/title/heading/起止行/source/md_file
  <out>/chapters/<NN>_<title>.md  每章正文（习题/小结区剔除，图片语法原样保留——图说/识图由外部环节后置）

章标题只依赖 "第…章" 前缀（编号阿拉伯或中文数字），后随标题文字可空格/无空格/OCR 错字均不影响。
书末 "附录/答案/参考文献" 段与章内 "习题/练习/小结" 段分别由 truncate_book_at / strip_sections 处理。

用法：
  python scripts/cut_chapters.py --config config/xxx.json [--only 5] [--dry]
config 关键字段：
  domain / source / source_type / chapter_mode{title_re,truncate_book_at,strip_sections,exclude_chapters,chapter_structure} / chapter_out_dir
"""
import sys, io, json, re, argparse
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 章标题：行首若干 # + "第" + 编号(阿拉伯|中文数字) + "章"，编号后可有空白/直接跟标题
TITLE_RE_DEFAULT = r'^#{1,6}\s*第\s*([0-9]+|[一二三四五六七八九十百]+)\s*章'

CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn_to_int(s: str) -> int:
    """中文数字（1~999）转 int：一百二十三 / 十五 / 二十 / 三 等。"""
    if s.isdigit():
        return int(s)
    total = 0
    section = 0
    for ch in s:
        if ch in CN_NUM:
            section += CN_NUM[ch]
        elif ch == '十':
            section = section * 10 if section else 10
        elif ch == '百':
            total += section * 100
            section = 0
        elif ch == '千':
            total += section * 1000
            section = 0
    return total + section


def sanitize_filename(name: str) -> str:
    name = name.strip().replace('：', '-').replace('，', ',')
    name = re.sub(r'[\\/*?:"<>|$`{}^]', '', name)
    name = re.sub(r'\s+', '', name)
    return (name[:50]) or 'untitled'


class ChapterScanner:
    def __init__(self, cfg: dict):
        self.title_re = re.compile(cfg.get('title_re', TITLE_RE_DEFAULT))
        self.truncate_at = cfg.get('truncate_book_at', ['附录', '答案', '参考文献', '索引'])
        self.strip_sections = cfg.get('strip_sections',
                                      ['习题', '练习', '思考题', '复习题', '自测题',
                                       '本章小结', '本章的重要概念', '重要概念', '数学软件实验', '实验'])
        self.exclude = cfg.get('exclude_chapters') or []
        # 排除项：int（章号）或字符串（匹配原文编号/标题）
        self.excl_no = {x for x in self.exclude if isinstance(x, int)}
        self.excl_text = [str(x) for x in self.exclude if not isinstance(x, int)]

    def _is_strip_head(self, line: str) -> bool:
        m = re.match(r'^#{1,6}\s+(.+)$', line)
        if not m:
            return False
        t = m.group(1).strip()
        for s in self.strip_sections:
            if re.match(r'^' + re.escape(s), t):
                return True
        return False

    def _is_truncate_head(self, line: str) -> bool:
        m = re.match(r'^#{1,6}\s+(.+)$', line)
        if not m:
            return False
        t = m.group(1).strip()
        return any(re.match(r'^' + re.escape(s), t) for s in self.truncate_at)

    def _match_chapter(self, line: str):
        """返回 (no, rest_text) 或 None。rest_text = '章' 后同一行剩余文字。"""
        m = self.title_re.match(line)
        if not m:
            return None
        num_s = m.group(1)
        rest = line[m.end():].strip()
        no = cn_to_int(num_s)
        return no, rest

    def _excluded(self, no: int, rest: str) -> bool:
        if no in self.excl_no:
            return True
        return any(x in rest for x in self.excl_text)

    def scan(self, text: str):
        """返回 chapters 原始区间列表：[{no, no_raw, rest, heading, start, book_end_cap}]"""
        lines = text.split('\n')
        # 书末截断点：只认"最后一个正文章标题之后"的 附录/答案/参考文献/索引 顶层标题，
        # 且取其中最靠前的一个（书末附属区起点）——正文中间出现的同类标题一律不截断。
        # 例如每章末都有"参考文献"的书不会被提前截断；书末连续多组附录（附录A~E）
        # 会从附录 A 起整体切除，不会混入最后一章。
        trunc_pos = [i for i, ln in enumerate(lines)
                     if self._is_truncate_head(ln)]
        hits = []
        for i, ln in enumerate(lines):
            m = self._match_chapter(ln)
            if not m:
                continue
            no, rest = m
            hits.append({'no': no, 'no_raw': f'第{no}章', 'rest': rest,
                         'heading': ln.strip(), 'line': i})
        # 归并：无标题文字（rest 空）的伪章（习题答案分章）剔除
        real = [h for h in hits if h['rest']]
        last_ch_line = real[-1]['line'] if real else 0
        tail_trunc = [t for t in trunc_pos if t > last_ch_line]
        book_end = tail_trunc[0] if tail_trunc else len(lines)
        chapters = []
        for k, h in enumerate(real):
            if self._excluded(h['no'], h['rest']):
                continue
            end = real[k + 1]['line'] if k + 1 < len(real) else book_end
            chapters.append({'no': h['no'], 'no_raw': h['no_raw'],
                             'title': h['rest'], 'heading': h['heading'],
                             'start': h['line'], 'end': end})
        return lines, chapters, book_end

    def build_chapter_md(self, lines, start: int, end: int) -> str:
        """章正文：剔除 strip 区段（习题/小结…自标题行起直至章末）。"""
        out = []
        skipping = False
        for ln in lines[start:end]:
            if self._is_strip_head(ln):
                skipping = True
                continue
            if skipping and re.match(r'^#{1,6}\s+\S', ln):
                skipping = False  # 遇到新标题恢复（章内 strip 段通常到章末，兜底逻辑）
            if skipping:
                continue
            out.append(ln)
        return '\n'.join(out).rstrip() + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--only', default='', help='只切指定章号，逗号分隔')
    ap.add_argument('--dry', action='store_true', help='只扫描章清单不写文件')
    ap.add_argument('--out', default='', help='覆盖 config.chapter_out_dir')
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    cm = cfg.get('chapter_mode') or {}
    out_dir = Path(args.out or cfg.get('chapter_out_dir') or 'intermediate/chapters')
    out_dir.mkdir(parents=True, exist_ok=True)

    scanner = ChapterScanner(cm)
    src = Path(cfg['source'])
    if not src.is_absolute():
        src = Path(__file__).resolve().parent.parent / src
    text = src.read_text(encoding='utf-8')
    lines, chapters, book_end = scanner.scan(text)

    print(f"📖 {cfg.get('domain', '')} 源: {src}")
    print(f"   共识别正文章 {len(chapters)} 个（正文截断于 L{book_end}）")

    if args.only:
        only = {int(x) for x in args.only.split(',') if x.strip()}
        chapters = [c for c in chapters if c['no'] in only]

    md_dir = out_dir / 'chapters'
    md_dir.mkdir(parents=True, exist_ok=True)
    index = {'book': cfg.get('domain', ''), 'source': str(src),
             'source_type': cfg.get('source_type', 'book_md'),
             'chapters': []}

    for c in chapters:
        body = scanner.build_chapter_md(lines, c['start'], c['end'])
        fname = f"{c['no']:02d}_{sanitize_filename(c['title'])}.md"
        if args.dry:
            print(f"  · 第{c['no']}章 {c['title']}  L{c['start']}~L{c['end']}  "
                  f"{len(body)} 字符  → {fname}  (dry)")
            continue
        (md_dir / fname).write_text(body, encoding='utf-8')
        print(f"  · 第{c['no']}章 {c['title']}  L{c['start']}~L{c['end']}  "
              f"{len(body)} 字符  → {fname}")
        index['chapters'].append({
            'no': c['no'], 'no_raw': c['no_raw'], 'title': c['title'],
            'heading': c['heading'], 'start_line': c['start'], 'end_line': c['end'],
            'char_len': len(body), 'md_file': f"chapters/{fname}"})

    if not args.dry:
        (out_dir / 'chapter_index.json').write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"\n✅ chapter_index.json 已写（{len(index['chapters'])} 章）：{out_dir}")


if __name__ == '__main__':
    main()
