"""Prompt-diversity measurement for LLM Prompt Studio.

Why this module exists
----------------------
"Are the prompts actually diverse?" needs a number, not an impression. The
metrics implemented here are the standard ones from the text-generation
literature:

* ``distinct_n``      lexical variety of a prompt set (Li et al., 2015,
                      "A Diversity-Promoting Objective Function for Neural
                      Conversation Models", arXiv:1510.03055, whose evaluation
                      introduced distinct-1/distinct-2).
* ``self_bleu``       how much the items copy each other; lower is better
                      (Zhu et al., 2018, Texygen, arXiv:1802.01886, which
                      standardised Self-BLEU as a diversity metric).
* ``vendi_score``     the effective number of distinct items, computed as
                      ``exp(H(eigenvalues of a similarity matrix))``
                      (Friedman & Dieng, 2022, "The Vendi Score",
                      arXiv:2210.02410). It needs no reference distribution and
                      is explicitly designed to expose mode collapse.
* ``content_cosine``  mean pairwise overlap *after* the shared subject and
                      format skeleton is removed, i.e. how much of the
                      variable content (action, place, prop, light, palette)
                      two prompts still have in common.

The skeleton vocabulary, the concept aliases and the stop-word set live here
and are imported by ``prompt_studio_ui``. That is deliberate: the runtime
duplicate gate and this report must measure exactly the same thing, otherwise
"the gate passes" and "the report looks good" can drift apart.

Only the standard library is required at import time; numpy is used when it is
available for the eigen-decomposition, with a Jacobi fallback otherwise.

Usage
-----
    python prompt_studio_diversity.py --db user/prompt_studio.db
    python prompt_studio_diversity.py --file prompts.txt --json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
# Shared vocabulary (imported by prompt_studio_ui)
# --------------------------------------------------------------------------

# Shared subject and format vocabulary. Every item of a batch keeps these words,
# so they carry no diversity signal: counting them made the old duplicate rule
# flag every pair and the old ledger demand "avoid animal ears / fluffy tail".
# Action, place, prop, material, palette and light words deliberately stay out
# of the list, because those are the dimensions a batch is supposed to vary.
SKELETON_WORDS = """
    a an the and or of with without into onto over under above below beside near around across
    through between behind in front on at to from as by for while when that which who whose
    her his she he they them their it its this these those there here very slightly
    girl boy woman man lady child kid young teenage adult single solo
    character figure person people subject face faces head hair hairstyle bangs eyes eye
    iris pupil eyebrow eyelashes skin body arm arms hand hands finger fingers leg legs foot feet
    ear ears animal fluffy soft fur furry tail tails horn horns wing wings
    cute adorable pretty beautiful lovely expression expressions
    outfit clothes clothing dress skirt shirt blouse sweater apron uniform costume
    wear wearing wears worn dressed attire garment garments
    pose posing stance position view visible appears image picture illustration art artwork
    anime japanese prompt scene scenes setting background foreground midground depth layer layers
    composition frame framing quality masterpiece best detailed detail details resolution
    photograph photo render high
