from pathlib import Path

import folder_helper_installer as helper


def test_install_path_is_user_local_and_stable(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "LOCAL_APP_DATA", tmp_path)
    assert helper.install_path() == tmp_path / "SilenceCutter" / "folder-helper.exe"


def test_service_command_uses_installed_executable():
    path = Path(r"C:\Users\demo\AppData\Local\SilenceCutter\folder-helper.exe")
    assert helper.service_command(path) == [str(path), "--service"]


def test_source_script_has_single_file_install_mode():
    source = Path(helper.__file__).read_text(encoding="utf-8")
    assert "--service" in source
    assert "CurrentVersion\\Run" in source
