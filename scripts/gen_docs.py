#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
docs 阶段（整章化流水线 Stage 3.5）——由物化产物 chapter_graph.json 驱动生成。

批式生成（方向 A）：
- 叶子按 seq 顺序分批（--batch-size N，默认 3），每批一次 LLM 调用，
  整章原文 / 规范 / 叶子清单只被读取一次 → 输入 token 大幅下降（费用随批大小近似
  从「N × 整章」降到「整章 × 1」）。
- LLM 对每份文档用 `<<<DOC_START seq="{seq}">>>` … `<<<DOC_END>>>` 显式包裹，
  按 seq 绑定叶子，杜绝顺序错位。
- 自愈：输出被截断（finish_reason=length）、调用失败、或块缺失时自动降级——
  批量拆半递归；最小粒度（单叶）失败才记为失败。可安全用于断点续跑/补篇。

用法：
  python scripts/gen_docs.py --cfg <配置json> [--only "1,8,21"] [--skip] [--batch-size 3]
                             [--max-workers 3] [--skip-existing] [--dry-run]
cfg 字段：
  graph / chapter_md / out_dir / subject_name / subject(可选术语体系) / subject_tag /
  spec(可选) / samples[](可选) / model(可选)
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
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

MODEL = os.environ.get("LLM_MODEL", "qwen3.7-plus")
MAX_OUTPUT_TOKENS = 8000
DEFAULT_SPEC = str(ROOT / "markdown-hack规范-v2.2.md")

DOC_START_RE = re.compile(r'<<<DOC_START\s*(.*?)>>>\s*\n?(.*?)\n?\s*<<<DOC_END>>>', re.S)


def read_text(p): return Path(p).read_text(encoding='utf-8')


def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = name.replace('：', '-').replace('，', ',')
    name = name.replace('（', '(').replace('）', ')')
    name = re.sub(r'[\\/*?:"<>|$`{}^]', '', name)
    name = name.replace(' ', '-')
    if len(name) > 60:
        name = name[:57] + '...'
    return name or 'untitled'


# ---------------- 图谱读取 ----------------

def load_graph(path: Path):
    g = json.loads(Path(path).read_text(encoding='utf-8'))
    by_id = {n['id']: n for n in g['nodes']}
    topics = [n for n in g['nodes'] if n['kind'] == 'topic']
    for i, n in enumerate(topics, 1):
        n['seq'] = i
    return g, by_id, topics


def build_leaf_context(g: dict, by_id: dict, node: dict) -> list[dict]:
    """由 rel_edges 构建：前置基础(先学)/直接应用(后学)/并列对比(关联) 候选。"""
    cands = []
    nid = node['id']
    for e in g.get('rel_edges', []):
        if e['rel'] == 'prerequisite_of':
            if e['to'] == nid:
                src = by_id.get(e['from'])
                if src:
                    cands.append({'rel': '前置基础', 'tag': '先学',
                                  'title': src['title'], 'reason': e.get('note', '')})
            elif e['from'] == nid:
                dst = by_id.get(e['to'])
                if dst:
                    cands.append({'rel': '直接应用', 'tag': '后学',
                                  'title': dst['title'], 'reason': e.get('note', '')})
        elif e['rel'] == 'parallel_to':
            other_id = e['to'] if e['from'] == nid else (e['from'] if e['to'] == nid else None)
            other = by_id.get(other_id) if other_id else None
            if other:
                cands.append({'rel': '并列对比', 'tag': '关联',
                              'title': other['title'], 'reason': e.get('note', '')})
    seen, out = set(), []
    for c in cands:
        if c['title'] not in seen:
            seen.add(c['title'])
            out.append(c)
    return out


def peer_lines(topics: list[dict]) -> str:
    lines = []
    for t in topics:
        lines.append(f"- **{t['title']}** — {t.get('summary', '')[:120]}")
    return '\n'.join(lines)


def cand_lines(cands: list[dict]) -> str:
    if not cands:
        return "(无候选：本文档可不写「相关知识点」区块，或依据正文自然提及的术语酌情省略)"
    lines = []
    for i, c in enumerate(cands, 1):
        lines.append(f"- {i}. [{c['tag']}·{c['rel']}] {c['title']}"
                     + (f"（{c['reason'][:90]}）" if c['reason'] else ''))
    return '\n'.join(lines)


