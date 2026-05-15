"""Page 1 — Topic & Items.

Two stages, on tabs:
  1. Topic spec     -- load the bundled fractions spec, upload a custom
                       one, or edit JSON inline.
  2. Item bank      -- generate (LLM agent loop), validate, or upload a
                       prebuilt bank. Preview items.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.state import (
    init_state, require_ws, save_ws, header, BUNDLED,
)
from lib.runners import (
    run_bank_generator, run_validate_bank, ollama_reachable,
)


st.set_page_config(page_title="Topic & Items — mehuwo", page_icon="📐",
                   layout="wide")
init_state()
w = require_ws()


header(
    "Topic & Items",
    eyebrow="step 1 of 5",
    description=(
        "Set the skill graph for the unit you want to assess, then have "
        "the LLM author a multiple-choice item bank against it. Every "
        "item is verified arithmetically before it's accepted into the bank."
    ),
)


tab_spec, tab_bank = st.tabs(["1 · Topic spec", "2 · Item bank"])


# =========================================================================
# TAB 1: Topic spec
# =========================================================================

with tab_spec:
    st.markdown(
        '<div class="eyebrow">topic specification</div>'
        '<h3 style="margin-top:0">Choose or upload a topic spec</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="lead">A topic spec is a small JSON file that lists the '
        "skills in the unit, their prerequisite edges, and the cultural "
        "context the LLM should use when writing items.</p>",
        unsafe_allow_html=True,
    )

    cur = w.topic_spec
    if cur:
        st.markdown(
            f'<span class="pill done">current spec</span> '
            f'<span class="kbd">{Path(cur).name}</span>',
            unsafe_allow_html=True,
        )

    col_a, col_b = st.columns([1, 1], gap="large")

    with col_a:
        st.markdown("**Use the bundled Fractions spec**")
        st.caption(
            "10-skill Fractions unit (B.S. 4–6 / JHS) with Ghanaian "
            "cultural context already configured."
        )
        if st.button("Use bundled spec", use_container_width=True,
                     disabled=not BUNDLED["topic_spec"].exists()):
            dest = w.root / "topic_spec.json"
            dest.write_text(BUNDLED["topic_spec"].read_text())
            w.topic_spec = dest
            save_ws()
            st.success(f"Loaded {dest.name}")
            st.rerun()

    with col_b:
        st.markdown("**Upload your own**")
        st.caption("JSON file following the same schema as the bundled spec.")
        uploaded = st.file_uploader(
            "Topic spec JSON", type=["json"], label_visibility="collapsed",
        )
        if uploaded is not None:
            dest = w.root / "topic_spec.json"
            dest.write_bytes(uploaded.getvalue())
            w.topic_spec = dest
            save_ws()
            st.success(f"Saved as {dest.name}")
            st.rerun()

    # Preview / inline edit ------------------------------------------------
    if w.topic_spec and Path(w.topic_spec).exists():
        st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
        st.markdown(
            '<div class="eyebrow">preview</div>'
            '<h3 style="margin-top:0">Current topic spec</h3>',
            unsafe_allow_html=True,
        )
        spec = json.loads(Path(w.topic_spec).read_text())

        col1, col2, col3 = st.columns(3)
        col1.metric("Subject", spec.get("subject", "—"))
        col2.metric("Skills", len(spec.get("node_order", [])))
        col3.metric(
            "Prerequisite edges",
            sum(len(v) for v in spec.get("prerequisites", {}).values()),
        )

        with st.expander("Skill graph (textual)", expanded=False):
            for s in spec.get("node_order", []):
                desc = spec.get("skill_descriptions", {}).get(s, "")
                parents = spec.get("prerequisites", {}).get(s, [])
                p_str = f"  ← {', '.join(parents)}" if parents else ""
                st.markdown(f"- **{s}**{p_str}  \n  <span class='muted' "
                            f"style='font-size:0.85rem'>{desc}</span>",
                            unsafe_allow_html=True)

        with st.expander("Raw JSON", expanded=False):
            st.code(json.dumps(spec, indent=2, ensure_ascii=False),
                    language="json")


# =========================================================================
# TAB 2: Item bank
# =========================================================================

with tab_bank:
    st.markdown(
        '<div class="eyebrow">item bank</div>'
        '<h3 style="margin-top:0">Build the diagnostic item bank</h3>',
        unsafe_allow_html=True,
    )

    if not w.topic_spec or not Path(w.topic_spec).exists():
        st.warning("Choose a topic spec on the first tab before generating "
                   "a bank.")
        st.stop()

    cur = w.bank
    if cur and Path(cur).exists():
        try:
            bank = json.loads(Path(cur).read_text())
            n_single = len(bank.get("single_skill_items", []))
            n_multi = len(bank.get("multi_skill_items", []))
            st.markdown(
                f'<span class="pill done">current bank</span> '
                f'<span class="kbd">{Path(cur).name}</span> · '
                f'{n_single} single-skill, {n_multi} multi-skill items',
                unsafe_allow_html=True,
            )
        except Exception:
            pass

    # Three ways to get a bank: generate, upload, use bundled --------------
    method = st.radio(
        "How would you like to get an item bank?",
        ["Use the bundled bank", "Generate with the LLM", "Upload my own"],
        horizontal=True, label_visibility="collapsed",
    )

    # --- Bundled -----------------------------------------------------
    if method == "Use the bundled bank":
        st.caption(
            "20 single-skill + 6 multi-skill items, already "
            "arithmetic-verified, matching the bundled Fractions spec."
        )
        if st.button("Use bundled bank", type="primary",
                     disabled=not BUNDLED["bank"].exists()):
            dest = w.root / "bank.json"
            dest.write_text(BUNDLED["bank"].read_text())
            w.bank = dest
            save_ws()
            st.success(f"Loaded {dest.name}")
            st.rerun()

    # --- Upload ------------------------------------------------------
    elif method == "Upload my own":
        uploaded = st.file_uploader("Bank JSON", type=["json"])
        if uploaded is not None:
            dest = w.root / "bank.json"
            dest.write_bytes(uploaded.getvalue())
            w.bank = dest
            save_ws()
            st.success(f"Saved as {dest.name}")
            st.rerun()

    # --- Generate ----------------------------------------------------
    else:
        st.markdown(
            '<div class="card"><h4>About this step</h4>'
            '<div class="muted" style="font-size:0.88rem">The agent runs '
            "a tool-use loop against your local LLM. For each item it "
            "writes, the arithmetic verifier evaluates the symbolic "
            "solution and compares to the marked correct option — items "
            "with mismatched math are rejected and the model is asked to "
            "fix them. This typically takes "
            "<b>10–30 minutes</b> on a recent laptop CPU for the default "
            "targets (2 pure items per skill plus 6 multi-skill items)."
            "</div></div>",
            unsafe_allow_html=True,
        )

        # Quick Ollama health check
        ok, detail = ollama_reachable(st.session_state["ollama_host"])
        if not ok:
            st.error(
                "Ollama is not reachable. Start the daemon "
                f"(`ollama serve`) before generating. Detail: {detail}"
            )
            st.stop()

        c1, c2, c3 = st.columns(3)
        with c1:
            target_per_skill = st.number_input(
                "Pure items per skill", min_value=1, max_value=6, value=2,
            )
        with c2:
            target_multi = st.number_input(
                "Multi-skill items", min_value=0, max_value=20, value=6,
            )
        with c3:
            max_iters = st.number_input(
                "Max iterations", min_value=20, max_value=800, value=200,
            )

        c4, c5 = st.columns(2)
        with c4:
            model = st.text_input("Model", value=st.session_state["ollama_model"])
        with c5:
            thinking = st.selectbox(
                "Thinking mode",
                options=["default", "force on", "force off"],
                help=(
                    "Gemma 4's <|think|> mode is more accurate at math but "
                    "much slower on weak hardware. Try 'force off' first; "
                    "flip to 'force on' if you see arithmetic_mismatch errors."
                ),
            )
        thinking_arg = {"default": None, "force on": True,
                        "force off": False}[thinking]

        if st.button("Generate bank", type="primary"):
            dest = w.root / "bank.json"
            log_area = st.empty()
            progress = st.empty()
            status = st.empty()
            lines: list[str] = []
            tools_seen = {"add_ok": 0, "add_fail": 0,
                          "coverage": 0, "verify": 0}

            status.info("Starting agent loop…")
            t0 = time.time()
            for stream, line in run_bank_generator(
                spec=Path(w.topic_spec),
                output=dest,
                target_per_skill=int(target_per_skill),
                target_multi=int(target_multi),
                max_iterations=int(max_iters),
                model=model.strip() or None,
                host=st.session_state["ollama_host"],
                thinking=thinking_arg,
            ):
                if stream == "exit":
                    rc = int(line)
                    elapsed = time.time() - t0
                    if rc == 0:
                        status.success(
                            f"Bank written to `{dest}` in {elapsed:.0f}s."
                        )
                    else:
                        status.error(
                            f"Generator exited with code {rc} after "
                            f"{elapsed:.0f}s. See log."
                        )
                    break

                # Surface the per-item progress lines from on_call
                if line.startswith("  + "):
                    tools_seen["add_ok"] += 1
                elif "add_item failed" in line:
                    tools_seen["add_fail"] += 1

                lines.append(line)
                # Keep the log readable
                tail = lines[-40:]
                log_area.code("\n".join(tail), language="text")
                progress.markdown(
                    f"<div class='muted' style='font-size:0.85rem'>"
                    f"items committed: <b>{tools_seen['add_ok']}</b> · "
                    f"verifier rejects: <b>{tools_seen['add_fail']}</b>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            if dest.exists():
                w.bank = dest
                save_ws()
                st.rerun()

    # --- Validate ----------------------------------------------------
    if w.bank and Path(w.bank).exists():
        st.markdown("<hr class='section-rule'>", unsafe_allow_html=True)
        st.markdown(
            '<div class="eyebrow">validation</div>'
            '<h3 style="margin-top:0">Run the schema gate</h3>',
            unsafe_allow_html=True,
        )
        st.caption("Nine structural / semantic checks (S01–S09). Fail "
                   "loudly here is much better than failing silently later.")

        if st.button("Run validate_bank.py"):
            log = []
            ok_flag = None
            with st.spinner("Validating…"):
                for stream, line in run_validate_bank(Path(w.bank)):
                    if stream == "exit":
                        ok_flag = (line == "0")
                    else:
                        log.append(line)
            st.code("\n".join(log) or "(no output)", language="text")
            if ok_flag:
                st.success("Bank passes all checks.")
            else:
                st.error("Validation failed. Fix the issues above before "
                         "moving on.")

        # --- Preview ----------------------------------------------------
        st.markdown(
            '<div class="eyebrow">bank preview</div>'
            '<h3 style="margin-top:0">Sample items</h3>',
            unsafe_allow_html=True,
        )

        bank = json.loads(Path(w.bank).read_text())
        skills = bank["meta"]["node_order"]
        n_single = len(bank["single_skill_items"])
        n_multi = len(bank["multi_skill_items"])

        c1, c2, c3 = st.columns(3)
        c1.metric("Skills covered", len(skills))
        c2.metric("Single-skill items", n_single)
        c3.metric("Multi-skill items", n_multi)

        coverage = {s: 0 for s in skills}
        for it in bank["single_skill_items"]:
            coverage[it["target_node"]] = coverage.get(
                it["target_node"], 0) + 1
        cov_df = pd.DataFrame([
            {"Skill": s, "Pure items": coverage[s]} for s in skills
        ])
        st.dataframe(cov_df, use_container_width=True, hide_index=True)

        # Show one sample of each kind
        if n_single:
            with st.expander("Sample single-skill item", expanded=True):
                sample = bank["single_skill_items"][0]
                st.markdown(
                    f"**{sample['item_id']}**  ·  "
                    f"target: `{sample['target_node']}`  ·  "
                    f"difficulty: {sample.get('difficulty_hint', '—')}"
                )
                st.markdown(f"**Stem.** {sample['stem']}")
                options = sample.get("options", [])
                if isinstance(options, dict):
                    for k, v in options.items():
                        marker = "✓" if v == sample.get("correct_answer") \
                                 or k == sample.get("correct_option") else "·"
                        st.markdown(f"&nbsp;&nbsp;{marker} **{k}.** {v}",
                                    unsafe_allow_html=True)
                else:
                    for k, v in zip("ABCD", options):
                        marker = "✓" if v == sample.get("correct_answer") \
                                 else "·"
                        st.markdown(f"&nbsp;&nbsp;{marker} **{k}.** {v}",
                                    unsafe_allow_html=True)
                if sample.get("distractor_rationales"):
                    st.caption("**Distractor rationales:**")
                    for k, v in sample["distractor_rationales"].items():
                        st.caption(f"  {k}. {v}")

        if n_multi:
            with st.expander("Sample multi-skill item", expanded=False):
                sample = bank["multi_skill_items"][0]
                st.markdown(
                    f"**{sample['item_id']}**  ·  "
                    f"targets: `{', '.join(sample.get('target_nodes', []))}`"
                )
                st.markdown(f"**Stem.** {sample['stem']}")
                options = sample.get("options", [])
                if isinstance(options, list):
                    for k, v in zip("ABCD", options):
                        marker = "✓" if v == sample.get("correct_answer") \
                                 else "·"
                        st.markdown(f"&nbsp;&nbsp;{marker} **{k}.** {v}",
                                    unsafe_allow_html=True)
