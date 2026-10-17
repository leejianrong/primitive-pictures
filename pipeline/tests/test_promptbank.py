import pytest

import promptbank


def test_sample_returns_exact_count():
    prompts = promptbank.sample(count=7, seed=1)
    assert len(prompts) == 7
    assert all(p.strip() for p in prompts)


def test_same_seed_is_reproducible():
    a = promptbank.sample(count=10, seed=42, categories=["landscapes"])
    b = promptbank.sample(count=10, seed=42, categories=["landscapes"])
    assert a == b


def test_different_seeds_usually_differ():
    a = promptbank.sample(count=10, seed=1)
    b = promptbank.sample(count=10, seed=2)
    assert a != b


def test_category_filter_restricts_subject_pool():
    prompts = promptbank.sample(count=20, seed=3, categories=["cityscapes"])
    landscape_only_phrase = "alpine lake"
    assert all(landscape_only_phrase not in p for p in prompts)


def test_unknown_category_raises():
    with pytest.raises(ValueError, match="unknown category"):
        promptbank.sample(count=1, seed=1, categories=["not-a-real-category"])


def test_sample_works_with_a_single_style_restriction():
    # regression: rnd.sample(pool, k=2) raises if len(pool) < 2 -- a single
    # --style value must not crash n_modifiers=2 draws.
    prompts = promptbank.sample(count=20, seed=7, styles=["photorealistic"])
    assert len(prompts) == 20


def test_unknown_style_raises():
    with pytest.raises(ValueError, match="unknown style"):
        promptbank.sample(count=1, seed=1, styles=["not-a-real-style"])


def test_count_must_be_positive():
    with pytest.raises(ValueError, match="count must be"):
        promptbank.sample(count=0, seed=1)


def test_category_names_cover_the_agreed_five():
    assert set(promptbank.CATEGORY_NAMES) == {
        "landscapes",
        "cityscapes",
        "animal-portraits",
        "animal-groups",
        "wildlife-scenes",
    }


def test_every_prompt_has_composition_and_lighting():
    for p in promptbank.sample(count=40, seed=5):
        assert any(c in p for c in promptbank.DETAILS["composition"])
        assert any(c in p for c in promptbank.DETAILS["lighting"])


def test_details_do_not_change_subject_or_style_choice():
    """Details use their own stream: the subject and styles per seed match a details-free draw."""
    import random

    seed = 11
    rnd = random.Random(seed)  # noqa: S311 -- mirrors promptbank's reproducible sampling
    pool = [s for subs in promptbank.SUBJECTS.values() for s in subs]
    expected = []
    for _ in range(15):
        subject = rnd.choice(pool)
        n = rnd.randint(0, 2)
        if n:
            rnd.sample(promptbank.STYLES, k=n)
        expected.append(subject)
    got = promptbank.sample(count=15, seed=seed)
    assert [p.split(", ")[0] for p in got] == expected


def test_lens_clause_skipped_for_painting_styles():
    prompts = promptbank.sample(count=60, seed=9, styles=["as an oil painting"])
    painted = [p for p in prompts if "oil painting" in p]
    assert painted
    assert all(not any(c in p for c in promptbank.DETAILS["lens"]) for p in painted)


def test_prompts_are_longer_than_subject_alone():
    for p in promptbank.sample(count=10, seed=2):
        assert len(p.split(", ")) >= 3
