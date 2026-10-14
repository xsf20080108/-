# -*- coding: utf-8 -*-
"""
理赔后医护关怀 —— 网页入口

两种演示模式：
1. 手动上传病历图片 → 结构化(a) → 痛点(b) → 温度指引(c) → 品质画像(d)。
2. 模拟理赔结案事件（claim.closed）→ 触发信号判定 → 自动跑 a→b→c→d → 附带指引触达，
   体现"理赔触发"而非手动分析。

启动：streamlit run app.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

# 强制使用磁盘上的最新 web.* 代码：Streamlit 文件变化时只重跑本脚本，
# 已导入的 web.* 模块会被 Python 缓存（sys.modules），导致新旧代码混跑。
for _m in [k for k in list(sys.modules) if k == "web" or k.startswith("web.")]:
    del sys.modules[_m]

from web import runner, ui, styles, trigger

st.set_page_config(page_title="理赔后医护关怀", page_icon="🏥", layout="wide")
styles.inject_css(st)

# —— 顶部 ——
st.title("理赔后医护关怀")
st.caption("理赔结案事件自动触发，生成结构化结果、就医痛点与温度指引")

# key 检查
from modules.a.config import api_config
if not api_config()["api_key"]:
    st.warning("未检测到 API key（项目根目录 `.env`）。将退回纯规则模式，语义字段可能缺失。")

# —— 演示模式切换 ——
mode = st.radio(
    "演示模式",
    ["📎 手动上传病历图片", "⚡ 模拟理赔结案事件（事件触发）"],
    horizontal=True,
)

if mode == "📎 手动上传病历图片":
    # —— 上传区 ——
    if "reset_counter" not in st.session_state:
        st.session_state["reset_counter"] = 0
    rc = st.session_state["reset_counter"]

    uploaded = st.file_uploader(
        "上传病历图片（可多选 / 拖拽）",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"],
        key=f"uploaded_files_{rc}",
    )
    pid = st.text_input("病人 ID（可选，留空自动生成）", value="", key=f"pid_input_{rc}")

    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("开始分析", type="primary", disabled=not uploaded):
        with st.status("正在分析…", expanded=True) as status:
            def progress(msg):
                status.update(label=msg)
            try:
                result = runner.run_pipeline(uploaded, pid, progress=progress)
                st.session_state["result"] = result
                st.session_state["result_pid"] = result["pid"]
            except Exception as e:
                st.error(f"分析失败：{type(e).__name__}: {e}")

    if c2.button("清空上传", disabled=not (uploaded or "result" in st.session_state)):
        st.session_state.pop("result", None)
        st.session_state.pop("result_pid", None)
        st.session_state["reset_counter"] = rc + 1
        st.rerun()

else:
    # —— 模拟理赔结案事件 ——
    st.markdown("**理赔核心系统推送「claim.closed」结案事件 → 触发信号判定 → 引擎自动出结果。**")
    cases = trigger.load_cases()
    if not cases:
        st.info("未在 output/ 找到已结构化的真实案件，请先在手动模式跑一例，或确保 output/CL*.json 存在。")
    else:
        # 案件下拉选择
        options = {f"{c['case_id']} · {c['hospital']} · {'、'.join(c['diagnoses'][:2]) or '—'}": c
                   for c in cases}
        sel_label = st.selectbox("选择已结案的理赔案件（来自 70 案真实结构化结果）", list(options.keys()))
        sel = options[sel_label]

        if st.button("⚡ 模拟核赔通过 → 触发引擎", type="primary"):
            with st.status("处理理赔结案事件…", expanded=True) as status:
                try:
                    # 1) 读取已结构化 record，并校验确实是结构化病历
                    rec = json.load(open(sel["path"], encoding="utf-8"))
                    if "encounter" not in rec or "diagnoses" not in rec:
                        raise ValueError(
                            f"{sel['path']} 不是结构化病历（缺 encounter/diagnoses），"
                            "请重新选择案件，或重启 Streamlit 后重试")
                    # 2) 构造触发事件 + 信号判定
                    event = trigger.build_event(sel["case_id"], rec)
                    st.session_state["trigger_event"] = event
                    # 3) 复用 runner 从已有 record 跑 b→c（不再 OCR）
                    def progress(msg, **kw):
                        if isinstance(msg, str) and msg:
                            status.update(label=msg)
                    result = runner.run_from_record(sel["path"], progress=progress)
                    st.session_state["result"] = result
                    st.session_state["result_pid"] = result["pid"]
                    status.update(label="✅ 结案事件处理完成，指引已附带生成", state="complete")
                except Exception as e:
                    st.error(f"触发处理失败：{type(e).__name__}: {e}")

    # 展示触发事件与信号判定
    if "trigger_event" in st.session_state:
        ev = st.session_state["trigger_event"]
        st.markdown("#### 📨 理赔触发事件（claim.closed）")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**事件类型**：`{ev['event_type']}`  \n"
                        f"**案件号**：`{ev['case_id']}`  \n"
                        f"**发生时间**：`{ev['occurred_at']}`")
        with c2:
            st.markdown("**命中的触发信号**：")
            for s in ev["trigger_signals"]:
                st.markdown(f"- ✅ {s}")
        st.info(f"**触发原因**：{ev['trigger_reason']}")
        if ev.get("trigger_signals"):
            for sig, det in (ev.get("_details") or {}).items():
                st.caption(f"· {sig}：{det}")

# —— 结果区（两种模式共用） ——
if "result" in st.session_state:
    r = st.session_state["result"]
    st.success(f"分析完成（病人 {r['pid']}）")

    tabs = st.tabs(["📋 结构化结果", "⚠️ 就医痛点", "💌 温度指引", "🏥 品质画像", "💡 关怀增强"])
    with tabs[0]:
        ui.render_record(r["record"], st)
    with tabs[1]:
        ui.render_pain_points(r["pain_points"], st)
    with tabs[2]:
        ui.render_guidance(r["guidance"], st)
    with tabs[3]:
        ui.render_quality(st, record=r["record"])
    with tabs[4]:
        ui.render_enhance(st, enhance=r.get("enhance"))
