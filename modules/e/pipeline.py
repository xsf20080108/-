# -*- coding: utf-8 -*-
"""
端到端链路编排（模块 e · 核心）

理赔结案事件到达 → 病历结构化(a) → 痛点识别(b) → 温度指引(c) → 品质画像与推荐(d) → 触达计划(e)

与网页版 runner 的区别：
  · 输入是"理赔案件号"而不是人工上传的图片（材料来自官方提取结果，跳过 OCR）
  · 输出多出"触达计划"，并写入审计日志与反馈通道（对应命题可落地性要求）
"""

from __future__ import annotations

import os
import sys
import json
import glob
from typing import Dict, Any, List, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.a import extract, confidence
from modules.a.run import build_rules_record
from modules.b.detect import detect_all
from modules.b.schema import PainPointReport
from modules.c.generate import generate_guidance, personalization_check
from modules.d import aggregate, recommend

from . import kr1_source, event as event_mod, touchpoint


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _out_dir(out_dir: str = None) -> str:
    return out_dir or os.path.join(_project_root(), "output")


def _load_json(p: str) -> Dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _real_records(out_dir: str, exclude: str = None) -> List[Dict]:
    """收集 output/ 下所有真实病例记录（排除合成数据与本模块产物）。"""
    recs = []
    for p in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        name = os.path.basename(p)
        if name.startswith(("quality_profiles", "audit", "feedback")):
            continue
        if name.endswith((".pain_points.json", ".guidance.json", ".scores.json")):
            continue
        if exclude and name == exclude:
            continue
        try:
            recs.append(_load_json(p))
        except Exception:
            continue
    return [r for r in recs if not r.get("synthetic")]


def handle_claim_closed(ev: "event_mod.ClaimClosedEvent",
                        out_dir: str = None,
                        use_llm: bool = True,
                        with_quality: bool = True,
                        progress: Callable[[str], None] = lambda m: None) -> Dict[str, Any]:
    """消费一条理赔结案事件，跑完整链路，返回结果包。"""
    out_dir = _out_dir(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    pid = ev.claim_id
    event_mod.audit(ev, "RECEIVED", {"source_system": ev.source_system}, out_dir=out_dir)

    # ——① 结构化（材料已是文本，跳过 OCR）——
    progress("① 病历结构化（理赔材料 %d 份）…" % len(ev.materials))
    texts = kr1_source.load_claim_texts(pid)
    if not texts:
        raise RuntimeError("案件 %s 未找到材料" % pid)
    try:
        if use_llm:
            rec = extract.extract_structured(texts, patient_id=pid)
        else:
            raise RuntimeError("force rules")
    except Exception as e:
        progress("⚠ LLM 抽取失败，退回纯规则模式：%s" % type(e).__name__)
        rec = build_rules_record(pid, texts)
    rec = confidence.recompute_confidence(rec)
    rec_path = os.path.join(out_dir, "%s.json" % pid)
    with open(rec_path, "w", encoding="utf-8") as f:
        f.write(rec.model_dump_json(indent=2, exclude_none=False))
    record = _load_json(rec_path)
    mr = confidence.missing_rate(rec)

    # ——② 痛点识别——
    progress("② 就医痛点识别…")
    try:
        pps = detect_all(record, use_llm=use_llm)
    except Exception as e:
        progress("⚠ 痛点识别异常：%s" % type(e).__name__)
        pps = []
    pp_report = PainPointReport(patient_id=pid, pain_points=pps)
    pp_path = os.path.join(out_dir, "%s.pain_points.json" % pid)
    with open(pp_path, "w", encoding="utf-8") as f:
        f.write(pp_report.model_dump_json(indent=2))

    # ——③ 温度指引——
    progress("③ 温度指引生成…")
    try:
        g = generate_guidance(record, [p.model_dump() for p in pps])
        chk = personalization_check(record, g)
    except Exception as e:
        progress("⚠ 指引生成失败：%s" % type(e).__name__)
        g, chk = None, {"cited_count": 0, "total_facts": 0, "personalization_score": 0}
    g_path = os.path.join(out_dir, "%s.guidance.json" % pid)
    if g:
        with open(g_path, "w", encoding="utf-8") as f:
            f.write(g.model_dump_json(indent=2))
        with open(g_path.replace(".json", ".md"), "w", encoding="utf-8") as f:
            f.write(g.full_text)

    # ——④ 品质画像与推荐（纯真实数据聚合）——
    recommendation = None
    profiles = []
    if with_quality:
        progress("④ 品质画像与可解释推荐（真实病例聚合）…")
        try:
            all_recs = _real_records(out_dir)
            profiles = aggregate.build_profiles(all_recs)
            recs = recommend.recommend_all(profiles, top_k=3)
            disease = aggregate._primary_disease(record)
            for r in recs:
                if r.disease == disease and r.rank == 1:
                    recommendation = r.model_dump()
                    break
            with open(os.path.join(out_dir, "quality_profiles.json"), "w", encoding="utf-8") as f:
                json.dump({"profiles": [p.model_dump() for p in profiles],
                           "recommendations": [r.model_dump() for r in recs]},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            progress("⚠ 画像聚合失败：%s" % type(e).__name__)

    # ——⑤ 触达计划——
    progress("⑤ 生成触达计划…")
    plan = touchpoint.build_touchplan(
        record,
        guidance=g.model_dump() if g else None,
        recommendation=recommendation,
        closed_at=ev.closed_at,
    )

    event_mod.audit(ev, "COMPLETED",
                    {"missing_rate": round(mr, 4), "n_pain_points": len(pps),
                     "n_touchpoints": len(plan)}, out_dir=out_dir)

    return {
        "event": ev.to_dict(),
        "pid": pid,
        "record": record,
        "missing_rate": mr,
        "pain_points": [p.model_dump() for p in pps],
        "guidance": g.model_dump() if g else None,
        "personalization": chk,
        "recommendation": recommendation,
        "n_profiles": len(profiles),
        "touchplan": plan,
        "out_dir": out_dir,
    }
