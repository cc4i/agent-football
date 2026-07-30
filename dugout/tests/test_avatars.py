import pytest

from tools import avatars


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

    def fake_generate_one(client, prompt, filename, make_default_gk):
        calls.append(filename)
        return f"/sprites/{filename}"

    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one", fake_generate_one)

    result = avatars.generate_team_avatars("blue", "black", "wolf", "blond hair")

    assert result["team"] == "blue"
    assert result["sprite_sheet"] == "/sprites/player_blue.png"
    assert result["goalkeeper"] == "/sprites/goalkeeper_blue.png"
    assert calls == ["player_blue.png", "goalkeeper_blue.png"]
