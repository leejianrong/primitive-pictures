import pytest

from config import (
    ALL_BACKEND_NAMES,
    ALL_VIDEO_BACKEND_NAMES,
    BACKENDS,
    DEFAULT_VIDEO_MODEL,
    VIDEO_BACKENDS,
    resolve_backends,
    resolve_video_backend,
)


def test_all_backend_names_present_in_registry():
    assert set(ALL_BACKEND_NAMES) == set(BACKENDS.keys())
    assert len(ALL_BACKEND_NAMES) == 4


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


def test_all_video_backend_names_present_in_registry():
    assert set(ALL_VIDEO_BACKEND_NAMES) == set(VIDEO_BACKENDS.keys())


def test_default_video_model_is_registered():
    assert DEFAULT_VIDEO_MODEL in VIDEO_BACKENDS


def test_resolve_video_backend_single_name():
    backend = resolve_video_backend("wan2.2-ti2v-5b")
    assert backend.name == "wan2.2-ti2v-5b"


def test_resolve_video_backend_unknown_raises():
    with pytest.raises(ValueError, match="unknown video model"):
        resolve_video_backend("not-a-real-video-model")


def test_video_backend_num_frames_is_4k_plus_1():
    # Wan's temporal VAE expects num_frames of the form 4k+1 -- catch a
    # config typo here rather than on a real (paid) pod run.
    for backend in VIDEO_BACKENDS.values():
        assert (backend.num_frames - 1) % 4 == 0


def test_every_video_backend_has_a_license_recorded():
    for backend in VIDEO_BACKENDS.values():
        assert backend.license


def test_video_pod_image_has_torch_new_enough_for_diffusers_wan_support():
    # diffusers 0.35 calls scaled_dot_product_attention(enable_gqa=...), added in torch 2.5; a
    # 2.4.0 image crashed a real pod on exactly that (ADR-0008's addendum).
    from config import DEFAULT_VIDEO_POD_IMAGE

    torch_version = DEFAULT_VIDEO_POD_IMAGE.split(":", 1)[1].split("-", 1)[0]
    major, minor = (int(part) for part in torch_version.split(".")[:2])
    assert (major, minor) >= (2, 5)
