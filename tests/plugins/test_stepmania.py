"""Tests for the built-in StepMania random-selection plugin."""

import json
import random
import zipfile
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from haven_cli.plugins.base import MediaSource
from haven_cli.plugins.builtin.stepmania import StepManiaPlugin
from haven_cli.plugins.builtin.stepmania.providers import (
    PackCandidate,
    StepManiaOnlineProvider,
)
from haven_cli.plugins.builtin.stepmania.settings import SettingsError, load_settings


def test_load_settings_defaults_and_song_validation() -> None:
    settings = load_settings({})
    assert settings.providers == ("stepmania_online",)
    assert settings.count == 1
    assert settings.unit == "pack"
    assert settings.generic_file_accept == ("*",)
    assert settings.upload_as_pack is True
    assert settings.pack_manifest_max_songs == 250

    with pytest.raises(SettingsError, match="unit = 'song' requires"):
        load_settings({"providers": ["stepmania_online"], "unit": "song"})

    song_settings = load_settings({"providers": ["etterna"], "unit": "song"})
    assert song_settings.unit == "song"


def test_smo_candidate_from_catalog_row() -> None:
    settings = load_settings({"providers": ["stepmania_online"]})
    provider = StepManiaOnlineProvider(settings, Mock(spec=httpx.AsyncClient))
    candidate = provider._candidate_from_row(
        {
            "ID": "42",
            "Pack Name": "Keyboard Pack",
            "Song Count": "7",
            "Size": "12345",
            "Sync": "n/a",
            "PackType": "KB",
            "Substyle": "None",
            "Min Version": "Stepmania 5",
        }
    )

    assert candidate is not None
    assert candidate.provider == "stepmania_online"
    assert candidate.pack_id == "42"
    assert candidate.name == "Keyboard Pack"
    assert candidate.download_url == "https://stepmaniaonline.net/download/pack/42/"
    assert candidate.song_count == 7
    assert candidate.size_bytes == 12345
    assert candidate.metadata["pack_type"] == "kb"
    assert candidate.metadata["min_version"] == "Stepmania 5"


@pytest.mark.asyncio
async def test_discover_sources_for_uses_seeded_random_draw(monkeypatch, tmp_path: Path) -> None:
    class FakeProvider:
        async def discover(self, *, count: int, unit: str, rng: random.Random, unavailable):
            return [
                PackCandidate(
                    provider="stepmania_online",
                    pack_id=str(index),
                    name=f"Pack {index}",
                    download_url=f"https://example.test/{index}.zip",
                )
                for index in range(count)
            ]

    monkeypatch.setattr(
        "haven_cli.plugins.builtin.stepmania.plugin.provider_for",
        lambda *args, **kwargs: FakeProvider(),
    )
    plugin = StepManiaPlugin(
        {
            "providers": ["stepmania_online"],
            "count": 3,
            "download_dir": str(tmp_path / "downloads"),
        }
    )
    plugin._download_dir = tmp_path / "downloads"
    plugin._state_path = plugin._download_dir / ".stepmania_state.json"
    plugin._client = Mock(spec=httpx.AsyncClient)
    plugin._initialized = True

    sources = await plugin.discover_sources_for({"stepmania_seed": 123})

    assert len(sources) == 3
    assert {source.source_id for source in sources} == {
        "stepmania_online:0",
        "stepmania_online:1",
        "stepmania_online:2",
    }
    assert all(source.media_type == "stepmania" for source in sources)
    assert sources[0].metadata["stepmania_candidate"]["download_url"].endswith(".zip")


