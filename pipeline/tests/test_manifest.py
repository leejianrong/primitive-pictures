import manifest


def test_round_trip_preserves_schema_version_and_mixed_status(tmp_path):
    m = manifest.Manifest(
        run_id="run-test-0001",
        items=[
            manifest.Item(
                index=0,
                model="sd15",
                prompt="a cat",
                seed_image="out/sd15/000-a-cat.png",
                status="ok",
                primitive_png="out/sd15/000-a-cat.primitive.png",
                primitive_svg="out/sd15/000-a-cat.primitive.svg",
                generation_seconds=1.23,
            ),
            manifest.Item(
                index=1,
                model="sd15",
                prompt="a broken one",
                seed_image="out/sd15/001-a-broken-one.png",
                status="failed",
                error="primitive exited 1: unrecognized file extension",
            ),
        ],
    )

    path = tmp_path / "manifest.json"
    manifest.write(path, m)
    loaded = manifest.read(path)

    assert loaded.run_id == m.run_id
    assert loaded.schema_version == manifest.SCHEMA_VERSION
    assert loaded.items == m.items
    assert loaded.ok_count == 1
    assert loaded.failed_count == 1


def test_write_produces_readable_json(tmp_path):
    m = manifest.Manifest(run_id="run-test-0002", items=[])
    path = tmp_path / "manifest.json"
    manifest.write(path, m)

    import json

    payload = json.loads(path.read_text())
    assert payload["run_id"] == "run-test-0002"
    assert payload["schema_version"] == manifest.SCHEMA_VERSION
    assert payload["items"] == []
