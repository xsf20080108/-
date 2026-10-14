# -*- coding: utf-8 -*-
"""
模块 b · 批量痛点识别 + 一致性评估（KR2 效果验证）

解决两个缺口：
  1. 批量产出证据：对全部 70 例结构化病历跑规则轨，写出 output/kr2/<pid>.pain_points.json，
     并汇总覆盖率 / 类别分布 / 严重度分布（此前 KR2 只有 2 个孤例）。
  2. 一致率工具：支持读入人工标注 gold，用 evaluate.py 算「标签 F1 + 严重程度一致率」，
     作为 KR2「与专家标注一致率 ≥85%」的可复现评估入口（官方未给 gold，需团队自标注）。

用法：
    python -m modules.b.consistency                        # 离线：规则轨批量 + 汇总
    python -m modules.b.consistency --gold output/kr2_gold.json   # 与人工标注比对，算一致率
    python -m modules.b.consistency --llm --limit 10       # 加 LLM轨（交叉一致率，仅重叠类型）

gold 文件格式（JSON）：
    { "CL0300004435037004": [{"type": "cost_self_pay_high", "severity": "高"}, ...], ... }
"""

from __future__ import annotations

import os
import sys
import json
import glob
import argparse
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.b.schema import TAXONOMY, CATEGORIES
from modules.b.rules import detect_rules
from modules.b.detect import detect_all, LLMDetector
from modules.b import evaluate


PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_RECORDS = os.path.join(PROJ, "output", "kr1_final")
OUT_DIR = os.path.join(PROJ, "output", "kr2")


def find_records(base: str) -> List[str]:
    """找出结构化病历 JSON（仅 CL<id>.json，排除 .guidance/.pain_points 等衍生文件）。"""
    import re
    out = []
    for p in sorted(glob.glob(os.path.join(base, "*.json"))):
        fn = os.path.basename(p)
        if re.fullmatch(r"CL\d+\.json", fn):
            out.append(p)
    return out


def _to_list(pps) -> List[Dict]:
    return [p.model_dump() if hasattr(p, "model_dump") else p for p in pps]


def batch_rules(records: List[str], out_dir: str = OUT_DIR) -> Dict[str, List[Dict]]:
    """规则轨批量识别，写出 per-case pain_points.json，返回 {pid: [painpoint dict]}。"""
    os.makedirs(out_dir, exist_ok=True)
    result: Dict[str, List[Dict]] = {}
    for p in records:
        rec = json.load(open(p, encoding="utf-8"))
        pid = rec.get("patient_id") or os.path.splitext(os.path.basename(p))[0]
        pps = detect_rules(rec)
        data = _to_list(pps)
        result[pid] = data
        with open(os.path.join(out_dir, f"{pid}.pain_points.json"), "w", encoding="utf-8") as f:
            json.dump({"patient_id": pid, "pain_points": data}, f, ensure_ascii=False, indent=2)
    return result


def summarize(per_case: Dict[str, List[Dict]]) -> Dict[str, Any]:
    """汇总覆盖率 / 类别 / 严重度 / 类型分布。"""
    n = len(per_case)
    with_pp = [pid for pid, pps in per_case.items() if pps]
    total_pps = sum(len(v) for v in per_case.values())

    type_dist: Dict[str, int] = {}
    cat_dist: Dict[str, int] = {}
    sev_dist: Dict[str, int] = {}
    for pps in per_case.values():
        for p in pps:
            t = p.get("type", "")
            type_dist[t] = type_dist.get(t, 0) + 1
            cat_dist[p.get("category", "")] = cat_dist.get(p.get("category", ""), 0) + 1
            sev_dist[p.get("severity", "")] = sev_dist.get(p.get("severity", ""), 0) + 1

    return {
        "n_cases": n,
        "n_cases_with_pain_points": len(with_pp),
        "coverage": round(len(with_pp) / n, 4) if n else 0,
        "total_pain_points": total_pps,
        "avg_pain_points_per_case": round(total_pps / n, 3) if n else 0,
        "type_distribution": {k: type_dist.get(k, 0) for k in TAXONOMY},
        "category_distribution": {k: cat_dist.get(k, 0) for k in CATEGORIES},
        "severity_distribution": sev_dist,
    }