@pytest.mark.asyncio
async def test_archive_extracts_zip_and_emits_generic_pipeline_options(
    monkeypatch, tmp_path: Path
) -> None:
    plugin = StepManiaPlugin(
        {
            "download_dir": str(tmp_path / "stepmania"),
            "upload_as_pack": False,
            "extract_archive": True,
            "include_extensions": [".sm", ".png"],
            "generic_file_accept": [".sm", ".png"],
            "keep_zip": False,
        }
    )
    await plugin.initialize()
    try:
        zip_path = plugin._download_dir / ".tmp" / "test-pack.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("Songs/Song One/song.sm", "#SMANI")
            archive.writestr("Songs/Song One/banner.png", "png")
            archive.writestr("Songs/Song One/readme.txt", "ignored")
            archive.writestr("__MACOSX/ignore.png", "ignored")

        async def fake_download(candidate, settings):
            return zip_path

        monkeypatch.setattr(plugin, "_download_zip", fake_download)
        candidate = PackCandidate(
            provider="stepmania_online",
            pack_id="42",
            name="Test Pack",
            download_url="https://example.test/pack-42.zip",
            song_count=1,
            size_bytes=zip_path.stat().st_size,
        )
        source = MediaSource(
            source_id=candidate.source_id,
            title=candidate.name,
            uri=candidate.download_url,
            media_type="stepmania",
            metadata={"stepmania_candidate": candidate.to_dict()},
        )

        result = await plugin.archive(source)

        assert result.success, result.error
        output_names = {Path(path).name for path in result.metadata["output_paths"]}
        assert output_names == {"song.sm", "banner.png"}
        assert result.metadata["pipeline_options"]["generic_files_enabled"] is True
        assert result.metadata["pipeline_options"]["generic_file_accept"] == [".sm", ".png"]
        marker = (
            plugin._download_dir
            / "stepmania_online"
            / "Test Pack"
            / ".haven-stepmania.json"
        )
        assert marker.exists()
        assert not zip_path.exists()
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_archive_uploads_pack_zip_with_dance_manifest(
    monkeypatch, tmp_path: Path
) -> None:
    """Pack mode keeps the ZIP whole and emits a FeetOnFire stepmania-pack manifest."""
    plugin = StepManiaPlugin(
        {
            "download_dir": str(tmp_path / "stepmania"),
            "upload_as_pack": True,
        }
    )
    await plugin.initialize()
    try:
        zip_path = plugin._download_dir / ".tmp" / "pack.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr(
                "Songs/Song One/song.sm",
                "#TITLE:Song One;\n#ARTIST:Artist A;\n#MUSIC:song.ogg;\n",
            )
            archive.writestr(
                "Songs/Song Two/song.ssc",
                "#TITLE:Song Two;\n#ARTIST:Artist B;\n#MUSIC:song2.ogg;\n",
            )
            archive.writestr("__MACOSX/junk.txt", "ignored")

        async def fake_download(candidate, settings):
            return zip_path

        monkeypatch.setattr(plugin, "_download_zip", fake_download)
        candidate = PackCandidate(
            provider="stepmania_online",
            pack_id="10458",
            name="Sai Memes",
            download_url="https://example.test/pack-10458.zip",
            song_count=2,
            size_bytes=zip_path.stat().st_size,
        )
        source = MediaSource(
            source_id=candidate.source_id,
            title=candidate.name,
            uri=candidate.download_url,
            media_type="stepmania",
            metadata={"stepmania_candidate": candidate.to_dict()},
        )

        result = await plugin.archive(source)

        assert result.success, result.error
        # One ZIP output, kept whole (not deleted, not extracted).
        assert len(result.metadata["output_paths"]) == 1
        output = Path(result.metadata["output_paths"][0])
        assert output.suffix == ".zip"
        assert output.is_file()

        options = result.metadata["pipeline_options"]
        assert options["arkiv_grp"] == "haven.file.full"
        dance = options["arkiv_payload_extra"]["dance"]
        assert dance["kind"] == "stepmania-pack"
        assert dance["version"] == 1
        assert dance["album"] == "Sai Memes"
        assert dance["sample"] is True
        assert {(song["title"], song["artist"]) for song in dance["songs"]} == {
            ("Song One", "Artist A"),
            ("Song Two", "Artist B"),
        }

        marker = json.loads(
            (
                plugin._download_dir
                / "stepmania_online"
                / "Sai Memes"
                / ".haven-stepmania.json"
            ).read_text()
        )
        assert marker["manifest"]["kind"] == "stepmania-pack"
    finally:
        await plugin.shutdown()
