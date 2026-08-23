from pathlib import Path

from silence_cutter.runtime_paths import model_reference


def test_model_reference_prefers_user_data_model(tmp_path, monkeypatch):
    model = tmp_path / "models" / "SenseVoiceSmall"
    model.mkdir(parents=True)
    monkeypatch.setenv("SILENCE_CUTTER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SILENCE_CUTTER_RESOURCE_DIR", raising=False)

    assert model_reference("SenseVoiceSmall", "remote/model") == str(model)

