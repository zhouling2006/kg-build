#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S3 — 章节画像 pass（build_chapter_profile）
为每个小节做「处理预设」：判定它在全书中扮演的角色（role）、该用多大力气处理（depth）、
大致涉及哪些内容（scope），输出 chapter_profile.json，注入下游逐节提取/生成阶段作为处理指引。
考试大纲是判断考点价值的参考信号（如有；无大纲时按学科体系判断），不是覆盖清单。

设计（对应纪要 S3 + 用户确认）：
- 规则匹配为快速路径（零成本）：章标题↔大纲一级条目（关键词）、节标题↔考点条目（bigram+子串）——承担考纲↔教材节的对应
- LLM 画像为主路径：按章批量调用，LLM 基于全书结构判断章节角色 → 节角色 → 处理力度 → 覆盖范围
- 合并策略：LLM 输出为准，规则强命中条目若 LLM 漏了则补进 scope（避免遗漏）

用法：
  python scripts/build_chapter_profile.py --subject os
  python scripts/build_chapter_profile.py --syllabus 大纲/操作系统.md --structure 操作系统/chapter_structure.json --output intermediate/os/chapter_profile.json [--dry-run]
"""

import argparse
import datetime
import io
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
for _p in (_SCRIPTS, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from billing import get_tracker, extract_usage

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

MODEL = "qwen3.7-plus"
SIM_THRESHOLD = 0.15

# 章标题 → 大纲一级条目 的启发式关键词（命中数最多者胜；用于裁剪 LLM 的考纲上下文）
TOPIC_HINTS = {
    "操作系统概述": ["概述", "引论", "结构", "引导", "系统调用", "虚拟机", "运行环境", "发展", "接口"],
    "进程管理": ["进程", "线程", "调度", "同步", "互斥", "死锁", "通信", "上下文", "处理机"],
    "内存管理": ["存储器", "内存", "存储", "分页", "分段", "虚拟", "对换", "装入"],
    "文件管理": ["文件", "目录", "索引节点", "文件系统"],
    "I/O管理": ["输入输出", "I/O", "设备", "缓冲", "中断", "磁盘", "外存"],
}

LLM_PROFILE_SYSTEM = """你是教材结构分析师。任务：为每个小节做「处理预设」——确定它在全书中扮演的角色、该用多大力气处理、大致涉及哪些内容。这份预设将注入逐节提取阶段，指导提取详略。如有考试大纲，它是判断考点价值的参考信号，不是覆盖清单；无大纲时按学科体系与常识判断。

工作逻辑（按此顺序推理）：
1. 通读全书章节结构，为每章定角色：导览/引言章、基础章、核心章、过渡章、延伸/收尾章。角色由标题、内容组织、先后顺序推出，不预设任何章的固定档位。
2. 在章角色约束下，为每个小节定角色：铺垫（为后续章节建立认知）、核心（本章主线考点）、延伸（边缘补充）。
3. 结合考试大纲覆盖密度（若有）与学科重点、考频，定处理力度档位。
4. 定该节大致涉及的内容范围。

## 处理力度档位

一档对应一个明确的提取动作：

- deep（精讲）：全书骨架考点，后续大量依赖，考试常直接命题。
  典型：死锁与银行家算法、页式虚拟存储、进程同步与 PV 操作。
  动作：完整提取——定义、机制/原理、典型解法、易错点。
- standard（常规）：重要但较独立，或偏应用技巧。
  典型：磁盘调度算法、文件的物理结构。
  动作：提取定义、关键机制、1 个典型例子。
- brief（概览）：背景、历史、导引性内容，细节由后续章节展开。
  典型：操作系统发展历程、体系结构综述。
  动作：仅提取定位性要点（是什么、起什么作用），并入相邻节点。

## 章节角色 → 力度约束

- 导览/引言章、延伸/收尾章：不设 deep，以 brief 为主。
- 基础章、核心章：可设 deep，每章 deep 不超过该章节数的 1/3。
- 每章至少 1 节 standard 或 deep。

## 输出字段