# ---------------- prompt ----------------

SYSTEM_TPL = """你是一个专业的{subject}文档转换助手。用户会提供：
1. 一章教材的原始 Markdown 全文（章级上下文）
2. 从该章提取的知识单元树：**一批叶子**（学习单元），每片叶子带结构化摘要与相关知识点候选
3. 本章所有叶子清单（避免重复、用于互相引用）

你的任务：把**每一片**叶子各改写为一份**完整、自包含、可直接学习**的 Markdown 知识点文档。
叶子是本章一个中等粒度的学习单元（概念 / 存储结构 / 基本操作算法 / 机制 / 对比选型），
比单个概念略宽，一个叶子就是一份文档。本批共需一次输出 {batch_n} 份独立文档。

## 输出格式（必须严格遵守）
- 每份文档必须用显式标记包裹，格式（不要写入花括号本身）：
  `<<<DOC_START seq="{{该叶 seq 数字}}">>>`
  换行后是该文档完整 markdown（含 YAML frontmatter，以 `---` 开始），
  末尾换行输出 `<<<DOC_END>>>`。
- 共输出 {batch_n} 组 DOC_START/DOC_END，与输入叶子一一对应（以 seq 为准，顺序可任意）。
- 整批输出很长：请让每篇精炼而完整（正文建议 800~1600 汉字），保证 {batch_n} 篇**全部输出完整**，
  宁可单篇略短也不要中途停笔。不要在输出的首尾加 ``` 代码围栏。

## 文档风格（每篇都按此执行，参照交付样例）
- YAML frontmatter 必须包含：title（=输入数据的原始 title，一字不改）、type=topic、aliases（该知识点的别称/英文名，如无则 []）、tags（知识领域标签 2~4 个，如"数据结构""线性表""顺序表"）、description（一句话）、terms（文档内讲解到的术语列表，数字 id 从 1 开始，每项含 name/aliases(可选)/definition）
- 一级标题 # 标题 后接正文；正文按 ## 小节组织
- 正文用 `<!-- @anchor-begin id="英文-小写-连字符" terms="1|2" note="..." -->` … `<!-- @anchor-end -->` 包裹关键定义/机制/结论段落，术语 id 对应 frontmatter 的 terms
- 算法/操作流程用清晰的编号步骤或 C 伪代码（```c 块）展开；公式用 LaTeX（行内 $...$、行间 $$...$$）
- 每篇文档末尾放 `## 相关知识点` 区块

## topic 写作基调
- 每篇开头第一句直接给结论；正文自包含，禁止"如上所述/前文提到/见教材"等依赖外部上下文的表述
- 讲清楚：正式定义/结构/公式、直观理解或类比、具体操作步骤或算法要点、复杂度或关键性质、易错点/易混淆处
- 叶子若偏算法/操作（标题含 操作/算法/方法/创建/实现/合并/查找 等），正文要落到"怎么做"，给出带编号的步骤或可运行伪代码，而不是只讲概念
- 叶子若偏概念/结构（定义/特点/类型 等），重点讲清定义要素、结构图景、区分概念（如头指针 vs 头结点）

## 相关知识点
- 每篇只能从输入中**该叶子自己的候选列表**里选择（没有候选就不写），3~6 条
- 每条用 `[[标题]]{{type=topic, label=标题, render=link}}`，标题必须一字不改
- 每条链接后补一句"为什么相关"的关系说明（前置基础→本文档用到它的什么；直接应用→学完本文后可继续看它的什么；并列对比→在哪个维度上与它对照），1 句话即可，不要写"详见 XX 文档"式的空话；关系说明可参考候选列表给出的原因

## 硬约束
- 【事实边界】术语定义、公式、数值、结论等可证事实以整章原文为准：原文明确给出的必须沿袭，
  不得歪曲或错移出处；原文没有的可证具体事实（精确公式、数值、人名年份、实验数据、文献引用、
  API 行为）一律不得虚构
- 【合理扩展】为了让文档"自包含、可直接学习"，教材未展开、但读懂本知识点必需的背景可适度补齐，
  例如：出现而未解释的前置术语的一句话解释、记号/符号含义、概念直觉或类比、典型应用场景、
  教材点到为止的相关结论的常识性展开。扩展须遵守：① 不与原文说法冲突；②   拿不准的用
  "通常/一般地/可理解为"等降级措辞；③ 不虚构可证具体事实；④ 篇幅克制，一两句或一个小节内收住，
  不喧宾夺主；⑤ 若想展开的主题恰是本章其它叶子（对照叶子清单）的主内容，只作半句引介并
  [[链接]] 到对应叶子，不重复展开成段
- C 伪代码必须忠实于教材算法语义，不得自行"发明"实现细节
- 规范中 `type=concept` + `render=inline` 的行内术语引用为预留扩展，当前不使用；正文遇术语直接加粗即可
- terms frontmatter 与 @anchor terms= 照常填写
- 【图片保留】整章原文中出现过、且与本叶论述直接相关的插图引用行（形如
  `![](images/xxx.jpg)`）必须保留到正文相应位置：结构图/示意图/对比曲线/流程图等能帮助
  理解就带，引用路径一字不改；图片行内若带（描述：…）辅助文字，它只是识图生成的说明，
  输出时可精简或略去；图题（如"图4.2 多层前馈神经网络"）独立成行时须一并保留在图片附近；
  与本叶无关的图不要携带；禁止凭空新增不存在的图片路径
- 每篇之间互不依赖、互不重复；不同叶子共享的背景（如定义一章的公共记号）可各自简要交代
- 结束前检查：本批 {batch_n} 份文档都完整写出、标记闭合、没有遗漏"""