def consistency_vs_gold(per_case: Dict[str, List[Dict]],
                        gold: Dict[str, List[Dict]]) -> Dict[str, Any]:
    """用 evaluate.py 的标签 F1 / 严重度一致率，逐病例与 gold 比对后取宏平均。"""
    macro = {"label_f1": [], "label_precision": [], "label_recall": [],
             "severity_agreement": [], "tp": 0, "fp": 0, "fn": 0}
    per_case_res = {}
    for pid, pred in per_case.items():
        if pid not in gold:
            continue
        g = gold[pid]
        r = evaluate.evaluate(g, pred)
        per_case_res[pid] = r
        macro["label_f1"].append(r["label_f1"])
        macro["label_precision"].append(r["label_precision"])
        macro["label_recall"].append(r["label_recall"])
        macro["severity_agreement"].append(r["severity_agreement"])
        macro["tp"] += r["tp"]
        macro["fp"] += r["fp"]
        macro["fn"] += r["fn"]

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    # 微平均 F1（按 tp/fp/fn 汇总）
    tp, fp, fn = macro["tp"], macro["fp"], macro["fn"]
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    micro_f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    return {
        "n_evaluated": len(per_case_res),
        "macro_label_f1": mean(macro["label_f1"]),
        "macro_label_precision": mean(macro["label_precision"]),
        "macro_label_recall": mean(macro["label_recall"]),
        "macro_severity_agreement": mean(macro["severity_agreement"]),
        "micro_label_f1": round(micro_f1, 4),
        "target": 0.85,
        "pass": (mean(macro["label_f1"]) or 0) >= 0.85,
        "per_case": per_case_res,
    }


def cross_track_consistency(records: List[str], limit: int) -> Dict[str, Any]:
    """规则轨 vs LLM轨 交叉一致率（仅两者都会产出的类型有意义，如临床类）。"""
    n = min(limit, len(records))
    agree_type = 0
    agree_sev = 0
    common_cases = 0
    details = []
    for p in records[:n]:
        rec = json.load(open(p, encoding="utf-8"))
        pid = rec.get("patient_id") or os.path.splitext(os.path.basename(p))[0]
        rule_pps = detect_rules(rec)
        try:
            llm_pps = LLMDetector().detect(rec)
        except Exception as e:
            print(f"[warn] LLM 失败 {pid}: {e}")
            continue
        # 只比对两轨都覆盖的类型（重叠类型）
        rule_types = {x.type: x for x in rule_pps}
        llm_types = {x.type: x for x in llm_pps}
        overlap = set(rule_types) & set(llm_types)
        if not overlap:
            continue
        common_cases += 1
        for t in overlap:
            agree_type += 1
            if rule_types[t].severity == llm_types[t].severity:
                agree_sev += 1
            details.append({"pid": pid, "type": t,
                            "rule_sev": rule_types[t].severity,
                            "llm_sev": llm_types[t].severity})
    return {
        "n_cases_with_overlap": common_cases,
        "n_overlap_types": agree_type,
        "severity_agreement_on_overlap": round(agree_sev / agree_type, 4) if agree_type else None,
        "note": "仅统计两轨都会产出的重叠类型；两轨本为互补设计（规则=确定性，LLM=语义），"
                "交叉一致率只反映重叠类型上的严重度共识，不代表完整专家一致率",
        "details": details,
    }


