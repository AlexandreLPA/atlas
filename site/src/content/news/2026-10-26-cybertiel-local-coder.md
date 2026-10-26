---
title: CyberTiel runs a 35B coding model on a 24GB graphics card
date: 2026-10-26
source: AlphaSignal
url: https://alphasignal.ai/news/cybertiel-brings-uncensored-security-coding-to-24gb-gpus
image: https://api.alphasignal.ai/api/news/preview/cybertiel-brings-uncensored-security-coding-to-24gb-gpus/v2/card.jpg
kind: model
topics: [agents, open source]
atlas: A local model like this is the kind Atlas can use as an executor when the paid plans are out of limit, but run it in a sandbox.
---

A community release that packs a 35B mixture-of-experts coder into a 22GB quantized file for
llama.cpp. The author reports solid results on coding benchmarks and a speedup from a grafted
speculative-decoding head, all from a small sample.

Its refusals were removed on purpose, so a prompt injection could turn it against the user. It needs
to run isolated from the rest of the machine.
