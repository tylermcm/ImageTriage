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
        summary="The big picture: score, group, rank, review, and (optionally) learn.",
        keywords=("ai", "concept", "overview", "clip", "topiq", "pipeline", "score"),
        markdown="""
        # How AI culling works

        AI culling is an optional layer that scores a folder for you and pre-sorts it, so you spend your time on decisions instead of first passes.

        The guiding principle is simple: **AI suggests, you stay in control.**

        ## The pipeline

        1. **Prefilter (optional)** — quick checks flag obvious problems before full scoring. See [Prefilters: DINO & pHash](doc:prefilters).
        2. **Index & Score** — the main CLIP/TOPIQ models analyze each image for quality and content. See [Index & Score](doc:index-score).
        3. **Group & rank** — similar shots are grouped, and every image gets a score and a position in the ranking.
        4. **Review** — you check the results, compare groups, and make the final calls. See [Reviewing AI results](doc:ai-review).
        5. **Learn (optional)** — train an adapter from your labels so the ranking matches *your* taste. See [What adapters are](doc:what-adapters-are).

        ## What you need first

        AI features require the AI runtime and model files. The installer offers to set these up on first launch; you can also install them later from **`AI > Runtime And Cache`**. See [Where AI files live](doc:where-files-live).

        > **Note:** You never have to use the adapter steps. Scoring and review work on their own — adapters simply make the ranking personal.
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

        Open it from **`AI > AI Workflow Center...`**. It is the control panel for a folder's AI sorting: it shows what has run, what to do next, and how your trained models performed.

        ## Layout

        - **Left** — the workflow steps, in order.
        - **Center** — an explanation of the step you select.
        - **Right** — trained adapters and their Score Fit results.

        ## The steps

        1. **Setup** — install the AI runtime and model files.
        2. **DINO Prefilter** — flag likely rejects before scoring (optional).
        3. **Index & Score** — run the main CLIP/TOPIQ scoring pass.
        4. **Review Labels** — confirm or correct example images.
        5. **Train Adapter** — build a preference model from your labels.
        6. **Evaluate** — measure how well the adapter performs.
        7. **Rank & Apply** — apply the final ranking to the folder.

        Each step explains its own prerequisites, so the window always points you at the next useful action.

        > **Tip:** The Workflow Center's **`?`** button opens stage-by-stage help for the step you are on. This page is the overview; that button is the detail.
        """,
    ),
    DocArticle(
        id="index-score",
        title="Index & Score",
        category="ai-culling",
        summary="The main scoring pass that powers AI review.",
        keywords=("index", "score", "scoring", "clip", "topiq", "rank", "extract"),
        markdown="""
        # Index & Score

        **Index & Score** is the core AI pass. It extracts features from every image, groups similar shots, scores them with the CLIP/TOPIQ models, and exports a report.

        ## Running it

        1. Open the folder you want to review.
        2. Open **`AI > AI Workflow Center...`** and run **Index & Score**.
        3. Wait for extraction, grouping, scoring, and report export to finish.
        4. The app loads the new results and switches into **AI Review** automatically.

        ## What it produces

        - A per-image **AI score** and an overall ranking.
        - **Groups** of visually similar shots.
        - A saved HTML report and database in the folder's hidden workspace — see [Where AI files live](doc:where-files-live).

        ## When to re-run it

        Re-run Index & Score if the folder changes substantially — images added or removed — before training or ranking again. For a stale ranking on an unchanged folder, re-ranking with your adapter is usually enough; see [Ranking with your adapter](doc:evaluating).

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

        Use the result filters to audit the pipeline — **AI Ingested**, **AI Prefilter Dumped**, **DINO Quarantine**, **DINO Removed**, and **AI Top Picks**. See [Prefilters: DINO & pHash](doc:prefilters).

        When the AI gets a specific image wrong, you can correct it — see [Disputing AI decisions](doc:disputing).
        """,
    ),
    DocArticle(
        id="prefilters",
        title="Prefilters: DINO & pHash",
        category="ai-culling",
        summary="Optional early checks that reduce what reaches full scoring.",
        keywords=("prefilter", "dino", "phash", "quarantine", "duplicate", "pool"),
        markdown="""
        # Prefilters: DINO & pHash

        Prefilters are optional early checks that reduce how many images reach the full CLIP/TOPIQ scoring stage.

        ## DINO Prefilter

        DINO examines each image and flags those that are likely bad, unwanted, or not worth scoring further.

        ## pHash Prefilter

        pHash detects very similar images, such as tight near-duplicates, using perceptual hashing. It works independently of DINO.

        ## Soft Quarantine vs. Pool Removal

        - **Soft Quarantine** marks images as questionable without hiding or deleting them. They stay visible but labeled, so you can inspect them later.
        - **Pool Removal** keeps flagged images out of the main scoring stage. This is faster, but those images are then excluded from the AI ranking.

        > **Tip:** When testing new thresholds, start with Soft Quarantine. It lets you see what the AI *would* have removed before anything is actually excluded.

        ## Checking the results

        After changing prefilter behavior, audit it with these filters:

        - **AI Ingested** — images that reached the main scoring step.
        - **AI Prefilter Dumped** — images quarantined or removed by DINO or pHash.
        - **DINO Quarantine** — images DINO marked for soft quarantine.
        - **DINO Removed** — images DINO removed from the scoring pool.
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

        1. Run [Index & Score](doc:index-score).
        2. Skim the results in [AI Review](doc:ai-review) to sanity-check the ranking.
        3. Apply AI decisions to clear the obvious in and out.
        4. Review what remains, then refine with an [adapter](doc:what-adapters-are) if you want the ranking to match your taste more closely.
        """,
    ),
]
