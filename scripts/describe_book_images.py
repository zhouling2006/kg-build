# -*- coding: utf-8 -*-
"""v4 预处理 · 图片解析（教材版）

输入：教材 MinerU 整本 md（或 split 目录）+ MinerU 产物目录
流程：
  1. scan   : 扫描图片引用 → 用 *_content_list.json（递归）分类：
              equation（公式图，MinerU 已内联 LaTeX，跳过）/ image（普通图，VL 候选）/ unmatched
  2. vl     : 分批 qwen-vl-max 描述（带图片前后原文上下文），
              按「文件名+大小」缓存到 out-dir/vl_cache.json，断点续跑不重复花费
  3. write  : 写回图说：
              有意义图  → 图片引用后追加（描述：...）
              无关图    → 替换为占位注释（保留可追溯）
              公式/未匹配 → 原样保留

产物（默认 test_os/image/）：
  candidates.json   候选清单（普通图 → VL 队列，含上下文）
  vl_cache.json     VL 描述缓存
  report.json       统计 + 写回明细

用法：
  python scripts/describe_book_images.py --mode all
  python scripts/describe_book_images.py --mode scan   # 只扫描候选
  python scripts/describe_book_images.py --mode vl     # 只跑 VL 描述（复用已有候选/缓存）
  python scripts/describe_book_images.py --mode write  # 只写回
可选：
  --target <目录|文件>   默认 test_os/split（目录逐文件处理）
  --mineru-dir <目录>    默认 408源文件/_mineru_output/<操作系统>（自动探测）
  --out-dir <目录>       默认 test_os/image
  --max-workers N        VL 并发数，默认 4
  --limit N              只处理前 N 张候选（测试用）
"""
import sys
import os
import re
import json
import time
import base64
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────
# 默认路径（脚本内部推导，规避 Windows 中文路径 argv 乱码）
# ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
TEST_OS = ROOT / "test_os"
DEFAULT_TARGET = TEST_OS / "split"
DEFAULT_OUT_DIR = TEST_OS / "image"
MINERU_BOOK_DIRS = [
    ROOT / "408源文件" / "_mineru_output" / "计算机操作系统（第四版） (汤小丹) (z-library.sk, 1lib.sk, z-lib.sk)",
]

VL_MODEL = "qwen-vl-max"

# 教材版 VL 判定提示词（区别于 exam_parser 的数学试卷版）
VL_SYS_PROMPT = (
    "你是教材图片解析助手。用户会给你一张教材插图，以及它在教材 Markdown 中的上下文。"
    "请判断这张图片是否与教材正文内容相关：\n"
    "- 示意图 / 流程图 / 结构图 / 状态图 / 时序图 / 表格 / 实物照片等与正文论述相关的图，"
    "输出 {\"relevant\": true, \"description\": \"一句话中文描述，说清图的类型与关键特征\"}\n"
    "- 水印、二维码、广告、页码、页眉页脚、logo、封面装饰、纯装饰插图等与正文无关的内容，"
    "输出 {\"relevant\": false}\n"
    "只输出 JSON，不要其它任何文字。"
)

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────
def _load_content_list_meta(mineru_dir: Path) -> tuple[dict, set]:
    """递归读取所有 *_content_list.json：返回 (eq_map, image_set)。

    - eq_map  : 图片 basename -> LaTeX 公式文本（type=equation 且带 text）
    - image_set: 非公式图片 basename 集合（type=image）
    MinerU 教材产物在 chunk_xxx/ 子目录，需递归；md 引用为 images/xxx.jpg，
    故用 basename 匹配（与 exam_parser 的一级 glob + 全路径不同）。
    """
    eq_map: dict = {}
    image_set: set = set()
    for p in sorted(mineru_dir.rglob("*_content_list.json")):
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for item in data:
            img = item.get("img_path")
            if not img:
                continue
            name = Path(img).name
            t = item.get("type")
            if t == "equation" and item.get("text"):
                eq_map[name] = item["text"]
            elif t == "image":
                image_set.add(name)
    return eq_map, image_set


def _collect_images(mineru_dir: Path) -> dict:
    """收集 mineru 产物下所有图片（basename -> 绝对路径），重名取第一个。"""
    imgs: dict = {}
    for p in mineru_dir.rglob("*.jpg"):
        imgs.setdefault(p.name, p)
    for p in mineru_dir.rglob("*.png"):
        imgs.setdefault(p.name, p)
    return imgs


