# -*- coding: utf-8 -*-
"""
模块 c · 人工盲评工具（KR3 温度指引「人工盲评 ≥4/5」）

此前盲评由 LLM 模拟盲评员完成（evaluate.py 的 BlindReviewer），属自评，不符合赛题
「人工盲评」要求。本工具把盲评流程做成人可执行的：

  prepare —— 把已生成的温度指引打包成「盲码」评审包（去掉病例号，评审人看不到身份），
             并生成打分表模板（温度感 / 可行动性，各 1~5 分，可给半分）。
  merge   —— 读入 1~3 名评审人的打分，按盲码映射回病例，算均分，写出
             <case_id>.guidance.scores.human.json + human_blind_review_summary.md。

用法：
    python -m modules.c.human_review prepare [--guidance-dir output] [--out output/review]
    python -m modules.c.human_review merge --scores a.json b.json c.json [--map output/review/code_map.json]

打分文件格式（每名评审人一份）：
    { "R01": {"temperature": 4.5, "actionability": 4.0, "comment": "..."}, ... }
"""

from __future__ import annotations

import os
import sys
import re
import json
import glob
import argparse
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def _anonymize(text: str) -> str:
    """轻量去标识：去掉病例号与明显的人名称呼，降低评审人识别出具体病例的概率。"""
    t = re.sub(r"CL\d{16,}", "[病例]", text)
    # 去「X先生 / X女士」里的人名，只保留称谓
    t = re.sub(r"[一-鿿]{1,2}\s*先生", "先生", t)
    t = re.sub(r"[一-鿿]{1,2}\s*女士", "女士", t)
    return t


def prepare(guidance_dir: str, out_dir: str) -> Dict[str, str]:
    """打包盲评材料，返回 code_map {code: case_id}。"""
    os.makedirs(os.path.join(out_dir, "blinded"), exist_ok=True)
    files = sorted(glob.glob(os.path.join(guidance_dir, "*.guidance.md")))
    code_map: Dict[str, str] = {}
    score_template: Dict[str, Any] = {}
    for i, p in enumerate(files, 1):
        code = f"R{i:02d}"
        case_id = os.path.basename(p).replace(".guidance.md", "")
        code_map[code] = case_id
        text = open(p, encoding="utf-8").read()
        with open(os.path.join(out_dir, "blinded", f"{code}.md"), "w", encoding="utf-8") as f:
            f.write(_anonymize(text))
        score_template[code] = {"temperature": None, "actionability": None, "comment": ""}

    # code_map 与打分表分离：code_map 仅给统计者，评审人只拿 blinded/ 与打分表
    with open(os.path.join(out_dir, "code_map.json"), "w", encoding="utf-8") as f:
        json.dump(code_map, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "score_sheet.template.json"), "w", encoding="utf-8") as f:
        json.dump(score_template, f, ensure_ascii=False, indent=2)

    readme = [
        "# 人工盲评说明", "",
        "评审人只看 `blinded/` 目录下的 R01.md ~ R{nn}.md（盲码，看不到病例号/姓名），",
        "按 5 分制给两个维度打分（可给半分）：",
        "- temperature（温度感）：5=像懂行家人对「我」说话/共情自然/锚定我的情况；1=通用科普。",
        "- actionability（可行动性）：5=每条有明确数字/时间/方法；1=几乎没有可执行建议。",
        "",
        "打分后每人填一份 score_sheet.template.json（把 null 换成分数），命名为 reviewer1.json、",
        "reviewer2.json、reviewer3.json，交给统计者用 `merge` 汇总。",
    ]
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(readme).replace("{nn}", str(len(files))))

    print(f"打包 {len(files)} 份指引 → {out_dir}/blinded/（盲码 R01~R{len(files):02d}）")
    print(f"打分表模板：{out_dir}/score_sheet.template.json")
    return code_map


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def merge(score_files: List[str], code_map_path: str) -> Dict[str, Any]:
    code_map = json.load(open(code_map_path, encoding="utf-8"))
    # 汇总所有评审人：{code: {temperature:[], actionability:[], comments:[]}}
    agg: Dict[str, Dict[str, List[Any]]] = {}
    for sf in score_files:
        scores = json.load(open(sf, encoding="utf-8"))
        for code, s in scores.items():
            if code not in code_map:
                continue
            a = agg.setdefault(code, {"temperature": [], "actionability": [], "comments": []})
            t, ac = s.get("temperature"), s.get("actionability")
            if t is not None:
                a["temperature"].append(float(t))
            if ac is not None:
                a["actionability"].append(float(ac))
            if s.get("comment"):
                a["comments"].append(s["comment"])

    results = {}
    for code, a in agg.items():
        if not a["temperature"] or not a["actionability"]:
            continue
        t_avg = round(sum(a["temperature"]) / len(a["temperature"]), 2)
        a_avg = round(sum(a["actionability"]) / len(a["actionability"]), 2)
        results[code] = {
            "case_id": code_map[code],
            "n_reviewers": len(a["temperature"]),
            "temperature_avg": t_avg,
            "actionability_avg": a_avg,
            "overall_avg": round((t_avg + a_avg) / 2, 2),
            "temperature_scores": a["temperature"],
            "actionability_scores": a["actionability"],
            "comments": a["comments"],
            "pass": t_avg >= 4 and a_avg >= 4,
        }
    return results


