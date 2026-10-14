# -*- coding: utf-8 -*-
"""
理赔触发 CLI（合并自队友分支：案件直通 + 触达计划 + 反馈回流）

    # 列出可触发的理赔案件号（70 个 KR1 真实案件）
    python -m modules.e.trigger_cli --list

    # 触发单个案件（模拟"该案件刚刚结案"，跑 a→b→c→d→触达全链路）
    python -m modules.e.trigger_cli --claim CL0300004435037004

    # 批量消费结案事件队列
    python -m modules.e.trigger_cli --listen --limit 10

    # 纯规则模式（不调 LLM）
    python -m modules.e.trigger_cli --claim CL0300004435037004 --no-llm

    # 演示反馈回流洞察
    python -m modules.e.trigger_cli --feedback-demo
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.e import kr1_source, event as event_mod, pipeline, touchpoint


def cmd_list(limit: int = 20) -> None:
    claims = kr1_source.list_claims()
    if not claims:
        print(kr1_source.diagnose())
        return
    print("可触发理赔案件 %d 个（数据源：%s）\n" % (len(claims), kr1_source.kr1_root()))
    for c in claims[:limit]:
        s = kr1_source.claim_summary(c)
        print("  %s  材料%2d份  %s  %s" % (
            c, s["n_materials"], (s["hospital"] or "—")[:22],
            "/".join(s["material_types"][:3])))
    if len(claims) > limit:
        print("  ... 其余 %d 个" % (len(claims) - limit))


def cmd_trigger(claim_id: str, use_llm: bool = True) -> dict:
    ev = event_mod.from_manual(claim_id)
    print("收到理赔结案事件：%s" % ev.event_id)
    print("  案件号 %s | 来源 %s | 结案时间 %s | 材料 %d 份 | 用户 %s"
          % (ev.claim_id, ev.source_system, ev.closed_at, len(ev.materials), ev.patient_ref))

    def progress(m):
        print("  " + m)

    res = pipeline.handle_claim_closed(ev, use_llm=use_llm, progress=progress)

    print("\n—— 处理结果 ——")
    print("关键字段缺失率：%.4f" % res["missing_rate"])
    print("痛点 %d 个：%s" % (len(res["pain_points"]),
                            "、".join(p["name"] for p in res["pain_points"][:5]) or "无"))
    if res["personalization"]:
        print("个性化自检：引用事实 %s/%s" % (res["personalization"]["cited_count"],
                                       res["personalization"]["total_facts"]))
    if res["recommendation"]:
        r = res["recommendation"]
        print("就医推荐：%s（综合分 %s）" % (r["hospital"], r["score"]))
        for x in r["reasons"][:2]:
            print("  · " + x)
    print("\n—— 触达计划（%d 条）——" % len(res["touchplan"]))
    for t in res["touchplan"]:
        print("[T+%dh %s] %s｜%s" % (t["offset_hours"], t["send_at"], t["channel"], t["scene"]))
        print("    %s" % t["content"][:110])
    print("\n输出目录：%s" % res["out_dir"])
    return res


def cmd_listen(limit: int = None, use_llm: bool = True) -> None:
    evs = event_mod.scan_closed_claims(limit=limit)
    print("事件队列：%d 条结案事件待处理\n" % len(evs))
    ok = 0
    for i, ev in enumerate(evs, 1):
        print("[%d/%d] %s" % (i, len(evs), ev.claim_id))
        try:
            pipeline.handle_claim_closed(ev, use_llm=use_llm, progress=lambda m: None)
            ok += 1
        except Exception as e:
            print("  失败：%s: %s" % (type(e).__name__, str(e)[:100]))
    print("\n处理完成 %d/%d" % (ok, len(evs)))


def cmd_feedback_demo() -> None:
    claims = kr1_source.list_claims()[:2]
    for c in claims:
        touchpoint.save_feedback(c, "企微", "评分", rating=5, text="指引很清楚")
        touchpoint.save_feedback(c, "APP", "追问", text="自费部分还能再解释下吗")
    print(json.dumps(touchpoint.feedback_insights(), ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="模块 e 理赔触发与触达")
    ap.add_argument("--list", action="store_true", help="列出可触发的理赔案件号")
    ap.add_argument("--claim", default=None, help="触发指定理赔案件号")
    ap.add_argument("--listen", action="store_true", help="批量消费结案事件队列")
    ap.add_argument("--limit", type=int, default=None, help="批量处理条数上限")
    ap.add_argument("--no-llm", action="store_true", help="纯规则模式（不调 LLM）")
    ap.add_argument("--feedback-demo", action="store_true", help="演示反馈回流")
    args = ap.parse_args()

    if args.list:
        cmd_list()
    elif args.claim:
        cmd_trigger(args.claim, use_llm=not args.no_llm)
    elif args.listen:
        cmd_listen(limit=args.limit, use_llm=not args.no_llm)
    elif args.feedback_demo:
        cmd_feedback_demo()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