def build_batch_user_prompt(chapter: str, batch: list[dict], all_topics: list[dict],
                            g: dict, by_id: dict, chapter_md: str, spec: str,
                            samples: list[tuple], batch_no: int, n_batches: int,
                            global_total: int) -> str:
    ex_parts = []
    for name, content in samples:
        ex_parts.append(f"### 示例文档：{name}\n\n```markdown\n{content}\n```")
    examples = '\n\n'.join(ex_parts)

    segs = []
    first, last = batch[0]['seq'], batch[-1]['seq']
    for node in batch:
        leaf_json = json.dumps({
            'title': node['title'], 'path': node['path'],
            'summary': node.get('summary', ''),
        }, ensure_ascii=False, indent=2)
        cands = build_leaf_context(g, by_id, node)
        segs.append(
            f"### 叶子 {node['seq']}：{node['title']}\n\n"
            f"该叶子对应一份待生成文档，title 必须一字不改。结构化数据：\n"
            f"```json\n{leaf_json}\n```\n\n"
            f"该叶子的相关知识点候选（只对它自己的文档有效，3~6 条；不足则少写或不写）：\n"
            f"{cand_lines(cands)}"
        )
    leaf_block = '\n\n---\n\n'.join(segs)

    return (
        f"# 任务：将《{chapter}》中的 {len(batch)} 个学习单元（叶子）各转换为独立的 "
        f"Markdown 知识点文档\n\n"
        f"（本批叶子 seq {first}~{last}，共 {len(batch)} 片；全书章内共 {global_total} 片，"
        f"本批为第 {batch_no}/{n_batches} 批）\n\n"
        f"---\n\n"
        f"## 待生成的叶子（每片独立成文）\n\n{leaf_block}\n\n"
        f"---\n\n"
        f"## 本章叶子清单（用于避免与其他叶子的文档内容重复；正文如需指向其他叶子请用 "
        f"`[[标题]]{{type=topic, label=标题, render=link}}`，标题一字不改）\n\n"
        f"{peer_lines(all_topics)}\n\n"
        f"---\n\n"
        f"## 参考规范（markdown-hack规范）\n\n```\n{spec}\n```\n\n"
        f"---\n\n"
        f"## 参考示例文档\n\n{examples}\n\n"
        f"---\n\n"
        f"## 整章原文（完整教材内容，所有叶子的正文事实来源）\n\n"
        f"```markdown\n{chapter_md}\n```\n\n"
        f"请严格按照规范为上述 {len(batch)} 个叶子各生成一份完整 Markdown 文档，"
        f"每份用 `<<<DOC_START seq=\"<seq>\">>>` … `<<<DOC_END>>>` 包裹后依次输出。"
    )


