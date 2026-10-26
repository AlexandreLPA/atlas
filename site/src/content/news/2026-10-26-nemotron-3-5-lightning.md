---
title: NVIDIA's Nemotron 3.5 Lightning fits a 1M-token context on one node
date: 2026-10-26
source: AlphaSignal
url: https://alphasignal.ai/news/nvidia-releases-nemotron-3-5-lightning-with-1m-token-context-on-one-gpu
image: https://api.alphasignal.ai/api/news/preview/nvidia-releases-nemotron-3-5-lightning-with-1m-token-context-on-one-gpu/v2/card.jpg
kind: model
topics: [models, open source]
---

A 30B open-weight model that activates only about 3B parameters per token, mixing Mamba-2,
attention and mixture-of-experts layers. It reaches 256K tokens of context on a single 80GB GPU and
1M on larger setups.

Commercial use is allowed. On most benchmarks it trails Qwen 3.6 35B A3B, so the draw is efficient
long-context serving, not top scores.