"""
SKELETON_TOKENS = frozenset(
    token for token in re.split(r"[^a-z0-9_]+", SKELETON_WORDS.lower()) if token
)

# Format and genre boilerplate that says nothing about the picture content.
STOP_TOKENS = frozenset({
    "a", "an", "and", "anime", "art", "best", "background", "character", "clothes", "clothing",
    "color", "composition", "cute", "detailed", "expression", "face", "girl", "hair", "image",
    "illustration", "japanese", "light", "lighting", "masterpiece", "outfit", "pose", "quality",
    "scene", "setting", "subject", "style", "the", "very", "visual", "weather", "with",
    "anchors", "backgrounds", "break", "description", "detail", "details", "extreme", "layered",
    "master", "part", "paragraph", "props", "reference", "section", "tags", "three", "two", "use",
}) | SKELETON_TOKENS

# Morphological variants that mean the same visual decision.
CONCEPT_ALIASES = {
    "stand": "stand", "standing": "stand", "wait": "stand", "waiting": "stand", "stroll": "walk",
    "walk": "walk", "walking": "walk", "step": "walk", "stepping": "walk",
    "run": "run", "running": "run", "sprint": "run", "sprinting": "run",
    "sit": "sit", "sitting": "sit", "seated": "sit", "kneel": "kneel", "kneeling": "kneel",
    "hold": "hold", "holding": "hold", "carry": "hold", "carrying": "hold", "grip": "hold", "gripping": "hold",
    "look": "gaze", "looking": "gaze", "gaze": "gaze", "gazing": "gaze", "stare": "gaze", "staring": "gaze",
    "smile": "smile", "smiling": "smile", "grin": "smile", "grinning": "smile",
    "laugh": "laugh", "laughing": "laugh", "giggle": "laugh", "giggling": "laugh",
    "portrait": "portrait", "closeup": "portrait", "close_up": "portrait", "headshot": "portrait",
    "fullbody": "fullbody", "full_body": "fullbody", "wide_shot": "wide_shot", "long_shot": "wide_shot",
    "front_facing": "front_facing", "three_quarter": "three_quarter", "low_angle": "low_angle", "high_angle": "high_angle",
    "temple": "shrine", "shrine": "shrine", "sanctuary": "shrine", "shrine_gate": "shrine",
    "umbrella": "umbrella", "parasol": "umbrella", "rain_umbrella": "umbrella",
}

DEFAULT_SIMILARITY = 0.55
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,4}")


# --------------------------------------------------------------------------
# Tokenisation and similarity
# --------------------------------------------------------------------------

def token_counts(text: str) -> collections.Counter:
    """Same tokenisation as ``prompt_studio_core._tokens``."""
    return collections.Counter(_TOKEN_PATTERN.findall((text or "").lower()))


def canonical_counts(text: str) -> collections.Counter:
    """Token counts with morphological variants folded onto one concept."""
    counts = collections.Counter()
    for token, count in token_counts(text).items():
        counts[CONCEPT_ALIASES.get(token, token)] += count
    return counts


def content_vector(text: str, skeleton: Iterable[str] = SKELETON_TOKENS) -> collections.Counter:
    """Content-bearing tokens only: skeleton, stop words and short noise removed."""
    skeleton_tokens = set(skeleton)
    return collections.Counter({
        token: count for token, count in canonical_counts(text).items()
        if token not in STOP_TOKENS and token not in skeleton_tokens
        and (len(token) >= 3 or "_" in token)
    })


def content_tokens(text: str, skeleton: Iterable[str] = SKELETON_TOKENS) -> list[str]:
    """Return content tokens in source order using the same filtering as the gate."""
    skeleton_tokens = set(skeleton)
    tokens = []
    for raw_token in _TOKEN_PATTERN.findall((text or "").lower()):
        token = CONCEPT_ALIASES.get(raw_token, raw_token)
        if token in STOP_TOKENS or token in skeleton_tokens:
            continue
        if len(token) >= 3 or "_" in token:
            tokens.append(token)
    return tokens


def cosine(left: collections.Counter, right: collections.Counter) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    numerator = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def content_cosine(left: str, right: str, skeleton: Iterable[str] = SKELETON_TOKENS) -> float:
    """Cosine over content tokens only (the gate's notion of "same picture")."""
    return cosine(content_vector(left, skeleton), content_vector(right, skeleton))


def mean_pairwise_cosine(texts: Sequence[str], skeleton: Iterable[str] = SKELETON_TOKENS) -> float:
    vectors = [content_vector(text, skeleton) for text in texts]
    values = [
        cosine(vectors[i], vectors[j])
        for i in range(len(vectors)) for j in range(i + 1, len(vectors))
    ]
    return sum(values) / len(values) if values else 0.0


# --------------------------------------------------------------------------
# Diversity metrics
# --------------------------------------------------------------------------

def _ngrams(tokens: Sequence[str], n: int) -> list[tuple]:
    return [tuple(tokens[index:index + n]) for index in range(len(tokens) - n + 1)]


def distinct_n(docs: Sequence[Sequence[str]], n: int) -> float:
    """Ratio of unique n-grams to all n-grams (higher = more lexical variety)."""
    grams = [gram for doc in docs for gram in _ngrams(list(doc), n)]
    if not grams:
        return 0.0
    return len(set(grams)) / len(grams)


def self_bleu(docs: Sequence[Sequence[str]], n: int = 4, max_samples: int = 200) -> float:
    """Approximate Self-BLEU: mean leave-one-out n-gram precision (lower = better).

    The exact metric scores every sample against the whole remaining set with
    BLEU; this corpus-level precision version keeps the same ordering while
    staying dependency-free and fast enough for a few hundred prompts.
    """
    corpus = collections.Counter(gram for doc in docs for gram in _ngrams(list(doc), n))
    scores = []
    for doc in docs[:max(1, max_samples)]:
        grams = _ngrams(list(doc), n)
        if not grams:
            continue
        # Exclude every n-gram occurrence from the current document. Otherwise
        # repeated phrases inside one prompt are incorrectly treated as
        # evidence that other prompts copied it.
        reference = corpus - collections.Counter(grams)
        hits = sum(1 for gram in grams if reference[gram] > 0)
        scores.append(hits / len(grams))
    return sum(scores) / len(scores) if scores else 0.0


def _jacobi_eigenvalues(matrix: list[list[float]], tolerance: float = 1e-12,
                        sweeps: int = 100) -> list[float]:
    """Eigenvalues of a symmetric matrix (fallback when numpy is unavailable)."""
    size = len(matrix)
    work = [row[:] for row in matrix]
    for _ in range(sweeps):
        off_diagonal = sum(
            work[i][j] * work[i][j] for i in range(size) for j in range(size) if i != j
        )
        if off_diagonal < tolerance:
            break
        for p in range(size - 1):
            for q in range(p + 1, size):
                if abs(work[p][q]) < 1e-15:
                    continue
                theta = (work[q][q] - work[p][p]) / (2.0 * work[p][q])
                sign = 1.0 if theta >= 0 else -1.0
                t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(size):
                    akp, akq = work[k][p], work[k][q]
                    work[k][p] = c * akp - s * akq
                    work[k][q] = s * akp + c * akq
                for k in range(size):
                    apk, aqk = work[p][k], work[q][k]
                    work[p][k] = c * apk - s * aqk
                    work[q][k] = s * apk + c * aqk
    return [work[i][i] for i in range(size)]


def _eigenvalues(matrix: list[list[float]]) -> list[float]:
    try:
        import numpy as np  # type: ignore
    except Exception:
        return _jacobi_eigenvalues(matrix)
    return [float(value) for value in np.linalg.eigvalsh(np.array(matrix))]


def vendi_score(docs: Sequence[Sequence[str]], max_items: int = 400) -> float:
    """Vendi Score over a token-cosine similarity matrix (Friedman & Dieng, 2022).

    Returns the effective number of distinct items: 1.0 means every item is the
    same picture, and the maximum is the number of items.
    """
    vectors = [collections.Counter(doc) if not isinstance(doc, str) else content_vector(doc)
               for doc in docs]
    vectors = [vector for vector in vectors if vector][:max_items]
    size = len(vectors)
    if size < 2:
        return float(size)
    norms = [math.sqrt(sum(value * value for value in vector.values())) or 1.0
             for vector in vectors]
    similarity = [[0.0] * size for _ in range(size)]
    for i in range(size):
        similarity[i][i] = 1.0
        for j in range(i + 1, size):
            value = cosine(vectors[i], vectors[j])
            similarity[i][j] = similarity[j][i] = value
    # Normalise so the matrix sums to 1, as required by the Vendi definition.
    normalised = [[value / size for value in row] for row in similarity]
    eigenvalues = [value for value in _eigenvalues(normalised) if value > 1e-12]
    if not eigenvalues:
        return 0.0
    entropy = -sum(value * math.log(value) for value in eigenvalues)
    return math.exp(entropy)


def duplicate_clusters(texts: Sequence[str], threshold: float = DEFAULT_SIMILARITY,
                       skeleton: Iterable[str] = SKELETON_TOKENS) -> list[list[int]]:
    """Group indexes whose content cosine reaches the threshold (greedy, order-stable)."""
    vectors = [content_vector(text, skeleton) for text in texts]
    clusters: list[list[int]] = []
    for index, vector in enumerate(vectors):
        for cluster in clusters:
            if cosine(vector, vectors[cluster[0]]) >= threshold:
                cluster.append(index)
                break
        else:
            clusters.append([index])
    return [cluster for cluster in clusters if len(cluster) > 1]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def diversity_report(texts: Sequence[str], skeleton: Iterable[str] = SKELETON_TOKENS,
                     threshold: float = DEFAULT_SIMILARITY) -> dict:
    """Compute every metric for one prompt set."""
    cleaned = [str(text or "").strip() for text in texts]
    cleaned = [text for text in cleaned if text]
    docs = [content_tokens(text, skeleton) for text in cleaned]
    docs = [doc for doc in docs if doc]
    clusters = duplicate_clusters(cleaned, threshold, skeleton)
    vendi_docs = docs[:400]
    vendi = vendi_score(vendi_docs)
    report = {
        "count": len(cleaned),
        "scored": len(docs),
        "mean_content_tokens": round(sum(len(doc) for doc in docs) / len(docs), 1) if docs else 0.0,
        "distinct_1": round(distinct_n(docs, 1), 4),
        "distinct_2": round(distinct_n(docs, 2), 4),
        "distinct_3": round(distinct_n(docs, 3), 4),
        "self_bleu_3": round(self_bleu(docs, 3), 4),
        "self_bleu_4": round(self_bleu(docs, 4), 4),
        "vendi": round(vendi, 2),
        "vendi_scored": len(vendi_docs),
        "vendi_ratio": round(vendi / len(vendi_docs), 4) if vendi_docs else 0.0,
        "mean_content_cosine": round(mean_pairwise_cosine(cleaned, skeleton), 4),
        "duplicate_clusters": len(clusters),
        "duplicate_items": sum(len(cluster) - 1 for cluster in clusters),
        "threshold": threshold,
    }
    return report


def format_report(report: dict) -> str:
    """Human-readable summary with a short interpretation."""
    vendi_ratio = report["vendi_ratio"]
    verdict = (
        "healthy" if vendi_ratio >= 0.6 else
        "acceptable" if vendi_ratio >= 0.35 else
        "collapsing"
    )
    lines = [
        f"prompts={report['count']}  scored={report['scored']}  "
        f"content tokens/item={report['mean_content_tokens']}",
        f"  distinct-1/2/3      {report['distinct_1']:.3f} / {report['distinct_2']:.3f} / {report['distinct_3']:.3f}"
        "   (higher = more lexical variety)",
        f"  Self-BLEU-3/4       {report['self_bleu_3']:.3f} / {report['self_bleu_4']:.3f}"
        "   (lower = items copy each other less)",
        f"  Vendi               {report['vendi']:.2f} of {report['vendi_scored']} max"
        f"   (= {report['vendi_ratio'] * 100:.1f}% effective, {verdict})",
        f"  mean content cosine {report['mean_content_cosine']:.3f}"
        "   (lower = more distinct)",
        f"  near-duplicates     {report['duplicate_items']} items in "
        f"{report['duplicate_clusters']} clusters at threshold {report['threshold']:.2f}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Input helpers / CLI
# --------------------------------------------------------------------------

def load_prompts_from_db(path: str | Path, limit: int = 0) -> list[str]:
    """Read prompts from a Studio database without ever writing to it."""
    query = "SELECT prompt FROM prompts ORDER BY id"
    if limit and int(limit) > 0:
        query += f" LIMIT {int(limit)}"
    connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute(query)]
    finally:
        connection.close()


def load_prompts_from_file(path: str | Path, limit: int = 0) -> list[str]:
    texts = [
        line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return texts[:int(limit)] if limit and int(limit) > 0 else texts


def default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "user" / "prompt_studio.db"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure prompt diversity (read-only).")
    parser.add_argument("--db", default=str(default_db_path()), help="Studio SQLite database")
    parser.add_argument("--file", default="", help="text file with one prompt per line (overrides --db)")
    parser.add_argument("--limit", type=int, default=0, help="only read the first N prompts")
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY,
                        help="content cosine used to count near-duplicates")
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    args = parser.parse_args(argv)

    if args.file:
        texts = load_prompts_from_file(args.file, args.limit)
    else:
        if not Path(args.db).exists():
            print(f"database not found: {args.db}", file=sys.stderr)
            return 2
        texts = load_prompts_from_db(args.db, args.limit)
    if not texts:
        print("no prompts found", file=sys.stderr)
        return 1
    report = diversity_report(texts, threshold=args.threshold)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
