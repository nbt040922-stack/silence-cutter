from pathlib import Path

from installer_setup.inventory import discover_models
from installer_setup.models import ModelSpec
from installer_setup.recommendation import recommend_qwen
from installer_setup.downloads import download_model, ensure_qwen_model


def test_discover_models_prefers_workspace_roots(tmp_path: Path):
    root = tmp_path / "local_models" / "Qwen2.5-VL-7B-Instruct-AWQ"
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}", encoding="utf-8")
    specs = [ModelSpec("qwen", "Qwen2.5-VL-7B-Instruct-AWQ", required_files=("config.json",))]

    found = discover_models(specs, roots=[tmp_path / "local_models"])

    assert found[0].status == "verified"
    assert found[0].path == root


def test_missing_model_reports_source_not_configured(tmp_path: Path):
    spec = ModelSpec("qwen", "missing", required_files=("config.json",))
    found = discover_models([spec], roots=[tmp_path])

    assert found[0].status == "missing"
    assert found[0].code == "MODEL_SOURCE_NOT_CONFIGURED"


def test_recommendation_uses_fit_not_gpu_name():
    specs = [
        ModelSpec("qwen", "small", min_vram_gb=6),
        ModelSpec("qwen", "large", min_vram_gb=12),
    ]
    hardware = {"cuda_available": True, "vram_gb": 8, "nvenc_available": True}

    assert recommend_qwen(specs, hardware).name == "small"


def test_download_model_uses_huggingface_source(tmp_path: Path):
    spec = ModelSpec("qwen", "qwen-model", required_files=("config.json",), source="Qwen/test")
    calls = {}

    def fake_snapshot_download(**kwargs):
        calls.update(kwargs)
        target = Path(kwargs["local_dir"])
        target.mkdir(parents=True)
        (target / "config.json").write_text("{}", encoding="utf-8")
        return str(target)

    result = download_model(spec, tmp_path, downloader=fake_snapshot_download)

    assert result.status == "verified"
    assert calls["repo_id"] == "Qwen/test"
    assert calls["local_dir"] == str(tmp_path / "qwen-model")


def test_ensure_qwen_model_downloads_only_when_missing(tmp_path: Path):
    manifest = tmp_path / "model_manifest.json"
    manifest.write_text(
        '{"models":[{"kind":"qwen","name":"qwen-model",'
        '"required_files":["config.json"],"source":"Qwen/test"}]}',
        encoding="utf-8",
    )
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["local_dir"])
        target.mkdir(parents=True)
        (target / "config.json").write_text("{}", encoding="utf-8")
        return str(target)

    result = ensure_qwen_model(manifest, tmp_path / "models", downloader=fake_snapshot_download)

    assert result.status == "verified"
    assert len(calls) == 1


def test_ensure_qwen_model_selects_variant_for_vram(tmp_path: Path):
    manifest = tmp_path / "model_manifest.json"
    manifest.write_text(
        '{"models":['
        '{"kind":"qwen","name":"3b","required_files":["config.json"],"min_vram_gb":6,"source":"Qwen/3b"},'
        '{"kind":"qwen","name":"7b","required_files":["config.json"],"min_vram_gb":10,"source":"Qwen/7b"}]}',
        encoding="utf-8",
    )

    def fake_snapshot_download(**kwargs):
        target = Path(kwargs["local_dir"])
        target.mkdir(parents=True)
        (target / "config.json").write_text("{}", encoding="utf-8")
        return str(target)

    result = ensure_qwen_model(manifest, tmp_path / "models", hardware={"vram_gb": 8}, downloader=fake_snapshot_download)

    assert result.spec.name == "3b"
