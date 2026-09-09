#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用入口：任意 PDF → MinerU 精准解析 → 整本 md（取代每本书一个 runner 脚本）

用法：
  python scripts/pdf_to_full_md.py --pdf <文件.pdf> [--out-dir <目录>] [--md-name full.md]

  --out-dir 缺省 = <pdf 所在目录>/_mineru/<pdf 文件名不含后缀>
  --md-name 缺省 full.md（写 <out-dir>/full.md）
  MinerU token 从根 .env 的 MINERU_API_TOKEN 读取；没有 token 自动降级 Agent 轻量解析
  （20 页/块）。断点续传：<out-dir>/<md-name> 已存在且非空则跳过。
  --clean  转换前清理旧产物（默认保留，避免误删）

示例：
  python scripts/pdf_to_full_md.py --pdf deeplearning/nndl-v2.pdf
  python scripts/pdf_to_full_md.py --pdf "408源文件/xxx.pdf" --out-dir "408源文件/_mineru_output/xxx"
"""
import sys, io, os, re, argparse
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass
from scripts.mineru_client import MinerUClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', required=True, help='输入 PDF（绝对或相对工作区根）')
    ap.add_argument('--out-dir', default='', help='输出目录（缺省 = <pdf>/_mineru/<stem>）')
    ap.add_argument('--md-name', default='full.md', help='合并 md 文件名（默认 full.md）')
    ap.add_argument('--clean', action='store_true', help='转换前清空旧输出目录')
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_absolute():
        pdf = ROOT / pdf
    if not pdf.exists():
        raise SystemExit(f'PDF 不存在: {pdf}')

    out_dir = Path(args.out_dir) if args.out_dir else pdf.parent / '_mineru' / pdf.stem
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
    if args.clean and out_dir.exists():
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)

    print('=' * 60)
    print('PDF → MinerU 精准解析 → 整本 md')
    print('=' * 60)
    client = MinerUClient(
        api_token=os.getenv('MINERU_API_TOKEN'),
        enable_formula=True,
        enable_table=True,
        is_ocr=True,
        language='ch',
        poll_interval=5,
        max_wait=2400,
        output_root=str(out_dir),
    )
    mode = '精准解析' if client.api_token else 'Agent轻量'
    print(f'模式: {mode} (Token={"已设置" if client.api_token else "未设置"})')

    out_md = out_dir / args.md_name
    if out_md.exists() and out_md.stat().st_size > 1000:
        print(f'[SKIP] 已有输出: {out_md} ({out_md.stat().st_size} 字节)')
    else:
        md = client.parse_file(str(pdf), str(out_dir))
        out_md.write_text(md, encoding='utf-8')
        print(f'[OK] -> {out_md} ({len(md)} 字符)')

    imgs = re.findall(r'!\[[^\]]*\]\([^)]*\)', out_md.read_text(encoding='utf-8'))
    print(f'md 中图片引用: {len(imgs)} 处')
    img_dir = out_dir / 'images'
    if img_dir.exists():
        print(f'images 目录: {len(list(img_dir.iterdir()))} 个文件')


if __name__ == '__main__':
    main()
