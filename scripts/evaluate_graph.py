"""
图谱质量自动评估
读取 nodes.json + edges_topic.json，产出多维指标报告。
用法: python scripts/evaluate_graph.py [--nodes nodes.json] [--edges edges_topic.json] [--out report.json]
"""
import json, argparse, sys
from pathlib import Path
from collections import defaultdict, Counter

# ── CLI ──
parser = argparse.ArgumentParser(description="图谱质量评估")
parser.add_argument("--nodes",   default="result_v3/nodes.json",       help="节点文件")
parser.add_argument("--edges",   default="result_v3/edges_topic.json", help="边文件（topic 边）")
parser.add_argument("--out",     default="result_v3/eval_report.json", help="评估报告输出")
args = parser.parse_args()

BASE = Path(__file__).parent.parent
NODES_PATH = BASE / args.nodes
EDGES_PATH = BASE / args.edges
OUT_PATH   = BASE / args.out

# ── 加载数据 ──
if not NODES_PATH.exists():
    print(f"[ERROR] 节点文件不存在: {NODES_PATH}")
    sys.exit(1)
if not EDGES_PATH.exists():
    print(f"[WARN] 边文件不存在: {EDGES_PATH}，仅做节点统计")
    edges_data = {"edges": []}
else:
    edges_data = json.loads(EDGES_PATH.read_text(encoding="utf-8"))

nodes_data = json.loads(NODES_PATH.read_text(encoding="utf-8"))
nodes = nodes_data.get("nodes", nodes_data)
edges = edges_data.get("edges", [])
if not edges and isinstance(edges_data, list):
    edges = edges_data

# ── 基础统计 ──
node_count = len(nodes)
edge_count = len(edges)

# 节点类型分布（字段名可能是 type 或 node_kind）
type_map = Counter()
chapter_map = Counter()
for n in nodes:
    kind = n.get("type") or n.get("node_kind", "unknown")
    type_map[kind] += 1
    ch = n.get("chapter", 0)
    chapter_map[ch] += 1

# 边类型分布
edge_type_map = Counter()
for e in edges:
    edge_type_map[e.get("type", "unknown")] += 1

# ── 图结构指标 ──
id2ch = {n["node_id"]: n.get("chapter", 0) for n in nodes}
id_set = set(id2ch.keys())

# 入度/出度
in_deg  = Counter()
out_deg = Counter()
for e in edges:
    if e["from_id"] in id_set:
        out_deg[e["from_id"]] += 1
    if e["to_id"] in id_set:
        in_deg[e["to_id"]] += 1

# 孤立节点（既无入度也无出度）
all_ids = set(id2ch.keys())
edge_ids = set(in_deg.keys()) | set(out_deg.keys())
orphan_ids = all_ids - edge_ids
orphan_count = len(orphan_ids)

# 平均度
total_deg = sum(in_deg.values()) + sum(out_deg.values())
avg_out = sum(out_deg.values()) / max(node_count, 1)
avg_in  = sum(in_deg.values())  / max(node_count, 1)

# 密度（有向图: 边数 / (n*(n-1))）
if node_count > 1:
    density = edge_count / (node_count * (node_count - 1))
else:
    density = 0.0

# 连通分量（并查集，忽略方向）
parent = {uid: uid for uid in all_ids}
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb

for e in edges:
    if e["from_id"] in id_set and e["to_id"] in id_set:
        union(e["from_id"], e["to_id"])

comp_map = Counter()
for uid in all_ids:
    comp_map[find(uid)] += 1
num_components = len(comp_map)
max_component_size = max(comp_map.values()) if comp_map else 0

# 跨章边比例
cross_chapter = 0
for e in edges:
    fc = id2ch.get(e["from_id"], 0)
    tc = id2ch.get(e["to_id"], 0)
    if fc and tc and fc != tc:
        cross_chapter += 1
cross_ratio = cross_chapter / max(edge_count, 1)

# ── 节点来源分析 ──
source_type_map = Counter()
for n in nodes:
    source_type_map[n.get("node_kind", "unknown")] += 1

# ── 汇总报告 ──
report = {
    "meta": {
        "nodes_file":    str(NODES_PATH),
        "edges_file":    str(EDGES_PATH),
        "eval_time":     __import__("datetime").datetime.now().isoformat(),
    },
    "summary": {
        "node_count":        node_count,
        "edge_count":        edge_count,
        "density":           round(density, 6),
        "avg_in_degree":     round(avg_in, 2),
        "avg_out_degree":    round(avg_out, 2),
        "orphan_nodes":      orphan_count,
        "orphan_pct":        round(orphan_count / max(node_count, 1) * 100, 1),
        "num_components":    num_components,
        "max_component_size": max_component_size,
        "max_component_pct": round(max_component_size / max(node_count, 1) * 100, 1),
        "cross_chapter_edges": cross_chapter,
        "cross_chapter_pct": round(cross_ratio * 100, 1),
    },
    "detail": {
        "types":           dict(type_map.most_common()),
        "edge_types":      dict(edge_type_map.most_common()),
        "chapters":        {str(k): v for k, v in sorted(chapter_map.items()) if k > 0},
        "node_kinds":      dict(source_type_map.most_common()),
    },
    "alerts": [],
}

# 自动告警
if orphan_count > node_count * 0.3:
    report["alerts"].append(f"孤立节点过多 ({orphan_count}/{node_count}, {report['summary']['orphan_pct']}%)")
if density < 0.001:
    report["alerts"].append(f"图密度过低 ({density:.6f})")
if num_components > 10:
    report["alerts"].append(f"连通分量过多 ({num_components})")
if cross_ratio < 0.05:
    report["alerts"].append(f"跨章连接偏少 ({cross_ratio*100:.1f}%)")
if not report["alerts"]:
    report["alerts"].append("✅ 无异常")

# ── 输出 ──
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 控制台输出 ──
print("=" * 60)
print("📊 图谱质量评估报告")
print("=" * 60)
print(f"  节点总数:        {node_count}")
print(f"  边总数:          {edge_count}")
print(f"  密度:            {density:.6f}")
print(f"  平均入度/出度:   {avg_in:.1f} / {avg_out:.1f}")
print(f"  孤立节点:        {orphan_count} ({report['summary']['orphan_pct']}%)")
print(f"  连通分量:        {num_components} (最大 {max_component_size}，占 {report['summary']['max_component_pct']}%)")
print(f"  跨章边:          {cross_chapter} ({cross_ratio*100:.1f}%)")
print()
print(f"  节点类型: {json.dumps(dict(type_map.most_common(8)), ensure_ascii=False)}")
print(f"  边类型:   {json.dumps(dict(edge_type_map.most_common()), ensure_ascii=False)}")
print(f"  按章分布: {json.dumps({str(k):v for k,v in sorted(chapter_map.items()) if k>0}, ensure_ascii=False)}")
print()
print(f"  告警: {report['alerts']}")
print()
print(f"  报告已保存: {OUT_PATH}")
