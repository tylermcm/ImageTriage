# Deep research prompt: commercially usable replacement for TOPIQ

Paste everything under the line into a deep-research tool. The results come back to the repo as evidence for
`docs/ai_quality_plan.md` and `docs/ai_model_licence_audit.md`, so ask for sources, not summaries.

---

## Task

I ship a commercial desktop photo-culling app (Windows and Linux, CPU first, optional GPU, ONNX Runtime, no cloud). It scores
each photo's technical and aesthetic quality to help photographers choose keepers from thousands of shots. I currently use
TOPIQ (no-reference, KonIQ-10k trained) as a 512-pixel ONNX model, and I **cannot ship it commercially**: the TOPIQ / pyiqa
code is PolyForm Noncommercial 1.0.0 and the ONNX copy I found is CC BY-NC-SA 4.0.

Find me a **replacement I can legally ship in a paid product**, or the most defensible route to one. Be rigorous about licences;
a wrong "yes" is much worse than "unknown".

## What I already established (do not repeat, but challenge if wrong)

- Public IQA datasets I looked at say they are "freely available to the research community" and give no commercial grant:
  KonIQ-10k, KADID-10k. SPAQ's repo states no licence. AVA is scraped from dpchallenge.com (uploader copyright). TID2013 and
  PaQ-2-PiQ terms were not found.
- MUSIQ (Google) has no licence file; its checkpoints are trained on AVA, KonIQ, PaQ-2-PiQ and SPAQ.
- CLIP-IQA code is NTU S-Lab License 1.0. idealo NIMA is Apache-2.0 code with AVA/TID2013 weights and no weight licence
  (archived Dec 2024). LAION aesthetic predictors are Apache-2.0 code, weights trained on AVA/SAC/LAION data.
- A 2026 paper, "Don't Trust the Label: License Laundering in AI Supply Chains" (arXiv 2607.20300), argues permissive
  labels can hide restrictive training data. I want to know how much that matters in practice for downstream users.

## Questions to answer, with sources

1. **Pretrained models.** Find every no-reference image quality or aesthetic model (2023 to 2026) whose **weights** are released
   under a licence that allows commercial use (MIT, Apache-2.0, BSD, CC0, CC-BY, OpenRAIL-M with commercial permitted, etc.)
   **and** whose **training data** is itself commercially usable or is self-supervised / synthetic / unlabeled. For each: name,
   authors, repo and weights URL, exact licence text for code AND weights, training datasets and each dataset's licence,
   input size, parameter count, whether an ONNX export exists, reported correlation (SRCC/PLCC) on KonIQ/SPAQ/LIVE-C, and CPU
   latency if reported. Include newer families I may not know (e.g. LLM or CLIP-based scorers, QualiCLIP, ARNIQA, LIQE,
   DeQA-Score, Q-Align, MANIQA, HyperIQA, CONTRIQUE, and anything newer). Say plainly which are excluded and why
   (NC licence, GPL, Llama or other restrictive base-model licence, research-only data).
2. **Self-supervised and synthetic routes.** Which methods learn quality without human opinion scores (synthetic distortions,
   contrastive pairs, text-prompt anchors)? Which training sets do they use, are those sets licensed for commercial training,
   and could I reproduce them on photos I own or on CC0/CC-BY images? Give recipes with the severity ranges and distortion
   types that worked, and known failure modes.
3. **Commercially clean data.** List photo datasets with explicit commercial-training-friendly licences (CC0, CC-BY, Unsplash,
   Pexels, Wikimedia Commons, Open Images with its licence caveats, others) suitable for building a quality benchmark or a
   synthetic-degradation training set. State each licence's attribution, share-alike and redistribution conditions.
4. **Legal landscape for downstream users.** What do actual legal analyses, court decisions and regulator guidance (EU AI Act
   GPAI provisions, US fair-use cases on training, UK, Japan) say about a **downstream user** shipping model weights that were
   trained on a research-only or non-commercial dataset? Is the non-commercial term inherited by the weights? Distinguish what
   is decided law, what is argued by commentators, and what is unknown. Cite cases and statutes by name and date.
5. **Getting permission.** Do the owners of KonIQ-10k, KADID-10k, SPAQ, AVA, PaQ-2-PiQ and TID2013 offer commercial licences?
   Give contact routes, any published terms, and examples of companies that obtained them.
6. **Recommendation.** Rank the options for a small commercial team: (a) a specific permissive pretrained model, (b) training
   our own small head on embeddings plus synthetic degradations of photos we own, (c) zero-shot CLIP-style prompt scoring on an
   embedding we already ship, (d) negotiating commercial rights, (e) shipping a research-trained model after legal review.
   For each give effort, expected quality loss versus TOPIQ, and legal risk.

## Constraints on the answer

- Every licence claim needs a link to the licence text or model card **and the date you read it**. Quote the key sentence.
- Separate "verified from primary source" from "reported by a secondary source" from "could not verify".
- Do not treat a Hugging Face `license:` tag as proof; check the repo LICENSE file and the training-data statement.
- If something is only likely, say so. Do not fill gaps from memory.
- Finish with a one-page summary table: model or route, code licence, weights licence, training-data licence, ONNX available,
  quality evidence, verdict (usable / usable after legal review / not usable).
- This is research for a product decision; do not give legal advice, but do state where a lawyer is needed.