def render_report(summary: Dict, gold_res: Dict = None, cross: Dict = None) -> str:
    L: List[str] = ["# KR2 就医痛点 —— 批量产出与一致性评估报告", ""]
    L.append(f"> 结构化病历来源：output/kr1_final/（{summary['n_cases']} 例）")
    L.append("")

    L.append("## 一、识别覆盖率（规则轨，离线可复现）")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 病例总数 | {summary['n_cases']} |")
    L.append(f"| 识别出 ≥1 个痛点的病例 | {summary['n_cases_with_pain_points']} |")
    L.append(f"| 覆盖率 | {summary['coverage']:.1%} |")
    L.append(f"| 痛点总数 / 每例均值 | {summary['total_pain_points']} / {summary['avg_pain_points_per_case']} |")
    L.append("")

    L.append("## 二、痛点类型分布（13 类）")
    L.append("")
    L.append("| 类型 | 大类 | 命中病例数 |")
    L.append("|---|---|---|")
    for k, v in summary["type_distribution"].items():
        name = TAXONOMY.get(k, {}).get("name", k)
        cat = TAXONOMY.get(k, {}).get("category", "—")
        L.append(f"| {name}（`{k}`） | {cat} | {v} |")
    L.append("")

    L.append("## 三、大类分布 / 严重度分布")
    L.append("")
    L.append(f"- 大类：{json.dumps(summary['category_distribution'], ensure_ascii=False)}")
    L.append(f"- 严重度：{json.dumps(summary['severity_distribution'], ensure_ascii=False)}")
    L.append("")

    if gold_res is not None:
        L.append("## 四、与人工标注 gold 的一致率（专家一致率 ≥85% 目标）")
        L.append("")
        L.append("| 指标 | 数值 | 目标 | 判定 |")
        L.append("|---|---|---|---|")
        L.append(f"| 宏平均标签 F1 | {gold_res['macro_label_f1']} | ≥0.85 | {'✅' if gold_res['pass'] else '❌'} |")
        L.append(f"| 宏平均精确率 | {gold_res['macro_label_precision']} | — | — |")
        L.append(f"| 宏平均召回率 | {gold_res['macro_label_recall']} | — | — |")
        L.append(f"| 严重程度一致率 | {gold_res['macro_severity_agreement']} | — | — |")
        L.append(f"| 微平均标签 F1 | {gold_res['micro_label_f1']} | ≥0.85 | — |")
        L.append("")
        L.append(f"> 已评估病例数：{gold_res['n_evaluated']}。")
        L.append("> **口径说明**：gold 为团队自标注（非官方专家标注），官方未提供痛点 gold。")
        L.append("")

    if cross is not None:
        L.append("## 五、规则轨 vs LLM轨 交叉一致率（重叠类型）")
        L.append("")
        L.append(f"- 重叠类型严重度一致率：{cross['severity_agreement_on_overlap']}")
        L.append(f"- 有重叠的病例数：{cross['n_cases_with_overlap']}")
        L.append(f"> {cross['note']}")
        L.append("")

    L.append("## 附：如何补测真实专家一致率")
    L.append("")
    L.append("1. 由团队医学同学按 `output/kr2_gold.template.json` 格式，对 20~30 例逐条标注痛点 type + severity；")
    L.append("2. 运行 `python -m modules.b.consistency --gold output/kr2_gold.json`；")
    L.append("3. 报告宏平均标签 F1 与严重度一致率（目标 ≥0.85）。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="KR2 批量痛点识别 + 一致性评估")
    ap.add_argument("--records", default=DEFAULT_RECORDS, help="结构化病历目录")
    ap.add_argument("--gold", default=None, help="人工标注 gold JSON（{pid: [{type,severity}]}）")
    ap.add_argument("--llm", action="store_true", help="是否运行 LLM 轨（需 .env 的 key）")
    ap.add_argument("--limit", type=int, default=10, help="LLM 轨最多跑多少例")
    args = ap.parse_args()

    records = find_records(args.records)
    print(f"找到 {len(records)} 例结构化病历")

    print("[1/3] 规则轨批量识别（离线）...")
    per_case = batch_rules(records)
    summary = summarize(per_case)
    print(f"  覆盖率 {summary['coverage']:.1%}，每例均值 {summary['avg_pain_points_per_case']} 个痛点")

    gold_res = None
    if args.gold:
        print("[2/3] 与人工标注 gold 比对...")
        gold = json.load(open(args.gold, encoding="utf-8"))
        gold_res = consistency_vs_gold(per_case, gold)
        print(f"  宏平均标签 F1 = {gold_res['macro_label_f1']}，严重度一致率 = {gold_res['macro_severity_agreement']}")

    cross = None
    if args.llm:
        print(f"[{'3/3' if args.gold else '2/3'}] 规则轨 vs LLM轨 交叉一致率（前 {args.limit} 例）...")
        cross = cross_track_consistency(records, args.limit)

    # 写汇总
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "gold_consistency": gold_res, "cross_track": cross},
                  f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "kr2_report.md"), "w", encoding="utf-8") as f:
        f.write(render_report(summary, gold_res, cross))

    print(f"完成：{OUT_DIR}/_summary.json 与 {OUT_DIR}/kr2_report.md")


if __name__ == "__main__":
    main()
