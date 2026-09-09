#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
billing.py — 阿里云百炼 API 计费跟踪模块

定价（元/百万 tokens）：
  qwen3.7-max:   input 12, output 36
  qwen3.7-plus:  input 2,  output 8

用法：
  from billing import BillingTracker

  tracker = BillingTracker("reprocess_v3")  # 或 "build_edges_v2" / "prograhspilt_v3"

  # 每次 LLM 调用后
  tracker.add(model="qwen3.7-max", prompt_tokens=1234, completion_tokens=567)

  # 最后
  tracker.summary()
  tracker.save("/path/to/billing.json")
"""

import json, time, os
from pathlib import Path

# ── 定价表（元 / 百万 tokens）─────────────────────────
PRICING = {
    "qwen3.7-max":  {"input": 12.0, "output": 36.0},
    "qwen3.7-plus": {"input": 2.0,  "output": 8.0},
    "qwen-plus":    {"input": 2.0,  "output": 8.0},   # 老别名
}

DEFAULT_MODEL = "qwen3.7-plus"

# ── 全局单例（按脚本名隔离）─────────────────────────
_GLOBAL_TRACKERS: dict[str, "BillingTracker"] = {}


def get_tracker(name: str = "global") -> "BillingTracker":
    """获取脚本级单例 tracker"""
    if name not in _GLOBAL_TRACKERS:
        _GLOBAL_TRACKERS[name] = BillingTracker(name)
    return _GLOBAL_TRACKERS[name]


def extract_usage(response):
    """从 OpenAI 兼容 response 对象中提取 usage dict

    返回: {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    """
    usage = getattr(response, "usage", None)
    if usage:
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class BillingTracker:
    """计费跟踪器"""

    def __init__(self, name: str = "unknown"):
        self.name = name
        self.calls: list[dict] = []
        self._start_time = time.time()

    def add(self, model: str, prompt_tokens: int, completion_tokens: int,
            description: str = ""):
        """记录一次 LLM 调用的 token 用量"""
        pricing = PRICING.get(model, PRICING[DEFAULT_MODEL])
        input_cost = prompt_tokens / 1_000_000 * pricing["input"]
        output_cost = completion_tokens / 1_000_000 * pricing["output"]
        total_cost = input_cost + output_cost

        entry = {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": round(total_cost, 6),
            "description": description,
        }
        self.calls.append(entry)
        return entry

    def add_from_usage(self, model: str, usage: dict, description: str = ""):
        """从 extract_usage() 的返回值记录"""
        return self.add(
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            description=description,
        )

    @property
    def total_tokens(self) -> int:
        return sum(c["total_tokens"] for c in self.calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(c["prompt_tokens"] for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c["completion_tokens"] for c in self.calls)

    @property
    def total_cost(self) -> float:
        return sum(c["total_cost"] for c in self.calls)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def summary(self) -> str:
        """打印账单摘要"""
        elapsed = time.time() - self._start_time
        lines = [
            "",
            "=" * 60,
            f"💰 API 计费摘要 [{self.name}]",
            "=" * 60,
            f"  调用次数：{self.call_count}",
            f"  输入 tokens：{self.total_input_tokens:,}",
            f"  输出 tokens：{self.total_output_tokens:,}",
            f"  总 tokens：{self.total_tokens:,}",
            f"  预估费用：¥{self.total_cost:.4f}",
            f"  耗时：{elapsed:.1f}s",
        ]

        # 按模型分组统计
        by_model: dict[str, list] = {}
        for c in self.calls:
            m = c["model"]
            by_model.setdefault(m, []).append(c)

        if len(by_model) > 1:
            lines.append("  --- 按模型 ---")
            for model, entries in sorted(by_model.items()):
                total = sum(e["total_cost"] for e in entries)
                lines.append(
                    f"    {model}: {len(entries)} 次, "
                    f"{sum(e['total_tokens'] for e in entries):,} tokens, "
                    f"¥{total:.4f}"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def print_summary(self):
        """打印并返回摘要"""
        text = self.summary()
        print(text)
        return text

    def save(self, path: str | Path):
        """保存为 JSON 文件"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "name": self.name,
            "call_count": self.call_count,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": round(self.total_cost, 4),
            "elapsed_seconds": round(time.time() - self._start_time, 1),
            "calls": self.calls,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ── 合并多个 billing.json ─────────────────────────────
def merge_billing_files(file_paths: list[str | Path]) -> dict:
    """合并多个 billing.json 文件，返回汇总数据"""
    merged = {
        "call_count": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "by_source": {},
    }
    for fp in file_paths:
        if not os.path.exists(fp):
            continue
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name", os.path.basename(fp))
        merged["call_count"] += data["call_count"]
        merged["total_tokens"] += data["total_tokens"]
        merged["total_cost"] += data["total_cost"]
        merged["by_source"][name] = {
            "call_count": data["call_count"],
            "total_tokens": data["total_tokens"],
            "total_cost": round(data["total_cost"], 4),
        }
    merged["total_cost"] = round(merged["total_cost"], 4)
    return merged
