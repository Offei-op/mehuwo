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
import uuid
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
from lib.state import invalidate_after


st.set_page_config(page_title="Topic & Items — mehuwo", page_icon="📐",
                   layout="wide")
init_state()
w = require_ws()


def new_bank_path():
    folder = w.root / "bank_versions" / str(uuid.uuid4())
    folder.mkdir(parents=True)
    return folder / "bank.json"


header(
    "Topic & Items",
    eyebrow="step 1 of 5",
    description=(
        "Upload a curriculum, review and approve its proposed skill map, then "
        "generate a multiple-choice question bank. Every "
        "item is verified arithmetically before it's accepted into the bank."
    ),
)


tab_spec, tab_bank = st.tabs(["1 · Topic spec", "2 · Item bank"])


# =========================================================================
# TAB 1: Topic spec
# =========================================================================

with tab_spec:
    from lib.curriculum import render_topic_workflow
    render_topic_workflow(w)


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

    if w.bank:
        st.page_link("pages/6_Expert_Review.py", label="Review answers, skill assignments, and prerequisites")

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
            dest = new_bank_path()
            dest.write_text(BUNDLED["bank"].read_text())
            invalidate_after(w, "bank")
            w.bank = dest
            save_ws()
            st.success(f"Loaded {dest.name}")
            st.rerun()

    # --- Upload ------------------------------------------------------
    elif method == "Upload my own":
        uploaded = st.file_uploader("Bank JSON", type=["json"])
        if uploaded is not None and st.button("Load uploaded bank"):
            dest = new_bank_path()
            dest.write_bytes(uploaded.getvalue())
            invalidate_after(w, "bank")
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

        from assessment.curriculum_maps import require_approved
        try:
            require_approved(w.root, json.loads(Path(w.topic_spec).read_text(encoding="utf-8")))
        except ValueError as exc:
            st.warning(f"Review and approve the current topic map before generating questions. {exc}")
            st.stop()

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
            from lib.jobs import queue
            queue(w, "bank", {
                "model": model.strip() or st.session_state["ollama_model"],
                "host": st.session_state["ollama_host"], "thinking": thinking_arg,
                "target_per_skill": int(target_per_skill), "target_multi": int(target_multi),
                "max_iterations": int(max_iters),
            }, {"spec": w.topic_spec})

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
