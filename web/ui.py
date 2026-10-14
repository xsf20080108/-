# -*- coding: utf-8 -*-
"""网页渲染：把结构化/痛点/指引/画像渲染成简洁优美的界面。"""
import os
import json
import html

from .styles import SEVERITY_COLOR, CATEGORY_COLOR


def val(f):
    """从 Field 包装 {"value": ...} 或裸值取值。"""
    return f.get("value") if isinstance(f, dict) and "value" in f else f


def fmt(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _esc(s):
    return html.escape(str(s)) if s is not None else ""


# ---------------------------------------------------------------------------
# 结构化结果
# ---------------------------------------------------------------------------

def render_record(rec, st):
    p = rec.get("patient", {})
    e = rec.get("encounter", {})
    c = rec.get("costs", {})

    name = val(p.get("name")) or "—"
    gender = val(p.get("gender")) or ""
    age = fmt(val(p.get("age")))
    hosp = val(e.get("hospital")) or "—"
    dept = val(e.get("department")) or "—"
    etype = val(e.get("type")) or "—"
    adm = val(e.get("admission_time"))
    dis = val(e.get("discharge_time"))
    los = val(e.get("length_of_stay_days"))
    visit = val(e.get("visit_time"))
    diagnoses = "、".join(val(d.get("name")) for d in rec.get("diagnoses", []) or [] if val(d.get("name")))
    procedures = "、".join(val(x.get("name")) for x in rec.get("procedures", []) or [] if val(x.get("name")))
    total = val(c.get("total"))
    ins = val(c.get("insurance_paid"))
    selfp = val(c.get("self_paid"))
    meds = rec.get("medications", []) or []

    # 时间信息（住院 vs 门诊）
    if adm or dis:
        time_txt = f"{fmt(adm)} → {fmt(dis)}（住院 {fmt(los)} 天）"
    else:
        time_txt = f"就诊时间 {fmt(visit)}"

    rows = [
        ("患者", f"{name}（{gender}，{age} 岁）"),
        ("医院 · 科室", f"{hosp} · {dept}"),
        ("就诊类型", etype),
        ("时间", time_txt),
        ("诊断", diagnoses or "—"),
        ("手术/操作", procedures or "—"),
        ("用药", f"{len(meds)} 种"),
        ("总费用", f"{fmt(total)} 元" if total is not None else "—"),
        ("医保 / 自费", f"{fmt(ins)} / {fmt(selfp)} 元"),
    ]
    md = "| 项目 | 内容 |\n|---|---|\n"
    for k, v in rows:
        md += f"| **{k}** | {_esc(v)} |\n"
    st.markdown(md)

    with st.expander("查看完整结构化 JSON"):
        st.json(rec)


# ---------------------------------------------------------------------------
# 就医痛点
# ---------------------------------------------------------------------------

def render_pain_points(pp, st):
    # 兼容两种形态：{"pain_points": [...]} 或直接 [...]
    if isinstance(pp, list):
        pps = pp
    else:
        pps = (pp or {}).get("pain_points", []) or []
    if not pps:
        st.success("未识别出明显痛点 🎉")
        return
    for p in pps:
        sev = p.get("severity", "中")
        cat = p.get("category", "")
        color = SEVERITY_COLOR.get(sev, "#718096")
        catcolor = CATEGORY_COLOR.get(cat, "#4a5568")
        name = _esc(p.get("name"))
        reason = _esc(p.get("reason"))
        ev = p.get("evidence", []) or []
        ev_html = "".join(
            f'<div class="pp-evidence">· {_esc(e.get("source"))}：{_esc(str(e.get("snippet"))[:100])}</div>'
            for e in ev[:2]
        )
        st.markdown(
            f'<div class="pp-card" style="border-left-color:{color}">'
            f'<span class="badge" style="background:{color}">{_esc(sev)}</span>'
            f'<span class="badge-cat" style="color:{catcolor};border:1px solid {catcolor}">{_esc(cat)}</span>'
            f'<strong>{name}</strong>'
            f'<div class="pp-reason">{reason}</div>{ev_html}</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# 温度指引
# ---------------------------------------------------------------------------

def render_guidance(g, st):
    if not g:                      # 指引生成失败时兜底，不让整页崩掉
        st.info("温度指引暂未生成（LLM 异常时可重试，或查看结构化结果与痛点）")
        return
    title = _esc(g.get("title") or "个性化健康指引")
    greeting = _esc(g.get("greeting") or "")
    sections = g.get("sections", []) or []

    parts = [f'<h2 style="margin:0 0 0.4rem 0;color:#2b6cb0">💌 {title}</h2>']
    if greeting:
        parts.append(f'<p style="margin:0 0 1rem 0;font-size:1.05rem">{greeting}</p>')
    for s in sections:
        t = _esc(s.get("title", ""))
        content = s.get("content", "") or ""
        content_html = _esc(content).replace("\n", "<br>")
        parts.append(f'<h3>{t}</h3><p style="margin:0 0 0.8rem 0">{content_html}</p>')

    st.markdown('<div class="guidance-card">' + "".join(parts) + "</div>", unsafe_allow_html=True)

    cited = g.get("cited_facts", []) or []
    if cited:
        st.caption("已引用的个性化事实：" + "、".join(_esc(x) for x in cited[:12]))


# ---------------------------------------------------------------------------
# 品质画像与推荐
# ---------------------------------------------------------------------------

def _render_profile_table(prows, st):
    table = []
    for p in prows:
        m = {x["dimension"]: x for x in p["dimensions"]}
        table.append({
            "机构": p["hospital"] + ("（含真实病例）" if not p.get("synthetic") else ""),
            "样本": p["n_cases"],
            "均次费用(元)": m["cost_efficiency"]["value"],
            "住院天数": m["length_of_stay"]["value"],
            "自费比例": f"{m['self_pay_ratio']['value']:.0f}%",
            "用药合理率": f"{m['medication_reasonableness']['value']:.0f}%",
            "治疗规范率": f"{m['treatment_standard']['value']:.0f}%",
            "再入院率": f"{m['readmission_rate']['value']:.0f}%",
            "满意度": m["satisfaction"]["value"],
        })
    st.dataframe(table, use_container_width=True, hide_index=True)


def _render_hospital_search(st, hospital, disease, prefix="lib"):
    """给某机构加一个「搜索公开评价」按钮，点按后抓取公开网络信息。

    prefix 用于区分不同区块（cur=当前病例 / lib=画像库），避免同一医院+病种
    在两处各生成一个同 key 按钮导致 Streamlit 报重复 key。
    """
    from web.search import web_search, llm_evaluate
    q = f"{hospital} {disease} 评价 口碑 专家"
    key = (f"{prefix}_search_{disease}_{hospital}").replace(" ", "_").replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    if st.button(f"🔍 搜索「{_esc(hospital)}」公开评价", key=key):
        with st.spinner("正在获取评价（AI 知识 + 网络）…"):
            eval_text = llm_evaluate(hospital, disease)   # 主：AI 知识评价
            rs = web_search(q, max_results=5)             # 辅：网络链接
        if eval_text:
            st.markdown("**AI 评价**（基于公开知识，可能过时，仅供参考）")
            st.markdown(eval_text)
        if rs:
            st.markdown("**相关网络链接**：")
            for r in rs:
                st.markdown(f"- **[{_esc(r['title'])}]({_esc(r['url'])})**\n  {_esc(r['snippet'])}")
        if not eval_text and not rs:
            st.info("未找到相关公开信息（网络受限，或该机构公开信息较少）。")


def render_quality(st, record=None):
    # —— 1. 当前上传病例的品质指标（单病例画像，实时计算）——
    if record:
        from modules.d.aggregate import build_profiles
        profs = build_profiles([record])
        if profs:
            p = profs[0].model_dump()   # Pydantic 对象转 dict
            st.subheader(f"当前病例：{p['disease']}")
            st.caption(f"{p['hospital']} · 共 {p['n_cases']} 例")
            _render_profile_table([p], st)
            _render_hospital_search(st, p["hospital"], p["disease"], prefix="cur")
            st.info("上表为当前上传病例的单病例画像。点下方按钮可把它纳入跨病历画像库，"
                    "与同病种其它机构对比（其余机构为合成估算，已标注）。")

    # —— 2. 跨病历画像库（机构×病种，含合成）——
    with st.expander("跨病历品质画像库（机构 × 病种）", expanded=False):
        if st.button("生成 / 刷新画像库"):
            from modules.d.run import run as run_d
            with st.spinner("聚合中…"):
                run_d()
            st.rerun()

        json_path = "output/quality_profiles.json"
        if not os.path.exists(json_path):
            st.info("尚未生成画像库，点上方按钮生成。")
            return

        rep = json.load(open(json_path, encoding="utf-8"))
        profiles = rep.get("profiles", [])
        recs = rep.get("recommendations", [])

        for disease in sorted({p["disease"] for p in profiles}):
            st.markdown(f"**病种：{disease}**")
            _render_profile_table([p for p in profiles if p["disease"] == disease], st)
            for r in [r for r in recs if r["disease"] == disease]:
                st.markdown(f"**#{r['rank']} 推荐：{_esc(r['hospital'])}**（综合分 {r['score']}）")
                for reason in r["reasons"]:
                    st.markdown(f"- {_esc(reason)}")
                _render_hospital_search(st, r["hospital"], r["disease"], prefix="lib")


def render_enhance(st, enhance=None):
    """渲染模块 e 理赔关怀增强（预判式 FAQ + 触达 + 增值服务 + 闭环 KPI）。"""
    if not enhance:
        st.info("暂无关怀增强结果。")
        return

    # —— 1. 预判式 FAQ ——
    st.subheader("💡 预判式 FAQ（您可能还想了解）")
    faqs = enhance.get("predictive_faqs", [])
    if not faqs:
        st.caption("（根据病历特征，暂未触发预判问题）")
    for f in faqs:
        badge = "✅ 有据可依" if f.get("grounded") else "⚠️ 需人工确认"
        st.markdown(f"**Q：{_esc(f['question'])}**  `{badge}`")
        st.markdown(f"<div style='margin-left:16px;color:#555'>A：{_esc(f['answer'])}</div>",
                    unsafe_allow_html=True)
        st.caption(f"· 为什么问这个：{_esc(f['why'])}")
        st.markdown("")

    # —— 2. 触达渠道 ——
    st.subheader("📣 触达渠道建议")
    ch = enhance.get("channel", {})
    if ch:
        st.markdown(f"**推荐渠道**：{_esc(ch.get('channel', ''))}")
        st.caption(f"理由：{_esc(ch.get('reason', ''))}")
        dist = ch.get("dist", {})
        if dist:
            st.caption("服务类型分布：企微 65.6% / 呼入 33.6% / 在线 0.8%（来自 1.2 万条真实会话统计）")

    # —— 3. 增值服务 ——
    st.subheader("🎁 增值服务推荐")
    svcs = enhance.get("value_added_services", [])
    if not svcs:
        st.caption("（根据病种暂未匹配到增值服务）")
    for s in svcs:
        st.markdown(f"- 匹配「{_esc(s['match'])}」→ **{'、'.join(s['services'])}**")

    # —— 4. 闭环 KPI ——
    st.subheader("📈 闭环 KPI（可度量验证）")
    kpi = enhance.get("closed_loop_kpi", {})
    if kpi:
        st.markdown(f"- **指标**：{_esc(kpi.get('metric', ''))}")
        st.markdown(f"- **公式**：`{_esc(kpi.get('formula', ''))}`")
        st.markdown(f"- **目标**：{_esc(kpi.get('target', ''))}")
        topics = kpi.get("topics_tracked", [])
        if topics:
            st.caption(f"追踪主题：{'、'.join(topics)}")



# ---------------------------------------------------------------------------
# 触达计划时间轴（理赔触发链路，合并自队友分支）
# ---------------------------------------------------------------------------

def render_touchplan(touchplan, st, event=None):
    """渲染理赔触发后的触达计划：何时推、推什么、什么渠道。"""
    if event:
        st.caption(f"结案事件 {_esc(event.get('event_id'))} ｜ 案件号 {_esc(event.get('claim_id'))} "
                   f"｜ 来源 {_esc(event.get('source_system'))} ｜ 结案时间 {_esc(event.get('closed_at'))}")
    if not touchplan:
        st.info("暂无触达计划")
        return
    for t in touchplan:
        label = f"T+{t['offset_hours']}h · {t['send_at']} ｜ {t['channel']} ｜ {t['scene']}"
        with st.expander(label, expanded=(t["offset_hours"] == 24)):
            st.caption(_esc(t["purpose"]))
            st.markdown(_esc(t["content"]))
            facts = t.get("cited_facts") or {}
            if facts:
                st.caption("引用事实：" + "、".join(f"{k}={v}" for k, v in facts.items()))
    st.markdown("---")
    st.caption("反馈回流：用户点击 / 评分 / 追问会写入 output/feedback.jsonl，"
               "用于迭代温度指引 prompt 与画像权重。")
