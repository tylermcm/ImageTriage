# TOPIQ replacement: research findings

Source: a ChatGPT deep-research report returned to the user on 18 October 2026, then spot-checked by us the same day.
The report is a secondary source. Anything marked **verified** below was re-read at the primary page by us; everything
else is the report's claim and should be treated as unverified until someone opens the source.

## Conclusion of the report

It found no 2023 to 2026 pretrained no-reference photo quality model that passes all three gates: code licence allows
commercial use, the weights themselves are licensed for commercial redistribution, and the training data are commercially
usable (owned, CC0, CC-BY, or generated from such images). Its recommended route is to train our own small quality model on
photos we own plus synthetic degradations, and to use our already-cleared embedding for the aesthetic side.

## Verified by us (17-18 October 2026)

| Claim | Result |
|---|---|
| ARNIQA repo is Apache-2.0 | Verified (repo page lists Apache-2.0). No statement anywhere on that page about the licence of the released weights. |
| ARNIQA's config starts from ImageNet-pretrained weights | Verified: `pretrained: true # if True, use ImageNet pretrained weights`; 224 px patches, `max_distortions: 4`, `num_levels: 5`, `embedding_dim: 128`. |
| QualiCLIP is non-commercial | Verified: "All material is made available under Creative Commons BY-NC 4.0." Its README also lists the pretrained model as not yet released. |
| Q-Align's licence is non-commercial although the Hugging Face tag says MIT | Verified: the repo LICENSE is the S-Lab License 1.0; the opening grant reads "Redistribution and use for non-commercial purpose...". The HF tag was not re-checked today. |
| PaQ-2-PiQ is non-commercial | Verified: README badge reads CC BY-NC-SA 4.0. The repo also holds a file named `CC-BY-NC-4.0.txt`, a different NC variant; both restrict commercial use. |
| KonIQ-10k and KADID-10k are only stated as available "to the research community" | Verified earlier today (owner pages). |

## Reported but not verified by us

- LIQE, QCN, GRepQ, Re-IQA: MIT code, weights trained with subjective-score datasets or with unstated weight terms.
- DeQA-Score: MIT code, built on a Llama-2-class base and trained on KonIQ, SPAQ and KADID.
- Q-ReAlign: no repository licence, trained on a mixture of KonIQ, SPAQ, KADID, AGIQA-20K, AVA and LSVQ.
- FGResQ: Apache-2.0 code, but its README reportedly says its training dataset is "available for research purposes".
- MA-CLIP (AAAI 2026): training-free CLIP-based scoring; the repository reportedly has no licence file, so only the idea is usable.
- PickScore and ImageReward: generated-image preference models, not general photo quality scorers.
- The legal summaries (US fair-use cases in 2025, the UK Getty v Stability judgment, EU AI Act article 53, Japan's article 30-4).
  The report itself marks the US cases as not re-read at the court documents. A lawyer needs the primary texts.
- Source-image pools: CC0, CC BY 4.0, Unsplash, Pexels, Pixabay, Wikimedia Commons, Open Images, with their conditions.
- No published commercial licensing programme was found for KonIQ-10k, KADID-10k, SPAQ, AVA, PaQ-2-PiQ or TID2013, and no
  company was found that has obtained one.

## What we take from it

1. **No drop-in model.** The only near-miss is ARNIQA's encoder, which is amber: Apache code, no weight grant, ImageNet
   initialisation, KADIS-based training data.
2. **A self-trained model is the route that fully controls the chain.** The report's design: an ARNIQA-style encoder with five
   ordered severity levels, one or two distortions at a time, a small pristine fraction, pairwise-ranking plus severity
   losses, and a separate high-resolution detail path so slight focus misses are not lost in a downsize.
3. **Starting weights matter.** ARNIQA starts from ImageNet-pretrained weights. If we train our own network, a pretrained
   backbone brings its own licence and data question. Options are random initialisation, or building on the embedding model
   we already ship (TinyCLIP, MIT), or an initialisation whose licence we have checked. We have not checked ImageNet-derived
   backbone weights.
4. **Split the score** into technical quality, subject/eye focus quality and an aesthetic "keeper" score, kept separate until
   the ranking layer. This matches the plan's existing dimensions design.
5. **Our own pairwise labels are the most valuable data.** Burst picks ("A or B?") teach what culling needs, and they feed
   the per-folder learner too. This links directly to the card photos and the manual-cull labelling task.
6. **Zero-shot prompt scoring on our embedding** is cheap, especially for aesthetics. Use paired prompts and keep the
   technical and aesthetic prompts separate. The report suggests testing embedding-magnitude cues (the MA-CLIP idea) by
   independent implementation, not by copying code.
7. **Permission route:** an explicit commercial grant must name training, derived weights and commercial distribution, not
   just "commercial use". Start with the Konstanz group for KonIQ-10k and KADID-10k.
8. **Failure modes to plan for:** synthetic degradation differs from real camera defects, intentional blur or grain is not a
   defect, global sharpness misses wrong-subject focus, and composition is not a distortion.

Where this changes the plan: `docs/ai_quality_plan.md` section 3A now recommends the self-trained route first.

## Public photo pools for the clean-source set (read 18 October 2026)

No public dataset offers quality labels meant for commercial use: every image-quality dataset we found is research-only or has no
licence. Public photo collections are useful only as extra clean sources for synthetic damage.

| Pool | Licence read at source | Commercial training | Caveats |
|---|---|---|---|
| PD12M (Spawning, Hugging Face) | Dataset CDLA-Permissive-2.0; images "entirely public domain and CC0" | Not stated explicitly, but the licence is permissive | Mostly museum, archive and iNaturalist images, not camera-style photos; sizes range from 256 px to very large; no opt-out status recorded; takedowns go through Source.Plus |
| CommonCatalog CC-BY (Common Canvas) | Dataset card says CC BY 4.0, per-image rows say CC BY 2.0 (inconsistent) | Not stated | Flickr photos about 1000 to 4000 px; per-photo attribution fields; keep a rights record |
| Open Images V7 | Annotations CC BY 4.0; images "listed as having a CC BY 2.0 license" | Not addressed | Google "make no representations or warranties regarding the license status of each image" |
| Megalith-10M (madebyollin) | Card tagged MIT (metadata); photos described as CC0 on Flickr | Not stated; one company asserts ML use is fine | Links to Flickr; image size not stated; a similar Flickr set (MegaFace) found 69% of images prohibited commercial use despite CC licences, so per-image checks matter |
| Unsplash Lite | TERMS.md: training allowed "for your internal business purposes"; Full dataset is non-commercial only | Internal business use only | Unclear whether shipping trained weights to customers counts as internal; no redistribution; needs Unsplash's written permission or counsel |

Recommendation: the owner's own photos stay the primary source (the failure seen was a lack of night, low-light and
texture-heavy clean examples, not a licensing one). Add a modest set of CC0 or public-domain photos, filtered to high
resolution and low compression, only to widen variety, each with a rights record.