每章：
- brief：本章在全书中的角色与核心考点（1~2 句，如"进程管理是全书主线，覆盖进程/线程模型、处理机调度、同步互斥与死锁四大块"）。
- chapter_depth：本章整体力度档位。

每节：
- role：该节在全书中的角色（一句话，如"全书导览章，为进程管理建立认知框架"）。
- requirement：该节处理时的覆盖指导，100~150 字：涉及的主题、重点、考试常见考法、易错点。大纲要求（如有）而该书该节未涉及的内容，直接写入覆盖范围（并入"涉及的主题/重点"），不要出现"待补"字样。
- scope：该节大致涉及的主题词，2~5 个，用学科通行说法（如"银行家算法""进程调度时机"）。
- depth：力度档位（见上）。
- depth_reason：一句话写明档位依据（如"死锁是考试必考大题，跨章桥接资源分配图"）。

## 输出格式

输出必须覆盖输入的全部 chapter_no 与 section_id，一章一节不省略。只输出纯 JSON 数组，无多余文字、无 Markdown 包裹：

[{"chapter_no": "第三章", "brief": "处理机调度与死锁是考试常考板块……", "chapter_depth": "deep",
  "sections": [
    {"section_id": "3.7", "role": "死锁处理是全章核心考点",
     "requirement": "本节应覆盖死锁的定义与产生必要条件（互斥、占有并等待、不可剥夺、循环等待）；处理策略——预防、避免（银行家算法，重点：安全状态判定与分配算法步骤）、检测与解除（资源分配图化简）。考试常以综合题考查银行家算法执行与安全序列判定。",
     "scope": ["死锁定义", "四个必要条件", "银行家算法", "安全序列", "资源分配图"],
     "depth": "deep", "depth_reason": "死锁是考试必考大题，跨章桥接资源分配图"}
  ]}]"""


# ═══════════════════════════════════════════════════════════
# 大纲解析
# ═══════════════════════════════════════════════════════════

def parse_syllabus(md_text: str) -> list:
    """解析大纲 markdown → 一级条目树。

    返回 [{"name": "### （一）操作系统概述",
           "points": [...无二级时直接挂的考点条目...],
           "children": [{"name": "**1. 操作系统的基本概念**", "points": [...]}]}]
    """
    tree = []
    current_top = None
    current_sub = None
    for raw in md_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^#{1,6}\s*（[一二三四五六七八九十]+）\s*(.+)$', line)
        if m:
            current_top = {"name": line, "points": [], "children": []}
            tree.append(current_top)
            current_sub = None
            continue
        if current_top is None:
            continue
        m = re.match(r'^\*\*([0-9]+\.?\s*.+?)\*\*\s*$', line)
        if m:
            current_sub = {"name": m.group(1).strip(), "points": []}
            current_top["children"].append(current_sub)
            continue
        m = re.match(r'^[-•*]\s*(.+)$', line)
        if m:
            item = m.group(1).strip()
            if current_sub is not None:
                current_sub["points"].append(item)
            else:
                current_top["points"].append(item)
            continue
        if line.startswith(('>', '|', '---', '```', '#')):
            continue
        if current_sub is not None and len(line) > 3:
            current_sub["points"].append(line)
    return tree


def topic_name(top: dict) -> str:
    """一级条目名 → 主题词，如"### （一）操作系统概述"→"操作系统概述" """
    name = top["name"].lstrip('#').strip()
    m = re.match(r'^（[一二三四五六七八九十]+）\s*(.+)$', name)
    return m.group(1).strip() if m else name


def top_context_text(top: dict | None) -> str:
    """把一级条目格式化成给 LLM 的考纲上下文文本。"""
    if top is None:
        return "（该章无显式考纲覆盖，请按学科常识为各节补充考点条目，source 统一为 llm_supplement）"
    lines = [top["name"]]
    for sub in top.get("children", []):
        lines.append(f"  {sub['name']}")
        for p in sub.get("points", []):
            lines.append(f"    - {p}")
    for p in top.get("points", []):
        lines.append(f"  - {p}")
    return "\n".join(lines)


def hints_for_topic(top: dict) -> list:
    """返回一级条目对应的启发式关键词（含 key 名称/关键词两轮匹配，兼容"输入/输出（I/O）管理"这类变形）。"""
    name = topic_name(top)
    for key, kws in TOPIC_HINTS.items():
        if key in name or name in key:
            return kws
    for key, kws in TOPIC_HINTS.items():
        if any(kw in name for kw in kws):
            return kws
    return []


# 规则难以判定的章 → 强制指定 LLM 考纲上下文（值为一级条目主题名，可多个）
CHAPTER_OVERRIDE = {
    "第八章": ["输入/输出（I/O）管理", "文件管理"],   # 磁盘存储器的管理：磁盘调度(I/O) + 外存/文件存储空间(文件)
}


def align_chapter_to_topic(chapter_no: str, chapter_title: str, tree: list) -> list:
    """章标题 → 匹配的一级条目列表（按关键词命中数；override 优先）。无命中返回 []。"""
    if chapter_no in CHAPTER_OVERRIDE:
        return [t for t in tree if topic_name(t) in CHAPTER_OVERRIDE[chapter_no]]
    best, best_score = [], 0
    for top in tree:
        score = sum(1 for kw in hints_for_topic(top) if kw in chapter_title)
        if score > best_score:
            best, best_score = [top], score
        elif score == best_score and score > 0:
            best.append(top)
    return best


# ═══════════════════════════════════════════════════════════
# 规则匹配（快速路径）
# ═══════════════════════════════════════════════════════════

SYNONYMS = [
    ("分页存储管理", "页式管理"), ("分段存储管理", "段式管理"),
    ("存储管理方式", "管理方式"), ("存储管理", "管理"),
    ("作业调度", "调度"), ("进程调度", "调度"), ("处理机调度", "调度"),
    ("虚拟存储器", "虚拟内存"), ("虚拟存储", "虚拟内存"),
    ("输入输出系统", "I/O系统"), ("输入输出", "I/O"),
    ("设备管理", "设备"), ("缓冲管理", "缓冲"), ("缓冲区管理", "缓冲"),
]


def normalize_cn(s: str) -> str:
    """清洗 + 同义词归一：去编号/空白/标点/括号注释，做教材↔大纲用词统一。"""
    s = re.sub(r'（[^）]*）', '', s)
    s = re.sub(r'\([^)]*\)', '', s)
    s = re.sub(r'[\s\d\.\-\（）()【】\[\]“”"\':：]', '', s)
    s = re.sub(r'^[0-9]+', '', s)
    for a, b in SYNONYMS:
        s = s.replace(a, b)
    return s


def bigrams(s: str) -> set:
    s = normalize_cn(s)
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def sim(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def match_score(core: str, candidate: str) -> float:
    """节标题 ↔ 考点条目 的匹配分：子串包含直接 1.0，否则 bigram Jaccard。"""
    a, b = normalize_cn(core), normalize_cn(candidate)
    if not a or not b:
        return 0.0
    if len(a) >= 2 and (a in b or b in a):
        return 1.0
    return sim(a, b)


def strip_leading_number(name: str) -> str:
    return re.sub(r'^[0-9]+\.\s*', '', name).strip()


_CN_NUM = "零一二三四五六七八九"


def _num_to_cn(n: int) -> str:
    """阿拉伯数字 → 中文数词（1..99）。"""
    if n <= 9:
        return _CN_NUM[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CN_NUM[n - 10]
    return _CN_NUM[n // 10] + "十" + (_CN_NUM[n % 10] if n % 10 else "")


def norm_chapter_ref(ref: str) -> str:
    """章引用归一化 → '第一章' 形式。支持'第一章'、'第一章 xxx'、'1'、'第1章'。
    数字形式用于绕开 PowerShell 传中文参数的 GBK 乱码。"""
    s = ref.strip()
    m = re.match(r'^第([一二三四五六七八九十]+)章', s)
    if m:
        return f"第{m.group(1)}章"
    m = re.match(r'^第?(\d+)\s*章?', s)
    if m:
        return f"第{_num_to_cn(int(m.group(1)))}章"
    return s


def _parent(top: dict, sub: dict | None = None) -> str:
    a = top["name"].lstrip('#').strip()
    return f"{a} / {sub['name'].strip()}" if sub else a


def _rule_match_one(section_title: str, top: dict) -> list:
    """单个一级条目下的规则匹配。返回 [{title,parent,source,_score}]"""
    core = section_title
    m = re.match(r'^[\d\.]+\s*(.+)$', section_title)
    if m:
        core = m.group(1)

    hits, matched_sub = [], None
    best_sub, best_score = None, 0.0
    for sub in top.get("children", []):
        score = match_score(core, strip_leading_number(sub["name"]))
        if score > best_score:
            best_sub, best_score = sub, score
    if best_sub is not None and best_score >= SIM_THRESHOLD:
        matched_sub = best_sub
        sub_name = strip_leading_number(best_sub["name"])
        parent = _parent(top, best_sub)
        hits.append({"title": sub_name, "parent": parent, "source": "syllabus",
                     "_score": best_score})
        for p in best_sub.get("points", []):
            sc = match_score(core, p)
            if sc >= SIM_THRESHOLD:
                hits.append({"title": p, "parent": parent, "source": "syllabus",
                             "_score": sc})

    if matched_sub is None:
        for sub in top.get("children", []):
            for p in sub.get("points", []):
                sc = match_score(core, p)
                if sc >= SIM_THRESHOLD:
                    hits.append({"title": p, "parent": _parent(top, sub),
                                 "source": "syllabus", "_score": sc})
        for p in top.get("points", []):
            sc = match_score(core, p)
            if sc >= SIM_THRESHOLD:
                hits.append({"title": p, "parent": _parent(top),
                             "source": "syllabus", "_score": sc})
    return hits


def rule_match_points(section_title: str, tops: list) -> list:
    """规则快速路径：在章对齐的（多个）一级条目下匹配考点条目，去重（保留最高分）。"""
    hits = []
    for top in tops:
        hits.extend(_rule_match_one(section_title, top))
    best = {}
    for h in hits:
        if h["title"] not in best or h["_score"] > best[h["title"]]["_score"]:
            best[h["title"]] = h
    return list(best.values())


# ═══════════════════════════════════════════════════════════
# LLM 画像（主路径，按章批量）
# ═══════════════════════════════════════════════════════════

def _llm_call(user_prompt: str, desc: str) -> list:
    """调用 LLM 并解析纯 JSON 数组返回；失败返回 []。"""
    from openai import OpenAI
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("  ⚠️ 未设置 DASHSCOPE_API_KEY，跳过 LLM 画像（仅保留规则匹配结果）")
        return []
    client = OpenAI(api_key=api_key,
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": LLM_PROFILE_SYSTEM},
                      {"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=16384,
        )
        usage = extract_usage(resp)
        get_tracker("build_chapter_profile").add_from_usage(
            model=MODEL, usage=usage, description=desc)
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            print(f"  ⚠️ {desc} LLM 返回非数组，忽略")
            return []
        return parsed
    except Exception as e:
        print(f"  ⚠️ {desc} LLM 画像失败：{e}")
        return []


def _flatten_llm_result(parsed: list) -> tuple:
    """解析 LLM 返回的章节数组 → ({chapter_no: chapter_meta}, {section_id: sec_meta})
    chapter_meta = {brief, chapter_depth}
    sec_meta     = {brief, requirement, role, scope, depth, depth_reason}
    兼容新格式 [{chapter_no, brief, chapter_depth, sections:[{section_id, requirement, scope, depth, ...}]}]
    与旧格式 [{section_id, points:[{title,...}]}]（points/must_cover 兜底进 scope）。"""
    chapter_meta, sec_meta = {}, {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        no = item.get("chapter_no")
        if no:
            cm = {}
            if item.get("brief"):
                cm["brief"] = str(item["brief"]).strip()
            if item.get("chapter_depth"):
                cm["chapter_depth"] = str(item["chapter_depth"]).strip()
            if cm:
                chapter_meta[no] = cm
            sections = item.get("sections", [])
        else:
            sections = [item]  # 旧格式：直接是 {section_id, ...}
        for s in sections:
            sid = s.get("section_id")
            if not sid:
                continue
            sm = {}
            for k in ("brief", "requirement", "role", "depth", "depth_reason"):
                if s.get(k):
                    sm[k] = str(s[k]).strip()
            # scope 支持新格式（关键词数组）与旧格式（must_cover/points 数组）兜底
            mc = s.get("scope") or s.get("must_cover") or s.get("points")
            if isinstance(mc, list):
                titles = []
                for p in mc:
                    if isinstance(p, str):
                        titles.append(p.strip())
                    elif isinstance(p, dict) and p.get("title"):
                        titles.append(str(p["title"]).strip())
                if titles:
                    sm["scope"] = titles[:5]  # 宁缺毋滥：最多 5 个
            if sm:
                sec_meta[sid] = sm
    return chapter_meta, sec_meta


def _structure_overview(level1: list, sections: list) -> str:
    """把全书章节结构压成一段文本，喂给 LLM 作为全局视野（章节角色判定依据）。"""
    lines = ["## 全书章节结构（按教材顺序）"]
    for ch in level1:
        ch_secs = [s for s in sections if s.get("父级编号") == ch["编号"]]
        titles = " / ".join(s.get("标题", "") for s in ch_secs)
        lines.append(f"- {ch['编号']} {ch['标题']}（{len(ch_secs)} 节：{titles}）")
    return "\n".join(lines)


def llm_profile_all(chapters, entries, ctx_by_chapter, overview, dry_run=False):
    """一次性为全部章节生成画像（章/节角色、需求、覆盖范围与深度分级），不额外增加调用次数。
    返回 ({chapter_no: chapter_meta}, {section_id: sec_meta})
    """
    if dry_run:
        return {}, {}
    blocks = []
    for ch in chapters:
        ch_sections = [e for e in entries if e["chapter"] == ch["编号"]]
        if not ch_sections:
            continue
        sec_lines = "\n".join(
            f"- section_id={s['section_id']} 节标题={s['section']}" for s in ch_sections)
        blocks.append(
            f"## 章：{ch['编号']} {ch['标题']}\n"
            f"考纲范围：\n{ctx_by_chapter.get(ch['编号'], top_context_text(None))}\n\n"
            f"小节列表：\n{sec_lines}")
    if not blocks:
        return {}, {}
    parsed = _llm_call(f"{overview}\n\n" + "\n\n".join(blocks), "llm_profile 全部章节(一次性)")
    if not parsed:
        return {}, {}
    return _flatten_llm_result(parsed)


def llm_profile_chapter(chapter, sections, ctx_text, overview, dry_run=False):
    """按章调用：为某一章的全部（或缺失）节做章节画像。
    返回 ({chapter_no: chapter_meta}, {section_id: sec_meta})"""
    if not sections or dry_run:
        return {}, {}
    sec_lines = "\n".join(
        f"- section_id={s['section_id']} 节标题={s['section']}" for s in sections)
    usr_prompt = (
        f"{overview}\n\n"
        f"## 章信息\n章编号：{chapter['编号']}　章标题：{chapter['标题']}\n"
        f"本节列表：\n{sec_lines}\n\n"
        f"## 该章对应的考纲范围\n{ctx_text}"
    )
    parsed = _llm_call(usr_prompt, f"llm_profile {chapter['编号']}")
    if not parsed:
        return {}, {}
    return _flatten_llm_result(parsed)


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def _scan_source_files(source_dir: str | None, file_map_path: str | None = None) -> dict:
    """建立 section_id → [文件相对路径] 映射并固化进产物。

    优先读切分后生成的 file_map.json（build_file_map 固化的权威素材：物理文件→section_id），
    只做查表、不做任何推断；找不到时回退扫描源目录（宽松前缀匹配：`X.Y` 前缀，
    兼容无下划线文件名与子节归父节，如 `3.2作业与作业调度.md` → 3.2、`7.4.1_xxx.md` → 7.4）。"""
    import re
    # 1) 优先：切分后固化的权威映射
    if file_map_path:
        fp = Path(file_map_path)
        if not fp.is_absolute():
            fp = _ROOT / fp
        if fp.exists():
            data = json.loads(fp.read_text(encoding="utf-8"))
            m = {}
            for e in data.get("files", []):
                sid = e.get("section_id")
                if sid:
                    m.setdefault(sid, []).append(e["path"])
            rel = fp.relative_to(_ROOT) if fp.is_relative_to(_ROOT) else fp
            print(f"  🗂️ 文件映射：读自 {rel}"
                  f"（{len(data.get('files', []))} 文件 → {len(m)} 节，仅查表）")
            return m
        print(f"  ⚠️ file_map 不存在，回退扫描源目录：{fp}")
    # 2) 回退：扫描源目录（宽松前缀匹配）
    if not source_dir:
        return {}
    root = Path(source_dir)
    if not root.is_dir():
        print(f"  ⚠️ 源目录不存在，跳过文件映射：{root}")
        return {}
    m = {}
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(_ROOT).as_posix()
        # 目录优先（物理存储位置为准），其次文件名前缀
        hit = re.match(r"(\d+\.\d+)", p.parent.name)
        if hit:
            m.setdefault(hit.group(1), []).append(rel)
            continue
        hit = re.match(r"(\d+\.\d+)", p.name)
        if hit:
            m.setdefault(hit.group(1), []).append(rel)
    print(f"  🗂️ 源文件映射（回退扫描）：{len(m)} 节 / "
          f"{sum(len(v) for v in m.values())} 个文件（{root}）")
    return m


def build_map(syllabus_md: str, structure_path: Path, output_path: Path,
              dry_run: bool = False, llm_batch: str = "chapter",
              only_chapter: str | None = None, source_dir: str | None = None,
              file_map_path: str | None = None) -> None:
    tree = parse_syllabus(syllabus_md)
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    chapters = structure.get("chapters", [])

    level1 = [c for c in chapters if c.get("level") == 1]
    sections = [c for c in chapters if c.get("level") == 2]

    # 章 → 对齐的一级条目列表（裁剪 LLM 考纲上下文）
    chap_align = {ch["编号"]: align_chapter_to_topic(ch["编号"], ch.get("标题", ""), tree)
                  for ch in level1}

    if dry_run:
        print("── 章级对齐诊断 ──")
        for ch in level1:
            tops = chap_align[ch["编号"]]
            tag = " / ".join(topic_name(t) for t in tops) if tops else "（无对应，走 LLM 常识补充）"
            print(f"  {ch['编号']} {ch['标题']} → {tag}")
        print("─" * 40)

    # 逐节：先规则匹配（快速路径）
    entries = []
    for sec in sections:
        chapter_no = sec.get("父级编号")
        tops = chap_align.get(chapter_no) or []
        sec_title = sec.get("标题") or sec.get("title", "")
        entry = {
            "section_id": sec.get("编号"),
            "chapter": chapter_no,
            "chapter_title": next((c["标题"] for c in level1 if c["编号"] == chapter_no), ""),
            "section": sec_title,
            "rule_points": rule_match_points(sec_title, tops),
        }
        entries.append(entry)

    rule_hit = sum(1 for e in entries if e["rule_points"])
    print(f"  🔍 规则快速路径：命中 {rule_hit}/{len(entries)} 节")
    if not dry_run:
        rule_miss = [e for e in entries if not e["rule_points"]]
        print(f"  🔍 规则未命中 {len(rule_miss)} 节，将走 LLM 画像")

    # 每章考纲上下文（裁剪后）
    ctx_by_chapter = {}
    for ch in level1:
        tops = chap_align[ch["编号"]]
        ctx_by_chapter[ch["编号"]] = (
            "\n\n".join(top_context_text(t) for t in tops) if tops else top_context_text(None))

    # 全书章节结构概览：LLM 判定章节角色与处理力度的全局视野
    overview = _structure_overview(level1, sections)

    # LLM 画像：默认按章各调用一次；once=全部章节一次性调用
    # only_chapter 指定时只对该章调 LLM（其余章仅规则兜底），用于单章试跑
    chapter_meta, sec_meta = {}, {}

    def _absorb(cbs, sbs):
        """llm_profile_* 已返回展平后的两件套，直接合并。"""
        chapter_meta.update(cbs)
        sec_meta.update(sbs)
        return sbs

    if llm_batch == "once":
        got = _absorb(*llm_profile_all(level1, entries, ctx_by_chapter, overview, dry_run=dry_run))
        if got:
            print(f"  ✅ 一次性 LLM 画像：{len(got)} 节，"
                  f"章节简报 {len(chapter_meta)} 章")
        missing = [e for e in entries if e["section_id"] not in sec_meta]
        if missing and not dry_run:
            print(f"  🔄 一次性画像后仍缺 {len(missing)} 节，按章补跑…")
            for ch in level1:
                ch_missing = [e for e in missing if e["chapter"] == ch["编号"]]
                if ch_missing:
                    _absorb(*llm_profile_chapter(
                        ch, ch_missing, ctx_by_chapter[ch["编号"]], overview, dry_run=dry_run))
    else:
        only_ref = norm_chapter_ref(only_chapter) if only_chapter else None
        for ch in level1:
            ch_sections = [e for e in entries if e["chapter"] == ch["编号"]]
            if not ch_sections:
                continue
            if only_ref and ch["编号"] != only_ref:
                continue
            got = _absorb(*llm_profile_chapter(
                ch, ch_sections, ctx_by_chapter[ch["编号"]], overview, dry_run=dry_run))
            if got:
                print(f"  ✅ {ch['编号']} {ch['标题']}：LLM 画像 {len(got)} 节")
    if only_chapter:
        print(f"  🔒 only_chapter={only_chapter}：其余章跳过 LLM，仅规则强命中兜底")

    # 合并：LLM 输出为准；规则兜底只补「强命中」（子串包含，_score>=1.0）的 scope，
    # 避免 bigram 弱匹配把误挂条目（如"分页"节挂"连续分配管理方式"）带进结果
    for e in entries:
        sm = sec_meta.get(e["section_id"], {})
        e["requirement"] = sm.get("requirement", "")
        e["brief"] = sm.get("brief", "")
        e["role"] = sm.get("role", "")
        e["depth"] = sm.get("depth", "")
        e["depth_reason"] = sm.get("depth_reason", "")
        mc = list(sm.get("scope", []))
        seen = set(mc)
        for rp in e["rule_points"]:
            if rp["title"] in seen or rp.get("_score", 0) < 1.0:
                continue
            mc.append(rp["title"])
            seen.add(rp["title"])
        e["scope"] = mc[:5]
        del e["rule_points"]

    # 物理文件映射：读 file_map.json（切分后固化的权威素材），只查表不做推断；
    # 无 file_map 时回退扫描源目录
    file_map = _scan_source_files(source_dir, file_map_path)
    for e in entries:
        e["files"] = file_map.get(e["section_id"], [])

    # 统计：depth 分布 + scope 覆盖率
    stats = {"total": len(entries)}
    depth_counts = {}
    for e in entries:
        d = e.get("depth") or "none"
        depth_counts[d] = depth_counts.get(d, 0) + 1
    stats["depth"] = dict(sorted(depth_counts.items()))
    stats["covered"] = sum(1 for e in entries if e.get("scope"))

    # 章节简报与全局深度（LLM 画像时顺带生成）
    chapters_out = []
    for ch in level1:
        ch_sections = [e for e in entries if e["chapter"] == ch["编号"]]
        cm = chapter_meta.get(ch["编号"], {})
        chapters_out.append({
            "chapter_no": ch["编号"],
            "chapter_title": ch["标题"],
            "brief": cm.get("brief", ""),
            "chapter_depth": cm.get("chapter_depth", ""),
            "section_count": len(ch_sections),
        })

    out = {
        "subject": structure_path.parent.name,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "model": MODEL,
        "sim_threshold": SIM_THRESHOLD,
        "dry_run": dry_run,
        "llm_batch": llm_batch,
        "stats": stats,
        "chapters": chapters_out,
        "sections": entries,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  💾 chapter_profile → {output_path}")
    depth_str = "，".join(f"{k}={v}" for k, v in stats["depth"].items())
    print(f"  📊 统计：总节 {stats['total']}，覆盖 {stats['covered']} 节，"
          f"深度分布 {depth_str}")
    if dry_run:
        missing = [f"{e['section_id']} {e['section']}" for e in entries
                   if not e["scope"]]
        print(f"  🔍 [dry-run] 规则未命中、需 LLM 画像的节（{len(missing)}）：")
        for m in missing:
            print(f"    - {m}")


def main():
    parser = argparse.ArgumentParser(description="S3 章节画像：全书结构 → 每节角色(role)+处理深度(depth)+涉及范围(scope)+覆盖需求(requirement)；考纲为可选参考信号")
    parser.add_argument("--subject", default=None, help="学科标识（如 os），从 config/<subject>.json 读路径")
    parser.add_argument("--syllabus", default=None, help="考纲 Markdown 路径")
    parser.add_argument("--structure", default=None, help="chapter_structure.json 路径")
    parser.add_argument("--output", default=None, help="输出 chapter_profile.json 路径")
    parser.add_argument("--dry-run", action="store_true", help="只跑规则匹配，不调 LLM")
    parser.add_argument("--llm-batch", choices=["once", "chapter"], default="chapter",
                        help="LLM 画像批次：chapter=每章一次调用（默认，输出更稳、失败隔离）；once=全部章节一次性调用")
    parser.add_argument("--only-chapter", default=None, help="只对指定章做 LLM 画像（如'第二章'），其余章仅规则兜底；用于单章试跑")
    parser.add_argument("--source-dir", default=None,
                        help="教材源目录（默认为 subject 配置的 source_dirs[0]）；无 file_map 时的回退扫描目录")
    parser.add_argument("--file-map", default=None,
                        help="切分后生成的文件映射 file_map.json（默认 <intermediate_dir>/file_map.json）；只查表，不做推断")
    args = parser.parse_args()

    if args.subject:
        cfg_path = _ROOT / "config" / f"{args.subject}.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        global MODEL
        MODEL = cfg.get("model", MODEL)
        syllabus = args.syllabus or str(_ROOT / cfg["syllabus"])
        structure = args.structure or str(_ROOT / cfg["source_dirs"][0].split("/")[0] / "chapter_structure.json")
        output = args.output or str(_ROOT / cfg["intermediate_dir"] / "chapter_profile.json")
        source_dir = args.source_dir or str(_ROOT / cfg["source_dirs"][0])
        file_map_path = args.file_map or str(_ROOT / cfg["intermediate_dir"] / "file_map.json")
    else:
        if not (args.syllabus and args.structure and args.output):
            parser.error("未指定 --subject，必须同时提供 --syllabus --structure --output")
        syllabus, structure, output = args.syllabus, args.structure, args.output
        source_dir = args.source_dir
        file_map_path = args.file_map

    print("=" * 60)
    print("S3 章节画像 pass")
    print("=" * 60)
    print(f"  考纲：{syllabus}")
    print(f"  章节结构：{structure}")
    print(f"  输出：{output}")
    print(f"  dry-run：{'是' if args.dry_run else '否'}")
    print(f"  LLM 批次：{args.llm_batch}")
    if args.only_chapter:
        print(f"  仅 LLM 画像章：{args.only_chapter}")
    print("=" * 60)

    syllabus_text = Path(syllabus).read_text(encoding="utf-8")
    build_map(syllabus_text, Path(structure), Path(output),
              dry_run=args.dry_run, llm_batch=args.llm_batch,
              only_chapter=args.only_chapter, source_dir=source_dir,
              file_map_path=file_map_path)

    tracker = get_tracker("build_chapter_profile")
    tracker.print_summary()


if __name__ == "__main__":
    main()
