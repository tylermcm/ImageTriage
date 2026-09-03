from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="ai-culling",
    title="AI Culling",
    order=2,
    icon="\U0001F916",
    summary="Let the app score and pre-sort a folder, then review its work.",
)

ARTICLES = [
    DocArticle(
        id="how-ai-works",
        title="How AI culling works",
        category="ai-culling",
        summary="The big picture: group duplicates, score quality and content, rank, and review.",
        keywords=("ai", "concept", "overview", "clip", "topiq", "pipeline", "score"),
        markdown="""
        # How AI culling works

        AI culling is an optional layer that scores a folder for you and pre-sorts it, so you spend your time on decisions instead of first passes.

        The guiding principle is simple: **AI suggests, you stay in control.**

        ## The pipeline

        1. **Duplicate prefilter (optional)** — pHash catches near-identical frames before full scoring. See [pHash prefilter](doc:prefilters).
        2. **Cull & Score** — CLIP, TOPIQ, and InsightFace analyze each image for content and technical quality. See [Cull & Score](doc:index-score).
        3. **Group & rank** — similar shots are grouped, and every image gets a score and a position in the ranking.
        4. **Review** — you check the results, compare groups, and make the final calls. See [Reviewing AI results](doc:ai-review).

        ## What you need first

        AI features require the AI runtime and model files. The installer offers to set these up on first launch; you can also install them later from **`AI > AI Setup And Cache`**. See [Where AI files live](doc:where-files-live).

        The supported workflow uses the base-model ranking directly; there is no adapter training step.
        """,
    ),
    DocArticle(
        id="workflow-center",
        title="The AI Workflow Center",
        category="ai-culling",
        summary="The control panel that shows what has run and what to do next.",
        keywords=("workflow center", "steps", "status", "pipeline", "control panel"),
        markdown="""
        # The AI Workflow Center

        Open it from **`AI > AI Workflow Center...`**. It is the control panel for a folder's AI sorting: it shows what has run and what to do next.

        ## Layout

        - **Left** — the workflow steps, in order.
        - **Center** — an explanation of the step you select.

        ## The steps

        1. **Setup** — choose CPU or GPU and install the runtime with CLIP, TOPIQ, and InsightFace.
        2. **Cull & Score** — run duplicate grouping, CLIP scoring and categorization, TOPIQ/InsightFace quality analysis, clustering, and ranking.
        3. **Review Results** — inspect the resulting buckets and comparisons.
        4. **Apply Decisions** — organize the clearest picks, rejects, or semantic categories.

        Each step explains its own prerequisites, so the window always points you at the next useful action.

        > **Tip:** The Workflow Center's **`?`** button opens stage-by-stage help for the step you are on. This page is the overview; that button is the detail.
        """,
    ),
    DocArticle(
        id="index-score",
        title="Cull & Score",
        category="ai-culling",
        summary="The main scoring pass that powers AI review.",
        keywords=("index", "score", "scoring", "clip", "topiq", "rank", "extract"),
        markdown="""
        # Cull & Score

        **Cull & Score** is the core AI pass. It groups near duplicates with pHash, extracts CLIP features, adds TOPIQ and InsightFace quality signals, clusters similar shots, and exports a diversified ranking.

        ## Running it

        1. Open the folder you want to review.
        2. Open **`AI > AI Workflow Center...`** and run **Cull & Score**.
        3. Wait for extraction, grouping, scoring, and report export to finish.
        4. The app loads the new results and switches into **AI Review** automatically.

        ## What it produces

        - A per-image **AI score** and an overall ranking.
        - **Groups** of visually similar shots.
        - A saved HTML report and database in the folder's hidden workspace — see [Where AI files live](doc:where-files-live).

        ## When to re-run it

        Re-run Cull & Score if images were added or removed. For an unchanged folder, **Quick Rerank** reuses the existing ingest, categories, and clusters and recalculates the base ranking.

        > **Tip:** Already scored a folder once? Use **`AI > Load Saved AI For Folder`** to reopen cached results without rerunning the models.
        """,
    ),
    DocArticle(
        id="ai-review",
        title="Reviewing AI results",
        category="ai-culling",
        summary="Read scores and badges, jump to top picks, and compare groups.",
        keywords=("ai review", "badges", "top picks", "scores", "groups", "review"),
        markdown="""
        # Reviewing AI results

        Once results are loaded, **AI Review** turns the scores into a fast review experience.

        ## What you see

        - **Ranked groups** of similar shots.
        - **Per-image AI scores.**
        - **Top-pick hints** for the strongest keepers.
        - **Compare groups** inside Preview.

        ## Move through the picks

        - `Ctrl+Alt+P` jumps to the next AI top pick.
        - `Ctrl+Alt+G` compares the current AI group so you can choose the best frame.

        ## Reading the badges

        AI Review marks images with badges for picks, rejects, and review states. The full legend is available from **`Help > AI Review Tag Legend`**.

        ## Inspecting what the AI did

        Use the result filters to audit the pipeline — **AI Ingested**, **AI Prefilter Dumped**, and **AI Top Picks**. See [pHash prefilter](doc:prefilters).

        When the AI gets a specific image wrong, keep the final decision under manual review rather than applying that bucket automatically.
        """,
    ),
    DocArticle(
        id="prefilters",
        title="pHash prefilter",
        category="ai-culling",
        summary="Optional early checks that reduce what reaches full scoring.",
        keywords=("prefilter", "phash", "duplicate", "pool"),
        markdown="""
        # pHash prefilter

        Prefilters are optional early checks that reduce how many images reach the full CLIP/TOPIQ scoring stage.

        pHash detects very similar images, such as tight near-duplicates, using perceptual hashing.

        ## Pool Removal

        pHash keeps duplicate candidates out of the main scoring stage. This saves processing time without hiding or deleting those images. They remain available for manual review, and images already marked as winners are protected from removal.

        ## Checking the results

        After changing prefilter behavior, audit it with these filters:

        - **AI Ingested** — images that reached the main scoring step.
        - **AI Prefilter Dumped** — images removed from AI scoring by pHash.
        - **AI Top Picks** — the strongest current keep candidates.

        Configure prefilters in Settings — see [AI settings](doc:ai-settings).
        """,
    ),
    DocArticle(
        id="apply-decisions",
        title="Applying AI decisions",
        category="ai-culling",
        summary="Auto-file the clearest winners and rejects.",
        keywords=("apply", "auto", "cull", "decisions", "run and apply"),
        markdown="""
        # Applying AI decisions

        When you trust the ranking, let the app act on the most confident calls for you.

        Choose **`AI > Run And Apply > Apply AI Decisions`**. The app auto-files only the **clearest** winners and rejects, leaving the uncertain middle for you to review by hand.

        ## A safe way to work

        Applying AI decisions is deliberately conservative: it acts where the model is confident and steps back where it is not. You stay responsible for the borderline cases, which is where your judgment matters most.

        ## Suggested flow

        1. Run [Cull & Score](doc:index-score).
        2. Skim the results in [AI Review](doc:ai-review) to sanity-check the ranking.
        3. Apply AI decisions to clear the obvious in and out.
        4. Review the uncertain middle manually and make the final calls.
        """,
    ),
]
