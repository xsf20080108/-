# -*- coding: utf-8 -*-
"""
理赔材料数据源适配（模块 e · 数据入口）

作用：把官方 KR1 的「命题材料提取结果」JSON 转成模块 a 可直接抽取的文本列表，
      从而跳过 OCR 环节（材料已由 AI 中台提取为文本）。

产出结构与 modules.a.ocr 的输出一致：[{"source": 文件名, "text": 文本, "path": 路径}]
"""

from __future__ import annotations

import os
import json
import glob
from typing import List, Dict, Any


def kr1_root() -> str:
    """KR1 数据根目录。可用环境变量 KR1_DIR 覆盖；否则自本项目向上逐级查找。"""
    env = os.environ.get("KR1_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))      # .../project/modules/e
    cur = os.path.dirname(os.path.dirname(here))           # 项目根
    for _ in range(5):
        for rel in (os.path.join("kr1", "命题材料提取结果"), "kr1"):
            cand = os.path.join(cur, rel)
            if os.path.isdir(cand):
                return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.join(os.path.dirname(cur), "kr1", "命题材料提取结果")


def diagnose() -> str:
    """返回人类可读的数据可用性诊断与获取指引（CLI 与网页共用）。

    KR1 是官方命题提供的脱敏理赔医疗记录，体积大且涉及合规，**不随仓库分发**。
    缺数据时本模块静默降级（返回空列表），不影响其他两种入口模式。
    """
    root = kr1_root()
    head = f"KR1 数据目录：{root}\n"
    if os.path.isdir(root):
        return head + f"状态：已就绪，检出 {len(list_claims(root))} 个理赔案件。"
    return (
        head + "状态：未找到（该目录不存在）。\n\n"
        "KR1 是比赛官方命题提供的脱敏理赔医疗记录，因体积与合规原因不在仓库内。获取方式：\n"
        "  1. 从官方材料包解压出「命题材料提取结果」目录；\n"
        "  2. 放到项目根目录的 kr1/ 下，结构为  kr1/命题材料提取结果/CL0xxx/*.json；\n"
        "  3. 或用环境变量 KR1_DIR 指向任意位置：\n"
        "       Windows  set KR1_DIR=D:\\path\\to\\命题材料提取结果\n"
        "       macOS/Linux  export KR1_DIR=/path/to/命题材料提取结果\n\n"
        "缺少该数据不影响「手动上传」与「模拟结案事件」两种模式。"
    )


def list_claims(root: str = None) -> List[str]:
    """列出所有可处理的理赔案件号（目录名，如 CL0300004435037004）。"""
    root = root or kr1_root()
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))


def _text_of(doc: Dict[str, Any]) -> str:
    """单份材料 JSON → 纯文本（保留字段名，方便 LLM 定位与证据溯源）。"""
    fields = doc.get("fields") or {}
    lines = ["【%s】" % (doc.get("materialType") or "材料")]
    for k, val in fields.items():
        if val is None or val == "" or val == []:
            continue
        if k == "blocks":
            for b in val or []:
                for item in (b.get("costDetailList") or []):
                    lines.append("费用明细：%s | %s | 数量%s | 单价%s | 金额%s" % (
                        item.get("itemCategoryName") or "", item.get("detailName") or "",
                        item.get("quantity"), item.get("unit"), item.get("feeAmount")))
            continue
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False)
        lines.append("%s: %s" % (k, val))
    return "\n".join(lines)


def load_claim_texts(claim_id: str, root: str = None) -> List[Dict[str, str]]:
    """读取某个理赔案件的全部材料文本（按文件名排序，保证可复现）。"""
    d = os.path.join(root or kr1_root(), claim_id)
    docs: List[Dict[str, str]] = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            doc = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        t = _text_of(doc)
        if t.strip():
            docs.append({"source": os.path.basename(p), "text": t, "path": p})
    return docs


def claim_summary(claim_id: str, root: str = None) -> Dict[str, Any]:
    """从材料里直接读出的确定信息（不经 LLM），用于事件卡与合规展示。"""
    root = root or kr1_root()
    d = os.path.join(root, claim_id)
    hospitals, doctors, departments, dates, types = [], [], [], [], []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            doc = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        f = doc.get("fields") or {}
        types.append(doc.get("materialType") or "其他")
        for key, bucket in (("hospitalName", hospitals), ("hospital", hospitals),
                            ("doctorName", doctors), ("department", departments)):
            v = f.get(key)
            if isinstance(v, list):          # 有的材料里同一字段是列表
                for x in v:
                    if x and x not in bucket:
                        bucket.append(str(x))
                continue
            if v and v not in bucket:
                bucket.append(str(v))
        for key in ("visitDate", "admissionDate", "dischargeDate", "invoiceDate", "report_time"):
            v = f.get(key)
            if v and str(v) not in dates:
                dates.append(str(v))
    return {
        "claim_id": claim_id,
        "hospital": hospitals[0] if hospitals else None,
        "hospitals": hospitals,
        "doctors": doctors[:3],
        "departments": departments[:3],
        "dates": sorted(dates)[:10],
        "material_types": sorted({t for t in types}),
        "n_materials": len(types),
    }