# ---------------- LLM ----------------

def call_llm(system: str, user: str, tracker: str, desc: str):
    """返回 (content, truncated)；调用彻底失败返回 (None, False)。"""
    client = OpenAI(api_key=os.environ.get('DASHSCOPE_API_KEY', ''),
                    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    for attempt in range(1, 4):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{'role': 'system', 'content': system},
                          {'role': 'user', 'content': user}],
                temperature=0.1, max_tokens=MAX_OUTPUT_TOKENS)
            content = resp.choices[0].message.content.strip()
            content = re.sub(r'^```(?:markdown)?\s*\n', '', content)
            content = re.sub(r'\n```\s*$', '', content)
            truncated = getattr(resp.choices[0], 'finish_reason', '') == 'length'
            get_tracker(tracker).add_from_usage(model=MODEL,
                                                usage=extract_usage(resp),
                                                description=desc)
            return content, truncated
        except Exception as e:
            print(f'⚠️ 第{attempt}次失败: {type(e).__name__}: {e}')
            if attempt < 3:
                time.sleep(10 * attempt)
    return None, False


def inject_frontmatter(md: str, node: dict, source: str) -> str:
    """强制 title=叶子 title、id=图谱 uuid、type=topic，清理多余字段，追加 source。"""
    canonical = node['title']
    new_id = node['id']  # 图谱权威 uuid，不重新生成
    fm = re.search(r'^---\s*\n(.*?)\n---', md, re.DOTALL)
    if fm:
        body = fm.group(1)
        if re.search(r'^title:', body, re.M):
            body = re.sub(r'^title:\s*.+$', f'title: {canonical}', body,
                          count=1, flags=re.M)
        else:
            body = f'title: {canonical}\n' + body
        if re.search(r'^id:', body, re.M):
            body = re.sub(r'^id:\s*.+$', f'id: {new_id}', body, count=1, flags=re.M)
        elif re.search(r'^title:.*\n', body):
            body = re.sub(r'(^title:.*\n)', rf'\1id: {new_id}\n', body, count=1)
        if re.search(r'^type:', body, re.M):
            body = re.sub(r'^type:\s*.+$', 'type: topic', body, count=1, flags=re.M)
        else:
            body = body.rstrip() + '\ntype: topic'
        for fld in ('numeric_source', 'idx', 'node_type', 'parent_concept'):
            body = re.sub(rf'^{fld}:\s*.+\n?', '', body, flags=re.M)
        if not re.search(r'^source:', body, re.M):
            body = body.rstrip() + f'\nsource: {source}'
        return '---\n' + body + '\n---' + md[fm.end():]
    return (f'---\ntitle: {canonical}\nid: {new_id}\ntype: topic\n'
            f'source: {source}\naliases: []\n---\n\n{md}')


def parse_blocks(text: str) -> dict:
    """解析 DOC_START/DOC_END 包裹的多文档输出 → {seq: body}。无标记返回 {}。"""
    out = {}
    for m in DOC_START_RE.finditer(text):
        attrs, body = m.group(1), m.group(2).strip()
        sm = re.search(r'seq="?(\d+)"?', attrs)
        if sm:
            out[int(sm.group(1))] = body
    return out


def save_doc(cfg: dict, node: dict, body: str) -> tuple:
    title = node['title']
    seq = node['seq']
    out_dir = Path(cfg['out_dir'])
    out_dir.mkdir(parents=True, exist_ok=True)
    md_text = inject_frontmatter(body, node, f"{cfg['chapter']}/{title}.md")
    filename = f"{seq:02d}-{sanitize_filename(title)}.md"
    fp = out_dir / filename
    fp.write_text(md_text, encoding='utf-8')
    print(f"    ✅ {len(md_text)} 字符 → {filename}", flush=True)
    return {'seq': seq, 'id': node['id'], 'title': title, 'filepath': str(fp),
            'success': True}


def fail_result(node: dict, error: str) -> dict:
    return {'seq': node['seq'], 'id': node['id'], 'title': node['title'],
            'filepath': None, 'success': False, 'error': error}