def _load_cache(path: Path) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _img_to_data_url(img_path: Path) -> str:
    suffix = img_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _parse_vl_result(out: str) -> str | None:
    """解析 VL JSON 输出：有意义的图返回描述，无关图返回 None。"""
    m = re.search(r"\{.*\}", out, re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = {}
    if data.get("relevant") is False:
        return None
    desc = str(data.get("description", "")).strip()
    return desc or None


# ─────────────────────────────────────────────────────────────
# 1. 扫描候选
# ─────────────────────────────────────────────────────────────
def scan_images(target: Path, mineru_dir: Path) -> dict:
    """扫描目标（目录=逐文件 / 文件=单文件）中的图片引用并分类。"""
    eq_map, image_set = _load_content_list_meta(mineru_dir)
    all_imgs = _collect_images(mineru_dir)

    files = sorted(target.rglob("*.md")) if target.is_dir() else [target]

    stats = {"files": len(files), "total_refs": 0, "equation": 0, "image": 0, "unmatched": 0}
    candidates = []

    for fp in files:
        lines = fp.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = IMG_RE.search(line)
            if not m:
                continue
            stats["total_refs"] += 1
            rel = m.group(2)
            name = Path(rel).name
            if name in eq_map:
                stats["equation"] += 1
            elif name in image_set and name in all_imgs:
                stats["image"] += 1
                ctx = "\n".join(lines[max(0, i - 3):i + 4])
                candidates.append({
                    "file": str(fp.relative_to(target)) if target.is_dir() else fp.name,
                    "line": i + 1,
                    "ref": rel,
                    "name": name,
                    "abs": str(all_imgs[name]),
                    "size": all_imgs[name].stat().st_size,
                    "ctx": ctx[:500],
                })
            else:
                stats["unmatched"] += 1

    return {"stats": stats, "candidates": candidates}


# ─────────────────────────────────────────────────────────────
# 2. VL 描述
# ─────────────────────────────────────────────────────────────
def _describe_one(item: dict, cache: dict, cache_path: Path) -> tuple[dict, str | None]:
    """单张图 VL 描述（带缓存，按 文件名:大小 命中）。"""
    key = f"{item['name']}:{item['size']}"
    if key in cache:
        return item, cache[key]

    img = Path(item["abs"])
    if not img.exists():
        return item, None
    print(f"    [VL] {item['name']} ...", flush=True)
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=VL_MODEL,
            messages=[
                {"role": "system", "content": VL_SYS_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": f"教材上下文（图片前后文本）：\n{item['ctx']}"},
                    {"type": "image_url", "image_url": {"url": _img_to_data_url(img)}},
                ]},
            ],
            max_tokens=512,
        )
        out = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"    ⚠ VL 失败 {item['name']}: {e}", flush=True)
        return item, None

    result = _parse_vl_result(out)
    cache[key] = result
    _save_cache(cache, cache_path)
    tag = "丢弃(无关)" if result is None else f"保留（{result[:30]}...）"
    print(f"    ✅ {item['name']} ({time.time()-t0:.1f}s) → {tag}", flush=True)
    return item, result


