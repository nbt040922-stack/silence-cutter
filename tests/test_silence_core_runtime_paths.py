from pathlib import Path

from silence_core.runtime_paths import CorePaths
from silence_core.setup import packaged_install_root


def test_packaged_paths_use_programdata_and_program_files(monkeypatch, tmp_path):
    install = tmp_path / "Program Files" / "ContentOps" / "SilenceCore"
    data = tmp_path / "ProgramData" / "ContentOps" / "SilenceCore"
    monkeypatch.setenv("SILENCE_CORE_PACKAGED", "1")
    monkeypatch.setenv("SILENCE_CORE_INSTALL_ROOT", str(install))
    monkeypatch.setenv("SILENCE_CORE_DATA_ROOT", str(data))

    paths = CorePaths.from_environment()

    assert paths.install_root == install.resolve()
    assert paths.data_root == data.resolve()
    assert paths.model_root == data.resolve() / "models"
    assert paths.ffmpeg == install.resolve() / "tools" / "ffmpeg.exe"
    assert "Silence_cutter" not in str(paths.data_root)


def test_ensure_data_layout_creates_mutable_directories(tmp_path):
    paths = CorePaths(tmp_path / "install", tmp_path / "data")

    paths.ensure_data_layout()

    for name in ("config", "state", "queue", "logs", "workspace", "models"):
        assert (paths.data_root / name).is_dir()


def test_packaged_setup_in_subdirectory_resolves_app_root(tmp_path):
    setup_exe = tmp_path / "ContentOps" / "SilenceCore" / "silence_core_setup" / "silence_core_setup.exe"
    assert packaged_install_root(setup_exe) == setup_exe.parent.parent


def test_bootstrap_can_select_a_model_directory(tmp_path, monkeypatch):
    selected = tmp_path / "qwen2.5-vl-3b"
    monkeypatch.setenv("SILENCE_CORE_MODEL_DIR", str(selected))
    paths = CorePaths(tmp_path / "install", tmp_path / "data")

    assert paths.model_path == selected.resolve()
