# -*- coding: utf-8 -*-
"""一次性：把 docs_all/<章>/*.md 中引用的图片从 MinerU images/ 收集到 docs_all/<章>/images/，
使文档相对引用 images/xxx.jpg 可解析。幂等，可全书完成后重跑。

路径全部从顶层配置推导（chapter_out_dir + image_config.mineru_dir），换书无需改本文件：
  python scripts/_tmp_collect_imgs.py [--config config/dl_chapter.json]
"""
import sys, io, re, shutil, json, argparse
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
IMG_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')


def collect_basenames(doc_dir):
    names = set()
    for f in doc_dir.rglob('*.md'):
        t = f.read_text(encoding='utf-8', errors='ignore')
        for m in IMG_RE.finditer(t):
            p = m.group(1).strip()
            if p.lower().startswith('http'):
                continue
            names.add(Path(p).name)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config/dl_chapter.json',
                    help='顶层配置（chapter_out_dir + image_config.mineru_dir）')
    args = ap.parse_args()
    cfg_p = Path(args.config)
    if not cfg_p.is_absolute():
        cfg_p = ROOT / cfg_p
    cfg = json.loads(cfg_p.read_text(encoding='utf-8'))

    run = Path(cfg.get('chapter_out_dir', 'intermediate/chapters'))
    RUN = run if run.is_absolute() else ROOT / run
    mineru = (cfg.get('image_config') or {}).get('mineru_dir', '')
    mdir = Path(mineru) if mineru and Path(mineru).is_absolute() \
        else (ROOT / mineru if mineru else None)
    # 与 S7 同样归一：mineru_dir 可能指向 …/images，也可能指向 MinerU 根
    SRC = mdir if mdir and mdir.name == 'images' \
        else (mdir / 'images' if mdir else None)
    docs_root = RUN / 'docs_all'
    if not docs_root.is_dir() or SRC is None or not SRC.is_dir():
        raise SystemExit(f'❌ 路径无效：docs_all={docs_root} / images={SRC}\n'
                         '   请确认 --config 的 chapter_out_dir 与 image_config.mineru_dir')

    total_ok = total_miss = 0
    for d in sorted(docs_root.iterdir()):
        if not d.is_dir():
            continue
        names = collect_basenames(d)
        if not names:
            print(f'⏭️ {d.name}: 无图片引用')
            continue
        out = d / 'images'
        out.mkdir(parents=True, exist_ok=True)
        ok = miss = 0
        for n in sorted(names):
            src = SRC / n
            if not src.exists():
                miss += 1
                print(f'  ❌ MISS {n}')
                continue
            dst = out / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
            ok += 1
        total_ok += ok
        total_miss += miss
        print(f'🖼️ {d.name}: 图 {ok} 就位 · 缺 {miss}')
    print(f'\n✅ 合计就位 {total_ok} · 缺失 {total_miss}')


if __name__ == '__main__':
    main()
