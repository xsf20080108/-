# -*- coding: utf-8 -*-
"""
模块 e · 端到端入口

输入：模块 a 结构化 JSON（可复用已生成的 record）
输出：output/<pid>.enhance.json  +  .md（预判 FAQ + 触达 + 增值服务 + 闭环 KPI）

用法：
    python -m modules.e.run output/kr1_final/CL0300004584506696.json
"""

from __future__ import annotations

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.e.service import run as run_enhance


def run(record_path: str, out_path: str = None) -> dict:
    record = json.load(open(record_path, encoding="utf-8"))
    pid = record.get("patient_id") or os.path.splitext(os.path.basename(record_path))[0]

    result = run_enhance(record)

    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(record_path)), f"{pid}.enhance.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 可读版
    md = render_md(result)
    md_path = out_path.replace(".json", ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"模块 e 完成：{out_path} / {md_path}")
    return result


def render_md(result: dict) -> str:
    lines = ["# 理赔关怀增强", ""]

    lines.append("## 一、预判式 FAQ（您可能还想了解）")
    faqs = result.get("predictive_faqs", [])
    if not faqs:
        lines.append("（根据病历特征，暂未触发预判问题）")
    for f in faqs:
        lines.append(f"- **{f['question']}**（{f['why']}）")
        lines.append(f"  - {f['answer']}")
    lines.append("")

    lines.append("## 二、触达渠道建议")
    ch = result.get("channel", {})
    lines.append(f"- 推荐渠道：**{ch.get('channel', '')}**")
    lines.append(f"- 理由：{ch.get('reason', '')}")

    lines.append("")
    lines.append("## 三、增值服务推荐")
    svcs = result.get("value_added_services", [])
    if not svcs:
        lines.append("（根据病种暂未匹配到增值服务）")
    for s in svcs:
        lines.append(f"- 匹配「{s['match']}」→ {('、'.join(s['services']))}")

    lines.append("")
    lines.append("## 四、闭环 KPI")
    kpi = result.get("closed_loop_kpi", {})
    lines.append(f"- 指标：{kpi.get('metric', '')}")
    lines.append(f"- 公式：{kpi.get('formula', '')}")
    lines.append(f"- 目标：{kpi.get('target', '')}")
    lines.append(f"- 追踪主题：{('、'.join(kpi.get('topics_tracked', []))) or '—'}")

    lines.append("")
    lines.append("## 五、异常模式监控（对模式变异免疫）")
    am = result.get("anomaly_monitoring", {})
    if am:
        res = am.get("result", {})
        lines.append(f"- 告警规则：{am.get('rule', '')}")
        lines.append(f"- 当前结论：{res.get('message', '')}")
        alerts = res.get("alerts", [])
        for a in alerts:
            lines.append(f"  - ⚠️ 主题「{a['topic']}」较基线增长 {a['growth']:.0%}（基线 {a['baseline']} → 当前 {a['current']}）")
        baseline = am.get("baseline_topics", {})
        if baseline:
            top = sorted(baseline.items(), key=lambda x: -x[1])[:8]
            lines.append(f"- 问答库主题基线（真实）：{'、'.join(f'{k} {v}' for k, v in top)}")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="模块 e 理赔关怀增强")
    ap.add_argument("record_json", help="模块 a 结构化结果 JSON 路径")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    run(args.record_json, out_path=args.out)


if __name__ == "__main__":
    main()