def _write_merge(results: Dict[str, Any], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for code, r in results.items():
        case_id = r["case_id"]
        # 与 LLM 模拟盲评的 .guidance.scores.json 同目录、可对照，加 .human 后缀区分来源
        p = os.path.join(PROJ, "output", f"{case_id}.guidance.scores.human.json")
        payload = {k: r[k] for k in ("temperature_avg", "actionability_avg", "overall_avg",
                                     "temperature_scores", "actionability_scores", "comments")}
        payload["source"] = "human_blind_review"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # 汇总
    n_pass = sum(1 for r in results.values() if r["pass"])
    n = len(results)
    avg_t = round(sum(r["temperature_avg"] for r in results.values()) / n, 2) if n else 0
    avg_a = round(sum(r["actionability_avg"] for r in results.values()) / n, 2) if n else 0
    lines = [
        "# KR3 温度指引 —— 人工盲评汇总", "",
        f"- 参与盲评病例数：{n}",
        f"- 温度感均分：{avg_t} / 可行动性均分：{avg_a} / 总均分：{round((avg_t + avg_a) / 2, 2)}",
        f"- 达标（两维度均 ≥4）病例数：{n_pass} / {n}",
        "",
        "| 盲码 | 病例 | 评审人数 | 温度感 | 可行动性 | 总均分 | 达标 |",
        "|---|---|---|---|---|---|---|",
    ]
    for code in sorted(results):
        r = results[code]
        lines.append(f"| {code} | {r['case_id']} | {r['n_reviewers']} | "
                     f"{r['temperature_avg']} | {r['actionability_avg']} | {r['overall_avg']} | "
                     f"{'✅' if r['pass'] else '❌'} |")
    with open(os.path.join(out_dir, "human_blind_review_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"人工盲评汇总：{out_dir}/human_blind_review_summary.md（达标 {n_pass}/{n}）")


def main():
    ap = argparse.ArgumentParser(description="KR3 温度指引人工盲评工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("prepare")
    p1.add_argument("--guidance-dir", default=os.path.join(PROJ, "output"))
    p1.add_argument("--out", default=os.path.join(PROJ, "output", "review"))

    p2 = sub.add_parser("merge")
    p2.add_argument("--scores", nargs="+", required=True, help="1~3 份评审人打分 JSON")
    p2.add_argument("--map", default=os.path.join(PROJ, "output", "review", "code_map.json"))
    p2.add_argument("--out", default=os.path.join(PROJ, "output", "review"))

    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args.guidance_dir, args.out)
    else:
        results = merge(args.scores, args.map)
        _write_merge(results, args.out)


if __name__ == "__main__":
    main()
