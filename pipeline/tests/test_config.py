import pytest

from config import ALL_BACKEND_NAMES, BACKENDS, resolve_backends


def test_all_backend_names_present_in_registry():
    assert set(ALL_BACKEND_NAMES) == set(BACKENDS.keys())
    assert len(ALL_BACKEND_NAMES) == 3


def test_resolve_backends_compare_returns_all():
    backends = resolve_backends("compare")
    assert {b.name for b in backends} == set(ALL_BACKEND_NAMES)


def test_resolve_backends_single_name():
    backends = resolve_backends("sd15")
    assert [b.name for b in backends] == ["sd15"]


def test_resolve_backends_unknown_raises():
    with pytest.raises(ValueError, match="unknown model"):
        resolve_backends("not-a-real-model")


def test_every_backend_has_a_license_recorded():
    for backend in BACKENDS.values():
        assert backend.license