def robust_batch(cfg: dict, batch: list[dict], ctx: dict,
                 batch_no: int, n_batches: int) -> list[dict]:
    """递归鲁棒批处理：整批失败→拆半；截断/缺块→对缺失叶子逐叶单跑兜底。"""
    if not batch:
        return []
    by_id, g = ctx['by_id'], ctx['g']
    seqs = [n['seq'] for n in batch]
    span = f"{seqs[0]}-{seqs[-1]}" if len(batch) > 1 else str(seqs[0])
    print(f"\n  ▶ 批[{span}] {len(batch)} 叶 × 1 次调用", flush=True)
    user = build_batch_user_prompt(cfg['chapter'], batch, ctx['topics'], g, by_id,
                                   ctx['chapter_md'], ctx['spec'], ctx['samples'],
                                   batch_no, n_batches, ctx['global_total'])
    out_dir = Path(cfg['out_dir'])
    out_dir.mkdir(parents=True, exist_ok=True)

    system = ctx['system'].replace('{batch_n}', str(len(batch)))
    if len(batch) == 1:
        node = batch[0]
        content, truncated = call_llm(system, user, cfg['tracker'],
                                      f"docs: {node['title']}")
        if content is None:
            return [fail_result(node, 'LLM call failed')]
        blocks = parse_blocks(content)
        body = blocks.get(node['seq']) or (content if not blocks else None)
        if body is None or truncated:
            return [fail_result(node, 'truncated output' if truncated
                                else 'no doc block')]
        return [save_doc(cfg, node, body)]

    content, truncated = call_llm(system, user, cfg['tracker'],
                                  f"docs: batch {span}")
    if content is None:
        mid = len(batch) // 2
        print(f"    ↳ 整批调用失败，拆半重试 {len(batch)}→{mid}+{len(batch)-mid}")
        return (robust_batch(cfg, batch[:mid], ctx, batch_no, n_batches)
                + robust_batch(cfg, batch[mid:], ctx, batch_no, n_batches))

    blocks = parse_blocks(content)
    saved_nodes, results = set(), []
    for node in batch:
        body = blocks.get(node['seq'])
        if body:
            try:
                results.append(save_doc(cfg, node, body))
                saved_nodes.add(node['seq'])
            except Exception as e:
                print(f"    ❌ 保存异常 {node['title']}: {e}")
                results.append(fail_result(node, f'save error: {e}'))
        else:
            results.append(None)
    missing = [n for n in batch if n['seq'] not in saved_nodes]
    for m in missing:
        if truncated and len(batch) > 1:
            print(f"    ↳ 输出截断，缺失叶子 seq={m['seq']} 单跑兜底")
            results.extend(robust_batch(cfg, [m], ctx, batch_no, n_batches))
        elif len(batch) > 1:
            print(f"    ↳ 缺块 seq={m['seq']}，单跑兜底")
            results.extend(robust_batch(cfg, [m], ctx, batch_no, n_batches))
        else:
            results.append(fail_result(m, 'no doc block'))
    return [r for r in results if r is not None]


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cfg', required=True)
    ap.add_argument('--only', default='', help='只处理指定叶子 seq，逗号分隔')
    ap.add_argument('--skip', default='', help='跳过指定叶子 seq，逗号分隔')
    ap.add_argument('--batch-size', type=int, default=3,
                    help='一次 LLM 调用生成的文档数（默认 3）')
    ap.add_argument('--max-workers', type=int, default=3, help='并发批数')
    ap.add_argument('--skip-existing', action='store_true',
                    help='跳过输出目录中已存在的叶子（按序号前缀判断）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    cfg = json.loads(Path(args.cfg).read_text(encoding='utf-8'))
    cfg.setdefault('tracker', 'gen_docs')
    cfg.setdefault('dry_run', args.dry_run)
    if cfg.get('model'):
        global MODEL
        MODEL = cfg['model']
    if not cfg.get('graph') or not cfg.get('chapter_md'):
        raise SystemExit('cfg 需包含 graph / chapter_md')

    g, by_id, topics = load_graph(Path(cfg['graph']))
    cfg.setdefault('chapter', g['chapter'])

    chapter_md = read_text(cfg['chapter_md'])
    spec = read_text(cfg.get('spec', DEFAULT_SPEC))
    # 规范第7节要求图片放在同级 images/ 且禁 ../——本链路图片沿用整章原文的相对路径，
    # 不要求 LLM 挪目录。把该节替换成"原样保留原文引用行"，避免提示词内部矛盾。
    spec = re.sub(
        r'## 7\.\s*图像资源规范.*?$',
        '## 7. 图像资源规范\n\n'
        '插图引用沿用整章原文已有的 markdown 图片行（`![…](相对路径)`），与本知识点论述'
        '直接相关的插图（结构图/示意图/对比曲线/流程图等）必须保留：引用路径一字不改，'
        '图题（如"图4.2 多层前馈神经网络"）保留在图片附近；图片行内若带（描述：…）'
        '辅助说明可保留一句也可略去；禁止新增原文不存在的图片路径。',
        spec, flags=re.S)
    samples = []
    for p in cfg.get('samples', []):
        sp = Path(p)
        if sp.exists():
            samples.append((sp.stem, sp.read_text(encoding='utf-8')))
    subject = cfg.get('subject', '')
    system = SYSTEM_TPL.format(subject=cfg['subject_name'], batch_n='{batch_n}')

    if args.only:
        ids = {int(x) for x in args.only.split(',') if x.strip()}
        topics = [t for t in topics if t['seq'] in ids]
    if args.skip:
        skip = {int(x) for x in args.skip.split(',') if x.strip()}
        topics = [t for t in topics if t['seq'] not in skip]
    if args.skip_existing:
        out_dir = Path(cfg['out_dir'])
        topics = [t for t in topics
                  if not list(out_dir.glob(f"{t['seq']:02d}-*.md"))]

    n = args.batch_size
    batches = [topics[i:i + n] for i in range(0, len(topics), n)]
    ctx = {
        'g': g, 'by_id': by_id, 'topics': topics, 'chapter_md': chapter_md,
        'spec': spec, 'samples': samples, 'system': system,
        'global_total': len(topics),
    }

    total_all = len(g['nodes'])
    print(f"📊 docs 任务：{len(topics)} 个 topic（图谱节点 {total_all}）→ "
          f"{len(batches)} 批 × {n} 叶/批 · 并发 {args.max_workers} 批 · 模型 {MODEL}")
    print(f"  整章原文 {len(chapter_md)} 字符 · 规范 {len(spec)} 字符 · "
          f"示例 {len(samples)} 个")
    os.makedirs(cfg['out_dir'], exist_ok=True)

    results = []
    if cfg.get('dry_run'):
        for i, b in enumerate(batches, 1):
            user = build_batch_user_prompt(cfg['chapter'], b, topics, g, by_id,
                                           chapter_md, spec, samples, i,
                                           len(batches), len(topics))
            span = f"{b[0]['seq']}-{b[-1]['seq']}"
            (Path(cfg['out_dir']) / f"_debug_prompt_{span}.txt").write_text(
                '=== SYSTEM ===\n' + system.replace('{batch_n}', str(len(b))) +
                '\n\n=== USER ===\n' + user, encoding='utf-8')
        print(f"  💾 {len(batches)} 个批 prompt → _debug_prompt_*.txt")
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futs = {ex.submit(robust_batch, cfg, b, ctx, i, len(batches)): b
                    for i, b in enumerate(batches, 1)}
            for fut in as_completed(futs):
                try:
                    results.extend(fut.result())
                except Exception as e:
                    b = futs[fut]
                    print(f"  ❌ 批异常 {[n['seq'] for n in b]}: {e}")

    results.sort(key=lambda r: r['seq'])
    ok = sum(1 for r in results if r.get('success'))
    fails = [r for r in results if not r.get('success')]
    print(f"\n{'=' * 50}\n✅ 完成 {ok}/{len(results)}，输出目录：{cfg['out_dir']}")
    if fails:
        print('   ⚠️ 未完成：' + '、'.join(f"{r['title']}({r.get('error')})"
                                            for r in fails[:10]))
    get_tracker(cfg['tracker']).print_summary()


if __name__ == '__main__':
    main()
