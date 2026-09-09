#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整章化端到端流水线（取代 v3 的"切分→二次加工→…→图谱"知识点提取链路）
  保留：mineru 转换/normalized、图片占位与识图（外部环节，本编排不重跑 mineru）
  本编排负责章级主链路：
    S-1 mineru(可选)      整本 md 缺失且 cfg 给 source_pdf 时自动 PDF→MinerU→full.md（外部转换）
  S0 cut_chapters        整本 md → 章级 chapter.md + chapter_index.json（纯脚本）
    S0.5 image_desc(可选) 插图 qwen-vl-max 描述写回（cfg.image_config.image_desc=true 时启用）
    S1 extract_chapter     每章：整章一次调用 → 知识单元树（LLM）
    S2 relate_chapter      每章：骨架一次调用 → 章内关系（LLM）
    S3 materialize         每章：物化 uuid 图谱 chapter_graph.json（纯脚本）
    S4 gen_docs            每章：每 topic 一片知识点文档（LLM）
    S5 cross_chapter       跨章关系（LLM，可选 --skip-cross）
    S6 merge_books         全书合并 book_graph.json（纯脚本）
    S7 backfill_images     图片资源收集（纯脚本）
    S8 export_from_graph   交付导出 → <run_dir>/deliver（纯脚本）

用法：
  python scripts/run_pipeline_whole.py --config config/cn_chapter.json [--dry-run] [--only 5]
