import io

import pytest
from PIL import Image, ImageDraw

from tools import avatars

LIME = (162, 247, 60)


def sheet_bytes(figures=4, size=(1024, 512)):
    """What the model hands back: figures on a green screen, no grid."""
    image = Image.new("RGB", size, LIME)
    draw = ImageDraw.Draw(image)
    for i in range(figures):
        x = 40 + i * 230
        draw.rectangle([x, 60, x + 120, 420], fill=(26, 26, 26))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeClient:
    """Stands in for genai.Client, which only ever gets asked for one image."""

    def __init__(self, image_bytes=None):
        self.models = self
        self._bytes = image_bytes

    def generate_content(self, model, contents):
        part = type("Part", (), {"inline_data": None})()
        if self._bytes is not None:
            part.inline_data = type("Data", (), {"data": self._bytes})()
        content = type("Content", (), {"parts": [part]})()
        return type("Response", (), {
            "candidates": [type("Candidate", (), {"content": content})()]})()


def test_unknown_team_is_rejected():
    with pytest.raises(ValueError, match="unknown team"):
        avatars.generate_team_avatars("green", "blue", "star", "spiky hair")


def test_no_image_from_the_model_raises_a_typed_error(monkeypatch):
    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one", lambda *a, **k: None)
    with pytest.raises(avatars.AvatarGenerationError, match="no image"):
        avatars.generate_team_avatars("blue", "black", "wolf", "blond hair")


def test_a_successful_run_reports_both_written_paths(monkeypatch):
    calls = []

    def fake_generate_one(client, prompt, filename):
        calls.append(filename)
        return f"/sprites/{filename}"

    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one", fake_generate_one)

    result = avatars.generate_team_avatars("blue", "black", "wolf", "blond hair")

    assert result["team"] == "blue"
    assert result["sprite_sheet"] == "/sprites/player_blue_team.png"
    assert result["goalkeeper"] == "/sprites/goalkeeper_blue_team.png"
    assert calls == ["player_blue_team.png", "goalkeeper_blue_team.png"]


def test_the_opponent_gets_its_own_pair_of_sheets(monkeypatch):
    calls = []

    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one",
                        lambda c, p, filename: calls.append(filename) or filename)

    avatars.generate_team_avatars("red", "white", "tiger", "dark hair")

    assert calls == ["player_red_team.png", "goalkeeper_red_team.png"]


def test_what_the_model_draws_is_cut_into_frames_before_it_lands(tmp_path,
                                                                monkeypatch):
    # The pitch reads frames out of the atlas. A raw generation has none, and
    # cutting one on a grid is what put a second keeper in the frame.
    monkeypatch.setattr(avatars, "SPRITE_DIR", tmp_path)

    written = avatars._generate_one(
        FakeClient(sheet_bytes()), "a prompt", "player_blue_team.png")

    assert written == str(tmp_path / "player_blue_team.png")
    import json
    atlas = json.loads((tmp_path / "player_blue_team.json").read_text())
    assert list(atlas["frames"]) == ["idle", "run_0", "run_1", "kick"]


def test_the_keeper_sheet_is_cut_by_the_keeper_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(avatars, "SPRITE_DIR", tmp_path)

    avatars._generate_one(
        FakeClient(sheet_bytes(figures=3)), "a prompt", "goalkeeper_red_team.png")

    import json
    atlas = json.loads((tmp_path / "goalkeeper_red_team.json").read_text())
    assert list(atlas["frames"]) == ["ready_0", "ready_1", "ready_2"]


def test_the_green_screen_is_gone_from_what_lands_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(avatars, "SPRITE_DIR", tmp_path)

    avatars._generate_one(
        FakeClient(sheet_bytes()), "a prompt", "player_blue_team.png")

    saved = Image.open(tmp_path / "player_blue_team.png")
    assert saved.getpixel((0, 0))[3] == 0


def test_a_sheet_the_slicer_cannot_read_is_reported_as_a_failure(tmp_path,
                                                                 monkeypatch):
    # An all-green generation keys out to nothing at all.
    monkeypatch.setattr(avatars, "SPRITE_DIR", tmp_path)
    blank = sheet_bytes(figures=0)

    with pytest.raises(avatars.AvatarGenerationError, match="player_blue_team"):
        avatars._generate_one(FakeClient(blank), "a prompt", "player_blue_team.png")


def test_a_model_that_returns_nothing_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(avatars, "SPRITE_DIR", tmp_path)

    assert avatars._generate_one(FakeClient(), "a prompt",
                                 "player_blue_team.png") is None
    assert list(tmp_path.iterdir()) == []
