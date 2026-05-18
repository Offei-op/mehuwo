---
title: mehuwo
emoji: 📐
colorFrom: green
colorTo: yellow
sdk: streamlit
app_file: ui/app.py
pinned: false
short_description: Adaptive math diagnostics for Ghanaian classrooms
---

# mehuwo — live demo

Adaptive math diagnostics for Ghanaian classrooms, powered by **local Gemma 4**.

`mehuwo` turns a marked paper test into a per-skill mastery profile, clusters
students by deficit pattern, walks the prerequisite skill graph to build a
remediation path per cluster, and prints teacher-facing PDFs — all offline, on
a laptop, with a local Ollama daemon doing the LLM work.

## What works in this hosted demo

- **Browse the pre-loaded fractions workspace** — skill graph, quiz PDF and
  answer key, mastery heatmap, cluster cards with their DAG-aware labels
  (`foundation-gap` / `middle-gap` / `leaf-gap` / `broad-gap` / `all-mastered`),
  and the printed remediation pack.
- **Try the recognition pipeline live.** Open *Score & Mastery* (Page 3), go to
  the *From roster scan* tab, and upload the bundled `roster_test.pdf` to watch
  ArUco perspective rectification + tick classification produce a real
  `results.xlsx` from scratch.

## What's disabled

The Hugging Face free tier doesn't host an Ollama daemon, so LLM-driven steps
— item-bank generation, cluster narratives, remediation content — are disabled
on this Space. The Ollama pill in the sidebar will show **red**, and the
existing UI gates the affected buttons behind that pill, so the failure mode
is graceful (this is exactly how the dashboard behaves locally when the daemon
is offline).

For the full pipeline — including the agentic, arithmetically-verified item
authoring loop on Gemma 4 — see the source repository.

## Built for

The **Gemma 4 Good Hackathon** · Future of Education track.

[Source code on GitHub](https://github.com/REPLACE-ME-with-your-repo) ·
[Kaggle writeup](https://www.kaggle.com/competitions/gemma-4-good-hackathon)
