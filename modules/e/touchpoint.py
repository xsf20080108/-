# -*- coding: utf-8 -*-
"""
触达与反馈回流（模块 e）

回答命题"可落地性"的三问：何时推、通过什么渠道、反馈如何回流。

1. 触达计划：结案后按时间轴生成多条消息（T+0 / T+1 / T+7 / T+30），
   每条锚定该用户的具体数字（住院天数、自费金额、术式、复查时间），非通用话术。
2. 反馈回流：用户点击 / 评分 / 追问写入 feedback.jsonl，
   聚合成"指引没说清的地方"，反向指导模块 c 的 prompt 迭代与模块 d 的画像权重。
"""

from __future__ import annotations

import os
import json
import datetime
from typing import List, Dict, Any, Optional

FEEDBACK_LOG = "feedback.jsonl"

# 触达节奏：offset_hours 为"结案后多久推送"
TOUCH_RULES = [
    {"offset_hours": 0, "channel": "APP", "scene": "结案即时",
     "purpose": "告知赔付结果与本次就医整理，降低不确定感"},
    {"offset_hours": 24, "channel": "企微", "scene": "温度指引",
     "purpose": "避开刚出院的情绪高峰，送上个性化康复指引"},
    {"offset_hours": 168, "channel": "短信", "scene": "康复随访",
     "purpose": "一周后确认恢复情况，提示复查与用药依从"},
    {"offset_hours": 720, "channel": "客服回访", "scene": "慢病/复诊提醒",
     "purpose": "30 天复诊提醒与下次就医推荐（带可解释理由）"},
]


def _v(f: Any) -> Any:
    if isinstance(f, dict) and "value" in f:
        return f["value"]
    return f


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _facts(record: Dict) -> Dict[str, Any]:
    """从结构化记录里取出可用于个性化文案的具体事实。"""
    enc = record.get("encounter", {}) or {}
    costs = record.get("costs", {}) or {}
    dias = record.get("diagnoses", []) or []
    procs = record.get("procedures", []) or []
    dis = record.get("discharge", {}) or {}
    return {
        "hospital": _v(enc.get("hospital", {})) or "本次就医医院",
        "department": _v(enc.get("department", {})) or "",
        "diagnosis": "、".join(str(_v(d.get("name", {})) or "") for d in dias[:2]) or "本次诊断",
        "procedure": "、".join(str(_v(p.get("name", {})) or "") for p in procs[:2]),
        "los": _v(enc.get("length_of_stay_days", {})),
        "total": _v(costs.get("total", {})),
        "self_paid": _v(costs.get("self_paid", {})),
        "insurance_paid": _v(costs.get("insurance_paid", {})),
        "follow_up": _v(dis.get("follow_up", {})) or "",
    }


def build_touchplan(record: Dict, guidance: Dict = None,
                    recommendation: Dict = None,
                    closed_at: str = None) -> List[Dict[str, Any]]:
    """生成触达计划（含每条消息的实际文案）。"""
    f = _facts(record)
    base = datetime.datetime.strptime(closed_at, "%Y-%m-%d %H:%M:%S") \
        if closed_at else datetime.datetime.now()

    los_txt = f"{f['los']} 天" if f["los"] else "本次住院"
    self_txt = f"{f['self_paid']} 元" if f["self_paid"] is not None else "—"
    total_txt = f"{f['total']} 元" if f["total"] is not None else "—"

    contents = {
        "结案即时": f"您的理赔已结案。本次在{f['hospital']}{f['department']}就诊，"
                    f"诊断 {f['diagnosis']}，住院 {los_txt}，总费用 {total_txt}，"
                    f"其中自费 {self_txt}。完整就医整理已生成，可随时查看。",
        "温度指引": (guidance or {}).get("greeting", "")
                    or f"这次{f['diagnosis']}的治疗辛苦了，我们把康复要点整理好了，按天照着做就行。",
        "康复随访": f"出院一周了，恢复得怎么样？{f['procedure'] + '术后' if f['procedure'] else ''}"
                    f"请注意伤口/症状变化，按医嘱用药。{f['follow_up'] or '如有不适及时复诊。'}",
        "慢病/复诊提醒": None,   # 下面按推荐结果动态生成
    }

    if recommendation:
        r = recommendation
        reasons = "；".join((r.get("reasons") or [])[:2])
        contents["慢病/复诊提醒"] = (
            f"到了复诊时间，建议回 {f['hospital']} 复查{f['diagnosis']}。"
            f"若需换院，可参考推荐：{r.get('hospital')}（综合分 {r.get('score')}）——{reasons}")
    else:
        contents["慢病/复诊提醒"] = f"到了复诊时间，建议回 {f['hospital']} 复查{f['diagnosis']}。"

    plan = []
    for rule in TOUCH_RULES:
        send_at = (base + datetime.timedelta(hours=rule["offset_hours"])).strftime("%Y-%m-%d %H:%M")
        plan.append({
            "offset_hours": rule["offset_hours"],
            "send_at": send_at,
            "channel": rule["channel"],
            "scene": rule["scene"],
            "purpose": rule["purpose"],
            "content": contents.get(rule["scene"], ""),
            "cited_facts": {k: f[k] for k in ("hospital", "diagnosis", "los", "total", "self_paid")
                            if f.get(k) is not None},
        })
    return plan


def save_feedback(claim_id: str, channel: str, feedback_type: str,
                  rating: int = None, text: str = "", out_dir: str = None) -> Dict:
    """记录一次用户反馈（点击 / 评分 / 追问 / 退订）。"""
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
    os.makedirs(out_dir, exist_ok=True)
    rec = {"ts": _now(), "claim_id": claim_id, "channel": channel,
           "type": feedback_type, "rating": rating, "text": text}
    with open(os.path.join(out_dir, FEEDBACK_LOG), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def feedback_insights(out_dir: str = None) -> Dict[str, Any]:
    """回流分析：把反馈聚合成可执行的优化信号。"""
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
    path = os.path.join(out_dir, FEEDBACK_LOG)
    if not os.path.exists(path):
        return {"n": 0, "avg_rating": None, "by_type": {}, "actions": ["暂无反馈数据"]}
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    ratings = [r["rating"] for r in rows if r.get("rating") is not None]
    by_type: Dict[str, int] = {}
    for r in rows:
        by_type[r.get("type", "未知")] = by_type.get(r.get("type", "未知"), 0) + 1
    avg = sum(ratings) / len(ratings) if ratings else None
    actions = []
    if avg is not None and avg < 4:
        actions.append("温度指引盲评低于 4/5：回到模块 c 强化个性化事实引用（住院天数/自费/术式）")
    if by_type.get("追问", 0) >= 3:
        actions.append("追问较多：指引缺少'费用解读/康复计划'细节，需在 prompt 中补对应要素")
    if by_type.get("退订", 0) >= 2:
        actions.append("出现退订：降低推送频率，改为 T+1 单条 + 用户主动拉取")
    if not actions:
        actions.append("反馈健康：维持当前触达节奏与指引模板")
    return {"n": len(rows), "avg_rating": round(avg, 2) if avg else None,
            "by_type": by_type, "actions": actions}