def run_vl(candidates: list[dict], out_dir: Path, max_workers: int, limit: int | None) -> dict:
    """分批并发 VL 描述，返回 {name:size: desc_or_None}。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "vl_cache.json"
    cache = _load_cache(cache_path)

    todo = candidates if limit is None else candidates[:limit]
    results: dict = {}

    if len(todo) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_describe_one, c, cache, cache_path): c for c in todo}
            for f in as_completed(futs):
                item, desc = f.result()
                results[item["name"]] = desc
    else:
        for c in todo:
            _, desc = _describe_one(c, cache, cache_path)
            results[c["name"]] = desc

    return results


# ─────────────────────────────────────────────────────────────
# 3. 写回
# ─────────────────────────────────────────────────────────────
def write_back(target: Path, eq_map: dict, cache: dict, out_dir: Path, mineru_dir: Path) -> dict:
    """按 VL 结果写回图说（直接改写 target 下 md 副本，不污染源 MinerU）。

    有意义图 → `![](x.jpg)（描述：...）`；无关图 → `<!-- 已忽略装饰/无关图：x.jpg -->`。
    """
    files = sorted(target.rglob("*.md")) if target.is_dir() else [target]
    write_stats = {"files": len(files), "described": 0, "ignored": 0, "kept": 0}

    for fp in files:
        lines = fp.read_text(encoding="utf-8").splitlines()
        out_lines = []
        for i, line in enumerate(lines):
            m = IMG_RE.search(line)
            if not m:
                out_lines.append(line)
                continue
            # 幂等：该行已带图说（（描述：…））或已忽略注释 → 原样保留，绝不重复追加。
            # （此前缺此守卫，重复跑 write/all 会把图说越拼越长。）
            if '（描述：' in line or '<!-- 已忽略' in line:
                out_lines.append(line)
                write_stats["kept"] += 1
                continue
            ref = m.group(2)
            name = Path(ref).name
            if name in eq_map:
                out_lines.append(line)          # 公式图已内联，原样保留
                write_stats["kept"] += 1
                continue
            key = f"{name}:{_size_of(mineru_dir, name)}"
            desc = cache.get(key)
            if desc:
                out_lines.append(line[:m.start()] + f"{m.group(0)}（描述：{desc}）" + line[m.end():])
                write_stats["described"] += 1
            elif key in cache:
                out_lines.append(line[:m.start()] + f"<!-- 已忽略装饰/无关图：{name} -->" + line[m.end():])
                write_stats["ignored"] += 1
            else:
                out_lines.append(line)          # 未处理（未在缓存中）→ 保持原样
                write_stats["kept"] += 1
        fp.write_text("\n".join(out_lines), encoding="utf-8")

    (out_dir / "write_report.json").write_text(
        json.dumps(write_stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return write_stats


def _size_of(mineru_dir: Path, name: str) -> int:
    """按 basename 找图片文件大小（缓存 key 需要）。"""
    try:
        for p in mineru_dir.rglob(name):
            return p.stat().st_size
    except Exception:
        pass
    return 0


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="v4 图片解析（教材版）")
    parser.add_argument("--mode", default="all", choices=["all", "scan", "vl", "write"],
                        help="scan=扫描候选 / vl=VL描述 / write=写回 / all=全流程")
    parser.add_argument("--target", default=None, help="目标 md 文件或目录（默认 test_os/split）")
    parser.add_argument("--mineru-dir", default=None, help="MinerU 产物目录（默认自动探测）")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认 test_os/image）")
    parser.add_argument("--max-workers", type=int, default=4, help="VL 并发数")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 张候选（测试用）")
    args = parser.parse_args()

    target = Path(args.target) if args.target else DEFAULT_TARGET
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    mineru_dir = Path(args.mineru_dir) if args.mineru_dir else MINERU_BOOK_DIRS[0]

    if not target.exists():
        print(f"❌ 目标不存在：{target}")
        return
    if not mineru_dir.exists():
        print(f"❌ MinerU 产物目录不存在：{mineru_dir}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"🎯 target: {target}\n📦 mineru: {mineru_dir}\n📤 out: {out_dir}")

    eq_map, _ = _load_content_list_meta(mineru_dir)

    # 1. 扫描（每次跑，更新候选）
    scan = scan_images(target, mineru_dir)
    (out_dir / "candidates.json").write_text(
        json.dumps(scan, ensure_ascii=False, indent=1), encoding="utf-8")
    st = scan["stats"]
    print(f"📊 扫描：{st['files']} 文件 | 引用 {st['total_refs']} "
          f"= 公式 {st['equation']} + 普通图 {st['image']} + 未匹配 {st['unmatched']}")

    if args.mode in ("all", "vl"):
        print(f"🖼 VL 描述（{len(scan['candidates'])} 张候选，并发 {args.max_workers}）...")
        run_vl(scan["candidates"], out_dir, args.max_workers, args.limit)

    if args.mode in ("all", "write"):
        cache_path = out_dir / "vl_cache.json"
        cache = _load_cache(cache_path)
        print("✍️ 写回图说...")
        ws = write_back(target, eq_map, cache, out_dir, mineru_dir)
        print(f"✅ 写回：描述 {ws['described']} | 忽略 {ws['ignored']} | 原样保留 {ws['kept']}")
        (out_dir / "report.json").write_text(
            json.dumps({"scan": st, "write": ws}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"📄 报告：{out_dir / 'report.json'}")


if __name__ == "__main__":
    main()