config 字段见 config/cn_chapter.json（pipeline=whole_chapter）。dry-run 不调 LLM，
纯脚本阶段照常产出；LLM 阶段只写 prompt 预览。
"""
import sys, io, os, json, time, subprocess, argparse
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

PYTHON = "python"
BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
LLM_SCRIPTS = ['extract_chapter.py', 'relate_chapter.py', 'gen_docs.py',
               'cross_chapter_links.py']

STAGES = ['S0 cut_chapters', 'S1 extract_chapter', 'S2 relate_chapter',
          'S3 materialize_chapter', 'S4 gen_docs', 'S5 cross_chapter_links',
          'S6 merge_books', 'S7 backfill_images', 'S8 export_from_graph']


def run(cmd, desc):
    print(f"\n▶ {desc}")
    print(f"  {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, cwd=str(BASE), encoding='utf-8')
    if r.returncode != 0:
        raise SystemExit(f"❌ {desc} 失败 (exit {r.returncode})")
    return True


def write_cfg(obj, p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', '-c', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--only', default='', help='只跑指定章号（逗号分隔）')
    ap.add_argument('--force', action='store_true', help='LLM 产物已存在也重跑')
    ap.add_argument('--skip-cross', action='store_true')
    ap.add_argument('--from', dest='from_stage', type=int, default=0)
    ap.add_argument('--to', dest='to_stage', type=int, default=8)
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = BASE / cfg_path
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    model = cfg.get('model', 'qwen3.7-plus')
    domain = cfg.get('domain', '')

    run_dir = Path(cfg.get('chapter_out_dir', f"intermediate/{domain}_whole"))
    if not run_dir.is_absolute():
        run_dir = BASE / run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = run_dir / 'run_cfg'
    cfg_dir.mkdir(exist_ok=True)
    stages = range(args.from_stage, args.to_stage + 1)
    force = args.force
    dry = args.dry_run
    print("=" * 60)
    print(f"🚀 整章化流水线 · {domain} · {model} · dry_run={dry}")
    print(f"  目录: {run_dir.relative_to(BASE)}")
    print("=" * 60)

    # ── S-1 MinerU 前置（可选）→ S0 cut_chapters ──
    index_file = run_dir / 'chapter_index.json'
    if 0 in stages and (force or not index_file.exists()):
        # 整书源 md 缺失：若有 source_pdf 则自动走 MinerU（pdf_to_full_md 断点续传：
        # full.md 已存在则跳过，无需重转）。缺书时给清晰指引而非盲目跑下去。
        src_md = Path(cfg.get('source', ''))
        if not src_md.is_absolute():
            src_md = BASE / src_md
        pdf_cfg = str(cfg.get('source_pdf', ''))
        if not src_md.exists():
            if pdf_cfg:
                pp = Path(pdf_cfg) if Path(pdf_cfg).is_absolute() else BASE / pdf_cfg
                if not pp.exists():
                    raise SystemExit(f'❌ cfg.source_pdf 不存在: {pp}')
                print('\n🛠 整书 md 缺失，自动执行 PDF → MinerU → full.md（长任务）…')
                run([PYTHON, str(SCRIPTS / 'pdf_to_full_md.py'),
                     '--pdf', str(pp)], 'S-1 MinerU 整书转换')
            else:
                raise SystemExit(
                    f'❌ 缺整书源 md: {src_md}\n'
                    '   换新书请先跑 scripts/pdf_to_full_md.py 转换，'
                    '或在该 cfg 加 "source_pdf" 让 S-1 自动转换')
        if not src_md.exists():
            raise SystemExit(f'❌ MinerU 转换未产出: {src_md}')
        run([PYTHON, str(SCRIPTS / 'cut_chapters.py'),
             '--config', str(cfg_path)], 'S0 切章')
    elif 0 not in stages:
        pass
    else:
        print('⏭️ S0 已有 chapter_index.json，跳过（--force 重跑）')

    if not index_file.exists():
        raise SystemExit('❌ 缺 chapter_index.json（先跑 S0）')
    idx = json.loads(index_file.read_text(encoding='utf-8'))
    chs = [c for c in idx.get('chapters', [])]
    if args.only:
        only = {int(x) for x in args.only.split(',') if x.strip()}
        chs = [c for c in chs if c['no'] in only]
    excl = set(cfg.get('chapter_mode', {}).get('exclude_chapters') or [])
    if not args.only:
        chs = [c for c in chs
               if c['no'] not in excl and c['no_raw'] not in excl
               and c['title'] not in excl]
    print(f"\n📖 章序: {len(chs)} 章 → "
          + '、'.join(f"第{c['no']}章" for c in chs))

    # ── image_config 自动归一：MinerU 图源可从 source 推导，识别图开关随产物自动 ──
    # MinerU 布局固定：<pdf>/_mineru/<书>/ 含 full.md + images/ + *_content_list.json，
    # 因此 source(=full.md) 的父目录即 MinerU 根。显式给了 mineru_dir 则优先，
    # 无效/缺失时自动回落推导；image_desc 未显式给时按"是否有真实图源"自动开/关；
    # desc_out 缺省 = 运行目录/image_meta。新书只需 source，这些字段可整块省略。
    imcfg = cfg.get('image_config') or {}
    src_path = Path(cfg.get('source', ''))
    if not src_path.is_absolute():
        src_path = BASE / src_path
    mdir = None            # MinerU 根（images/ 与 *_content_list.json 所在目录）
    cfg_note = []
    mcfg = str(imcfg.get('mineru_dir', '')).strip()
    if mcfg:
        p = Path(mcfg) if Path(mcfg).is_absolute() else BASE / mcfg
        if p.name == 'images':
            p = p.parent
        if (p / 'images').exists():
            mdir = p
        else:
            cfg_note.append('mineru_dir 指向的目录无 images/，视为无效')
    if mdir is None and (src_path.parent / 'images').exists():
        mdir = src_path.parent
        cfg_note.append(('cfg.mineru_dir 无效已回落' if mcfg else 'mineru_dir 未配置') +
                        f'，已从 source 自动推导: {mdir}')
    # 识别图开关：显式给则尊重；未给则随图源真实可用自动决定
    image_desc = imcfg.get('image_desc')
    if image_desc is None:
        image_desc = mdir is not None
        cfg_note.append(f'image_desc 未配置，按图源自动= {"开" if image_desc else "关"}')
    # desc_out 落点：显式给则尊重，否则 = 运行目录/image_meta
    desc_out = Path(str(imcfg.get('desc_out', '')).strip()) if imcfg.get('desc_out') else run_dir / 'image_meta'
    if not desc_out.is_absolute():
        desc_out = BASE / desc_out
    if cfg_note:
        print('   [auto] image_config: ' + '；'.join(cfg_note))
    imgs_src = str(mdir / 'images') if mdir else ''

    # ── S0.5 图片识图（cfg.image_config.image_desc=true 时启用）──
    # 对 chapters/*.md 里的插图做 qwen-vl-max 描述并写回"（描述：…）"，
    # 供后续 S1 骨架 / S4 docs 感知图的内容；公式图（MinerU 已内联）自动跳过。
    # S0.5 挂在 stage 0（切章）之后：阶段范围须含 S0 才跑，避免每次续跑
    # S4~S8 都无条件重扫全书（幂等但纯属浪费）。
    if image_desc and 0 in stages:
        ch_md_dir = run_dir / 'chapters'
        if mdir and mdir.exists():
            # 目标：全量 = chapters 目录；--only N → 只对选中的章 md 逐个识图
            # （vl_cache 在同一 desc_out 内累积，重跑/换章不重复扣费；候选/报告留最后一章）
            targets = ([run_dir / c['md_file'] for c in chs]
                       if args.only else [ch_md_dir])
            targets = [t for t in targets if t.exists()]
            if not targets:
                print('⏭️ S0.5 无可用章 md，跳过识图')
            for t in targets:
                cmd = [PYTHON, str(SCRIPTS / 'describe_book_images.py'),
                       '--mode', 'scan' if dry else 'all',
                       '--target', str(t), '--mineru-dir', str(mdir),
                       '--out-dir', str(desc_out),
                       '--max-workers', str(imcfg.get('desc_workers', 4))]
                tag = Path(t).name if Path(t).is_file() else '全书'
                if dry:
                    print('⏭️ S0.5 图片识图 dry-run：仅扫描候选，不调 VL')
                run(cmd, f'S0.5 图片识图 {tag}')
        else:
            print('⏭️ S0.5 图片识图跳过（MinerU 图源不可用：无有效 mineru_dir 且 source 同目录无 images/）')

    # ── S1/S2/S3：每章 extract → relate → materialize ──
    graph_files = []
    for c in chs:
        no, title = c['no'], c['title']
        ch_label = f"第{no}章 {title}"
        pc = run_dir / 'per_chapter' / f"{no:02d}_{title}"
        pc.mkdir(parents=True, exist_ok=True)
        md_file = Path(c['md_file'])
        if not md_file.is_absolute():
            md_file = run_dir / md_file
        chapter_md = str(md_file.absolute())

        # S1 extract
        skel = pc / 'chapter_skeleton.json'
        if 1 in stages and (force or not skel.exists()):
            ecfg = write_cfg({
                'chapter_md': chapter_md, 'chapter': ch_label, 'out': str(skel),
                'model': model, 'tracker': f'extract_cn{no}',
            }, cfg_dir / f'extract_{no:02d}.json')
            cmd = [PYTHON, str(SCRIPTS / 'extract_chapter.py'), '--cfg', ecfg]
            if dry:
                cmd.append('--dry-run')
            run(cmd, f'S1 extract 第{no}章')
        elif 1 not in stages:
            pass
        else:
            print(f'⏭️ S1 第{no}章骨架已存在，跳过')

        # S2 relate
        rel = pc / 'relations.json'
        if 2 in stages and (force or not rel.exists()) and skel.exists():
            rcfg = write_cfg({
                'skeleton': str(skel), 'chapter': ch_label, 'out': str(rel),
                'model': model, 'tracker': f'relate_cn{no}',
            }, cfg_dir / f'relate_{no:02d}.json')
            cmd = [PYTHON, str(SCRIPTS / 'relate_chapter.py'), '--cfg', rcfg]
            if dry:
                cmd.append('--dry-run')
            run(cmd, f'S2 relate 第{no}章')
        elif 2 not in stages:
            pass
        elif not skel.exists():
            print(f'⏭️ S2 第{no}章缺 skeleton（先跑 S1），跳过')
        else:
            print(f'⏭️ S2 第{no}章 relations 已存在，跳过')

        # S3 materialize
        cg = pc / 'chapter_graph.json'
        if 3 in stages and (force or not cg.exists()) and skel.exists() and rel.exists():
            run([PYTHON, str(SCRIPTS / 'materialize_chapter.py'),
                 '--chapter-index', str(index_file), '--chapter-no', str(no),
                 '--skeleton', str(skel), '--relations', str(rel),
                 '--chapter-md', chapter_md, '--out', str(cg)],
                f'S3 materialize 第{no}章')
        elif 3 not in stages:
            pass
        elif not skel.exists() or not rel.exists():
            print(f'⏭️ S3 第{no}章缺 skeleton/relations，跳过')
        else:
            print(f'⏭️ S3 第{no}章 chapter_graph 已存在，跳过')
        if cg.exists():
            graph_files.append(cg)

    # ── S4 gen_docs（每章）──
    docs_root = run_dir / 'docs_all'
    if 4 in stages and graph_files:
        for cg in graph_files:
            g = json.loads(cg.read_text(encoding='utf-8'))
            no = g.get('chapter', '')
            d_out = docs_root / cg.parent.name
            dcfg = write_cfg({
                'graph': str(cg),
                'chapter_md': g.get('source', ''),
                'out_dir': str(d_out),
                'subject_name': cfg.get('subject_name', domain),
                'subject_tag': cfg.get('subject_tag', ''),
                'samples': cfg.get('samples', []),
                'model': model, 'tracker': f'gen_docs_{cg.parent.name}',
            }, cfg_dir / f'docs_{cg.parent.name}.json')
            cmd = [PYTHON, str(SCRIPTS / 'gen_docs.py'), '--cfg', dcfg]
            if not force:
                cmd.append('--skip-existing')   # 断点续跑：已生成的叶子文档跳过，不重复计费
            if dry:
                cmd.append('--dry-run')
            run(cmd, f'S4 docs {g.get("chapter")}')
    else:
        print('⏭️ S4 跳过（无 chapter_graph 或不在阶段范围）')

    # ── S5 跨章边 ──
    cross_file = run_dir / 'cross_links.json'
    if 5 in stages and len(graph_files) > 1 and not args.skip_cross:
        pat = str(run_dir / 'per_chapter' / '*' / 'chapter_graph.json')
        cmd = [PYTHON, str(SCRIPTS / 'cross_chapter_links.py'),
               '--graphs', pat, '--out', str(cross_file)]
        if dry:
            cmd.append('--dry-run')
        run(cmd, 'S5 跨章关系')
    else:
        print('⏭️ S5 跳过（章数≤1 / 已 skip / 不在范围）')

    # ── S6 merge ──
    book_graph = run_dir / 'book_graph.json'
    if 6 in stages and graph_files:
        pat = str(run_dir / 'per_chapter' / 'ch*_graph.json')
        # 物化产物命名：per_chapter/05_xxx/chapter_graph.json → 用直接列表参数不可行（脚本是 glob）
        # 物化输出统一为 chapter_graph.json，merge 用 glob 匹配
        merge_glob = str(run_dir / 'per_chapter' / '*' / 'chapter_graph.json')
        cmd = [PYTHON, str(SCRIPTS / 'merge_books.py'),
               '--graphs', merge_glob, '--out', str(book_graph)]
        if cross_file.exists():
            cmd += ['--cross', str(cross_file)]
        run(cmd, 'S6 全书合并')
    else:
        print('⏭️ S6 跳过（无 chapter_graph）')

    # ── S7 backfill_images ──
    img_dir = run_dir / 'images'
    if 7 in stages and book_graph.exists():
        # 图源与 S0.5 同一套归一结果（imgs_src = <MinerU根>/images）；
        # 仅当 cfg 额外给了 images_glob（自定义目录）才覆盖。
        src_glob = cfg.get('image_config', {}).get('images_glob', '')
        if not src_glob:
            src_glob = imgs_src
        extra = [str(run_dir / 'chapters' / c['md_file'])
                 for c in idx.get('chapters', [])]
        cmd = [PYTHON, str(SCRIPTS / 'backfill_images.py'),
               '--docs', str(docs_root), '--out-images', str(img_dir)]
        for x in extra:
            p = Path(x) if Path(x).is_absolute() else run_dir / x
            if p.exists():
                cmd += ['--extra', str(p)]
        if src_glob:
            cmd += ['--images-src', src_glob]
        else:
            cmd += ['--images-src', str(run_dir / 'nope')]
        run(cmd, 'S7 图片资源回写')
    else:
        print('⏭️ S7 跳过')

    # ── S8 export ──
    if 8 in stages and book_graph.exists():
        # 交付目录名用 deliver/（避免与"数据结构 ds"混淆）
        deliver_dir = run_dir / 'deliver'
        run([PYTHON, str(SCRIPTS / 'export_from_graph.py'),
             '--graph', str(book_graph), '--texts', str(docs_root),
             '--images', str(img_dir), '--out', str(deliver_dir)],
            'S8 交付导出')
    else:
        print('⏭️ S8 跳过')

    print(f"\n✅ 整章化流水线执行完毕 · 产物: {run_dir}")


if __name__ == '__main__':
    main()
