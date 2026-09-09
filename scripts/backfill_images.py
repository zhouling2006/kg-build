#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片资源回写（整章化流水线收尾，纯脚本，无 LLM）：
  把 docs/交付引用到的插图实体从源目录收集到统一 images/ 目录，
  保证交付目录自包含（.md 中 `![](images/xx.jpg)` 可解析）。

收集来源（引用形态）：
  1. markdown 图片引用 `![alt](任意路径/xx.jpg)` —— 取文件名
  2. 切章占位 `【图：xx.jpg】` / `【图 x：描述】`
搜索范围：--images-src 指定的源目录（如 mineru 输出的 images/，递归）；找不到再搜项目内。

用法：
  python scripts/backfill_images.py \
      --docs intermediate/cn_chapters/ch5_docs \
      --images-src "408源文件/_mineru_output/*/images" \
      --out-images intermediate/cn_chapters/images \
      [--extra "intermediate/cn_chapters/chapters/05_运输层.md"]
"""
import sys, io, re, shutil, argparse, glob as globmod
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

MD_IMG_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
PH_RE = re.compile(r'【图[^】]*[:：]\s*([^】]+?)\s*】')


def collect_basenames(texts: list[str]) -> set:
    names = set()
    for t in texts:
        for m in MD_IMG_RE.finditer(t):
            p = m.group(1)
            if p.lower().startswith('http'):
                continue
            names.add(Path(p).name)
        for m in PH_RE.finditer(t):
            names.add(m.group(1).strip())
    return {n for n in names if n}


def find_image(basename: str, roots: list[Path]) -> Path | None:
    # 同名直接命中（不区分大小写）
    for r in roots:
        if not r.exists():
            continue
        if (r / basename).exists():
            return r / basename
    for r in roots:
        if not r.exists():
            continue
        for f in r.rglob(basename):
            return f
    # 后缀大小写兜底
    for r in roots:
        if not r.exists():
            continue
        for f in r.rglob('*'):
            if f.is_file() and f.name.lower() == basename.lower():
                return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--docs', default='', help='docs 目录（扫描图片引用）')
    ap.add_argument('--extra', action='append', default=[], help='额外 md 文件（可多个）')
    ap.add_argument('--images-src', required=True, help='源图片目录 glob（可多个逗号分隔）')
    ap.add_argument('--out-images', required=True)
    args = ap.parse_args()

    texts = []
    if args.docs:
        d = Path(args.docs)
        texts += [f.read_text(encoding='utf-8', errors='ignore')
                  for f in d.rglob('*.md')]
    for x in args.extra:
        p = Path(x)
        if p.exists():
            texts.append(p.read_text(encoding='utf-8', errors='ignore'))

    names = collect_basenames(texts)
    roots = []
    for pat in args.images_src.split(','):
        roots += [Path(p) for p in globmod.glob(pat.strip())]

    out = Path(args.out_images)
    out.mkdir(parents=True, exist_ok=True)
    found, missing = [], []
    for n in sorted(names):
        src = find_image(n, roots)
        if src is None:
            missing.append(n)
            continue
        dst = out / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        found.append(n)

    print(f"🖼️ 引用图片 {len(names)} 个 → 落盘 {len(found)} · 缺失 {len(missing)}")
    if found:
        print(f"   ✅ {len(found)} 个已就位: {out}")
    if missing:
        print("   ❌ 缺失（请补 images-src）:")
        for n in missing[:20]:
            print(f"      {n}")


if __name__ == '__main__':
    main()
