from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="adapters",
    title="Adapter Training",
    order=3,
    icon="\U0001F3AF",
    summary="Teach the AI your taste by training a preference model from your labels.",
)

ARTICLES = [
    DocArticle(
        id="what-adapters-are",
        title="What adapters are",
        category="adapters",
        summary="A small preference model trained from your own labels in a folder.",
        keywords=("adapter", "preference", "model", "learn", "taste", "training"),
        markdown="""
        # What adapters are

        An **adapter** is a small preference model trained from the labels you save in a folder. It teaches the AI what *you* consider good or bad for that specific set of images.

        Where the base CLIP/TOPIQ models judge general quality, an adapter layers your taste on top — the look, subjects, and choices you tend to keep.

        ## The adapter workflow

        1. **Score the folder** with [Index & Score](doc:index-score) so it has an AI database.
        2. **Label examples** — rate, accept, or reject images, or work through suggested candidates in [Reviewing & labeling](doc:review-labels).
        3. **Train** the adapter from those labels — see [Training an adapter](doc:training).
        4. **Evaluate** it before relying on it — see [Evaluating an adapter](doc:evaluating).
        5. **Rank** the folder with the adapter and review the refreshed result.

        ## Before you train

        Train an adapter only once you have labeled enough clear examples across at least two label types, such as keep and reject. A one-sided set teaches the model only half of the decision.

        See [Best practices](doc:adapter-best-practices) for how to get a strong adapter quickly.
        """,
    ),
    DocArticle(
        id="review-labels",
        title="Reviewing & labeling",
        category="adapters",
        summary="Work through suggested label candidates to build training data.",
        keywords=("label", "review labels", "candidates", "training data", "rate"),
        markdown="""
        # Reviewing & labeling

        Good training data is the difference between a sharp adapter and a noisy one. Image Triage helps you build it efficiently.

        ## Two ways to label

        - **As you cull** — every rating, accept, and reject you make is available as a training signal.
        - **Guided review** — choose **`AI > Adapter Training > Review Adapter Labels...`** to work through a curated set of suggested candidates.

        ## Why guided review helps

        Guided review surfaces a *diverse, informative* set of images to label rather than a wall of near-identical frames. It collapses tight duplicates, spreads picks across different scenes, and prioritizes the images that will teach the model the most. That means fewer labels for a better result.

        > **Tip:** A clean batch of varied labels beats a huge batch of repetitive ones. Aim for spread across scenes *and* across the rating scale — include clear keeps, clear rejects, and a few borderline calls.

        Once you have a solid batch, move on to [Training an adapter](doc:training).
        """,
    ),
    DocArticle(
        id="disputing",
        title="Disputing AI decisions",
        category="adapters",
        summary="Save a weighted correction when the AI clearly gets one wrong.",
        keywords=("dispute", "correction", "override", "disagree", "weight"),
        markdown="""
        # Disputing AI decisions

        A **dispute** is a correction you save when the AI clearly gets a specific image wrong — for example, marking a keeper as a reject, or promoting an image you would reject.

        ## How to dispute

        With an image selected in **AI Review**, do any of the following:

        - Click **Dispute AI** in the AI Review toolbar.
        - Right-click the image and choose **Dispute AI Decision**.
        - Press `D`, then a rating key from `1` to `5`:

        1. Best
        2. Strong
        3. Maybe
        4. Weak
        5. Reject

        ## Why disputes carry weight

        Disputes are saved as adapter training labels with **extra weight**, so they teach the adapter more strongly than a normal label. They represent a clear disagreement between you and the AI, which is exactly what the model needs to correct course.

        > **Note:** Use disputes for meaningful corrections, not every small preference. A focused set of clear disputes helps the adapter learn quickly; noisy disputes can make the next adapter less stable.
        """,
    ),
    DocArticle(
        id="training",
        title="Training an adapter",
        category="adapters",
        summary="Turn your saved labels into a personal preference model.",
        keywords=("train", "training", "adapter", "csv", "model"),
        markdown="""
        # Training an adapter

        When you have a solid batch of labels, train the adapter.

        1. Open the folder you trained from, with its AI database from [Index & Score](doc:index-score).
        2. Choose **`AI > Adapter Training > Prepare Rating CSV`** to materialize the current labels.
        3. Choose **`AI > Adapter Training > Train Adapter...`**.
        4. When training finishes, the new adapter appears in the AI Workflow Center with its Score Fit result.

        ## How much to label first

        Train once you have clear examples across at least two label types. Beyond that, more *varied* labels generally help — but retrain after a meaningful batch, not after every small change.

        ## Watching it run

        Long AI tasks show a centered progress dialog. **Stats For Nerds** opens the live training log if you want to follow the details.

        Before you rely on the result, always [evaluate it](doc:evaluating).
        """,
    ),
    DocArticle(
        id="evaluating",
        title="Evaluating an adapter",
        category="adapters",
        summary="Understand Score Fit and decide when an adapter is good enough.",
        keywords=("evaluate", "score fit", "metric", "accuracy", "rank", "apply"),
        markdown="""
        # Evaluating an adapter

        A new adapter is a hypothesis until you check it. Evaluate before trusting it broadly.

        ## Run an evaluation

        Choose **`AI > Adapter Training > Evaluate Adapter`** to check the latest adapter against your stored ratings.

        ## Reading Score Fit

        **Score Fit** shows how closely the adapter's scores matched your saved labels during testing. It is a *score-regression fit* metric — a measure of agreement with your labels, not a full measure of culling accuracy. Treat it as one signal among several, alongside how the ranking actually looks.

        ## The honest stopping rule

        There is no single magic number of labels. The most reliable guide is the trend: label a batch, retrain, evaluate, and watch whether results keep improving. **Stop when extra labels stop moving the needle** — and make sure your labels are balanced across keeps and rejects, not just keepers.

        ## Apply the ranking

        When you are satisfied, choose **`AI > Adapter Training > Rank Folder With Local Adapter`** to refresh the ranking, then review it in [AI Review](doc:ai-review).
        """,
    ),
    DocArticle(
        id="adapter-best-practices",
        title="Best practices",
        category="adapters",
        summary="Habits that produce strong, stable adapters.",
        keywords=("best practices", "tips", "quality", "balance", "stable"),
        markdown="""
        # Best practices for adapters

        A few habits consistently produce better adapters.

        - **Start with representative folders.** Train on the kind of work you care about most.
        - **Label both ends.** Clear winners *and* clear rejects — an adapter needs to learn the boundary, not just the keepers.
        - **Cover the uncertain middle.** Use [guided label review](doc:review-labels) to label informative, borderline cases.
        - **Use disputes sparingly but deliberately.** A focused set of [disputes](doc:disputing) corrects real mistakes; noisy ones add instability.
        - **Retrain in batches.** Label a meaningful set, then retrain — not after every change.
        - **Evaluate before trusting.** Check [Score Fit](doc:evaluating) and the actual ranking before applying broadly.

        > **Note:** Diversity beats volume. A varied set of well-chosen labels trains a sharper adapter than a large set of near-duplicates.
        """,
    ),
]
