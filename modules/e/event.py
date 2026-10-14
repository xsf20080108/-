# -*- coding: utf-8 -*-
"""
理赔结案事件（模块 e · 触发源）

真实环境中：理赔审核系统在案件结案（审核通过/赔付完成）时推送事件，
携带 理赔号 + 用户标识 + 材料清单，通过消息队列 / 接口回调 / 定时批 到达本引擎。
这里用 ClaimClosedEvent 统一建模，并提供三种事件源：

    from_manual(claim_id)      手动指定一个案件（演示用）
    scan_closed_claims()       扫描数据源，把所有案件当作"已结案"批量入队（原型用）
    from_queue_file(path)      从 JSON 队列文件读取（对接模拟，字段与线上一致）

每个事件处理前后都会写审计日志（合规要求：记录谁在何时调用了哪份记录）。
"""

from __future__ import annotations

import os
import json
import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

from . import kr1_source

AUDIT_LOG = "audit_log.jsonl"


@dataclass
class ClaimClosedEvent:
    """理赔结案事件 —— 引擎的唯一入口。"""
    claim_id: str                # 理赔案件号（如 CL0300004435037004）
    event_id: str                # 事件唯一 ID
    event_type: str = "CLAIM_CLOSED"
    source_system: str = "理赔审核系统"
    closed_at: str = ""          # 结案时间
    patient_ref: str = ""        # 用户标识（线上为 oneid / account_id）
    materials: List[str] = field(default_factory=list)   # 材料清单（文件名）
    hospital: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _eid(claim_id: str) -> str:
    return "EVT-%s-%s" % (claim_id[-6:], datetime.datetime.now().strftime("%H%M%S"))


def from_manual(claim_id: str, patient_ref: str = "") -> ClaimClosedEvent:
    """手动触发：模拟"某个案件刚刚结案"。"""
    s = kr1_source.claim_summary(claim_id)
    texts = kr1_source.load_claim_texts(claim_id)
    return ClaimClosedEvent(
        claim_id=claim_id,
        event_id=_eid(claim_id),
        closed_at=_now(),
        patient_ref=patient_ref or ("oneid:" + claim_id[-8:]),
        materials=[t["source"] for t in texts],
        hospital=s.get("hospital"),
        raw={"n_materials": len(texts), "material_types": s.get("material_types", [])},
    )


def scan_closed_claims(limit: int = None) -> List[ClaimClosedEvent]:
    """批量：把数据源中所有案件视为已结案，生成事件队列。"""
    claims = kr1_source.list_claims()
    if limit:
        claims = claims[:limit]
    events = []
    for c in claims:
        s = kr1_source.claim_summary(c)
        events.append(ClaimClosedEvent(
            claim_id=c,
            event_id=_eid(c),
            closed_at=_now(),
            patient_ref="oneid:" + c[-8:],
            materials=[t["source"] for t in kr1_source.load_claim_texts(c)],
            hospital=s.get("hospital"),
            raw={"n_materials": s.get("n_materials", 0)},
        ))
    return events


def from_queue_file(path: str) -> List[ClaimClosedEvent]:
    """从队列文件读取事件（字段与线上推送保持一致，便于后续替换为真实 MQ）。"""
    items = json.load(open(path, encoding="utf-8"))
    out = []
    for it in items:
        out.append(ClaimClosedEvent(
            claim_id=it["claim_id"],
            event_id=it.get("event_id") or _eid(it["claim_id"]),
            closed_at=it.get("closed_at") or _now(),
            patient_ref=it.get("patient_ref", ""),
            materials=it.get("materials", []),
            hospital=it.get("hospital"),
            raw=it,
        ))
    return out


def audit(event: ClaimClosedEvent, action: str, detail: Dict[str, Any] = None,
          out_dir: str = None) -> None:
    """合规审计留痕：记录谁在何时、因何事件、调用了哪份记录。"""
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
    os.makedirs(out_dir, exist_ok=True)
    rec = {"ts": _now(), "action": action, "event_id": event.event_id,
           "claim_id": event.claim_id, "patient_ref": event.patient_ref,
           "n_materials": len(event.materials)}
    if detail:
        rec.update(detail)
    with open(os.path.join(out_dir, AUDIT_LOG), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
