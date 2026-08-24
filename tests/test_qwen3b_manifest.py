import json
from pathlib import Path


def test_qwen3b_manifest_has_valid_huggingface_file_urls_and_processor_files():
    manifest_path = Path(__file__).parents[1] / "installer" / "core_model_manifest_3b.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in manifest["files"]}

    assert "preprocessor_config.json" in names
    assert "tokenizer_config.json" in names
    for item in manifest["files"]:
        assert "/=true" not in item["url"]
        assert item["url"].endswith(f"/{item['name']}?download=true")
