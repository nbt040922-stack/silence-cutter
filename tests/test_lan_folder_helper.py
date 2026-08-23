import lan_folder_helper
from pathlib import Path


def test_helper_binds_only_to_loopback():
    assert lan_folder_helper.HOST == "127.0.0.1"


def test_normalize_folder_accepts_unc_and_absolute_paths():
    assert lan_folder_helper.normalize_folder(r"\\192.168.1.18\Team 1") == r"\\192.168.1.18\Team 1"
    assert lan_folder_helper.normalize_folder(r"D:\\Output") == r"D:\\Output"


def test_normalize_folder_rejects_relative_paths():
    try:
        lan_folder_helper.normalize_folder("relative-output")
    except ValueError as error:
        assert "absolute" in str(error)
    else:
        raise AssertionError("relative path was accepted")


def test_hidden_vbs_assigns_filesystem_object_with_set():
    script = Path(__file__).parents[1] / "scripts" / "start_folder_helper_hidden.vbs"
    text = script.read_text(encoding="utf-8-sig")
    assert "Set fso = CreateObject(\"Scripting.FileSystemObject\")" in text
