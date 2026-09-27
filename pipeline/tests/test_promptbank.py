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
