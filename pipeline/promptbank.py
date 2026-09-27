"""A static prompt template bank -- replaces LLM-based prompt generation
(ADR-0006, superseding ADR-0005). No external dependency, no network, no
secret. Seeded random sampling: the same seed always reproduces the same
prompt set.

Two independent axes: SUBJECTS (grouped by category) and STYLES (a flat
modifier list). A prompt is one subject plus 0-2 sampled style modifiers.
"""

from __future__ import annotations

import random

SUBJECTS: dict[str, list[str]] = {
    "landscapes": [
        "a misty mountain range at dawn",
        "a still alpine lake reflecting the sky",
        "a powerful waterfall crashing into a rocky gorge",
        "a winding river cutting through a green valley",
        "rolling sand dunes in a vast desert",
        "a rugged coastline battered by ocean waves",
        "a dense forest with sunlight filtering through the canopy",
    ],
    "cityscapes": [
        "a dense city skyline at dusk",
        "a narrow cobblestone street in an old town",
        "a bustling night market lit with paper lanterns",
        "a futuristic city with towering illuminated skyscrapers",
        "a quiet suburban street lined with houses",
        "an aerial view of a sprawling metropolis",
    ],
    "animal-portraits": [
        "a close-up portrait of a lion",
        "a close-up portrait of a snowy owl",
        "a close-up portrait of a red fox",
        "a close-up portrait of a horse",
        "a close-up portrait of a timber wolf",
        "a close-up portrait of an elephant",
    ],
    "animal-groups": [
        "a herd of elephants crossing a river",
        "a pack of wolves moving through snow",
        "a flock of flamingos wading in a lake",
        "a school of fish swimming in tight formation",
        "a pride of lions resting together in the shade",
        "a flock of geese flying in formation",
    ],
    "wildlife-scenes": [
        "a safari scene at golden hour with grazing zebras",
        "an eagle soaring above a canyon",
        "a bear fishing in a rushing river",
        "a group of dolphins leaping out of the ocean",
        "a deer standing in a foggy forest clearing",
        "penguins gathered on an icy shore",
    ],
}

STYLES: list[str] = [
    "photorealistic",
    "in the style of a watercolor painting",
    "as an oil painting",
    "as a retro film photograph",
    "in dramatic golden-hour lighting",
    "under a dramatic storm sky",
    "in a minimalist style",
    "with vibrant saturated colors",
    "in muted pastel tones",
]

CATEGORY_NAMES = tuple(SUBJECTS.keys())


def _validate(names: list[str] | None, valid: tuple[str, ...] | list[str], kind: str) -> list[str]:
    if not names:
        return list(valid)
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(f"unknown {kind} {unknown}; choose from {tuple(valid)}")
    return names


def sample(
    count: int,
    seed: int,
    categories: list[str] | None = None,
    styles: list[str] | None = None,
) -> list[str]:
    """Return exactly `count` prompts, deterministic for a given seed.

    categories=None means all categories; styles=None means any style in the
    bank may be sampled (not "no style"). An unknown category/style name is a
    ValueError, not a silently-empty result.
    """
    if count < 1:
        raise ValueError("count must be >= 1")

    chosen_categories = _validate(categories, CATEGORY_NAMES, "category")
    style_pool = _validate(styles, STYLES, "style")

    subject_pool: list[str] = []
    for category in chosen_categories:
        subject_pool.extend(SUBJECTS[category])

    rnd = random.Random(seed)  # noqa: S311 -- reproducible sampling, not security
    max_modifiers = min(2, len(style_pool))
    prompts = []
    for _ in range(count):
        subject = rnd.choice(subject_pool)
        n_modifiers = rnd.randint(0, max_modifiers)
        modifiers = rnd.sample(style_pool, k=n_modifiers) if n_modifiers else []
        prompts.append(", ".join([subject, *modifiers]))
    return prompts
