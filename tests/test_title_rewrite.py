import json
import tempfile
import unittest
from pathlib import Path

from formatter.renderer import build_render_jobs
from formatter.title_rewrite import rewrite_title_once, safe_filename_title


class FakeClient:
    def __init__(self, response='{"rewritten_title":"50 Dollar Tree Deals Worth Buying"}'):
        self.response = response
        self.calls = []
        self.last_queue_wait = 0.1
        self.last_generation_time = 0.4

    def generate_text(self, images, prompt, **options):
        self.calls.append((images, prompt, options))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def plan(filename_base: str, part_count: int = 3):
    return {
        "formatter_status": "PLANNED", "part_count": part_count,
        "clean_video_duration": float(part_count), "direct_source_render": False,
        "filename_base": filename_base,
        "parts": [{"index": index, "label": f"PART {index}",
                   "clean_start": index - 1, "clean_end": index}
                  for index in range(1, part_count + 1)],
    }


class TitleRewriteTests(unittest.TestCase):
    def test_text_only_worker_task_valid_json_and_one_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            result = rewrite_title_once(
                directory, "50 *NEW* Dollar Tree Deals you NEED to buy! (from the pro!)",
                directory, source_id="abcdefghijk", client=client,
            )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["rewritten_title"], "50 Dollar Tree Deals Worth Buying")
        self.assertEqual(result["generation_count"], 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], [])
        self.assertEqual(client.calls[0][2]["task"], "title_rewrite")
        self.assertEqual(client.calls[0][2]["max_new_tokens"], 32)
        self.assertIn("SAME LANGUAGE", client.calls[0][1])

    def test_invalid_response_and_timeout_fall_back(self):
        for response in ("not json", TimeoutError("timeout"), '{"rewritten_title":""}'):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                result = rewrite_title_once(
                    directory, "Original Title", directory,
                    source_id="abcdefghijk", client=FakeClient(response),
                )
                self.assertEqual(result["status"], "FALLBACK")
                self.assertEqual(result["rewritten_title"], "Original Title")

    def test_retry_reuses_artifact_without_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            first = FakeClient()
            original = rewrite_title_once(
                directory, "Original", directory, source_id="id", client=first,
            )
            second = FakeClient('{"rewritten_title":"Changed"}')
            retry = rewrite_title_once(
                directory, "Original", directory, source_id="id", client=second,
            )
        self.assertEqual(retry, original)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(second.calls, [])

    def test_sanitizer_invalid_unicode_emoji_truncation_and_empty(self):
        japanese = "\u65e5\u672c\u8a9e"
        subject = "\u984c\u540d"
        self.assertEqual(
            safe_filename_title(f"  {japanese} : {subject}? \U0001f3ac\n  "),
            f"{japanese} _ {subject}_",
        )
        self.assertEqual(
            safe_filename_title("\U0001f3ac\u2728", fallback_id="abc123"),
            "video_abc123",
        )
        value = safe_filename_title("word " * 100)
        self.assertLessEqual(len(value), 120)
        self.assertFalse(value.endswith((" ", ".")))

    def test_collision_uses_one_source_suffix_for_all_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Clean Title_PART_1.mp4").write_bytes(b"other")
            result = rewrite_title_once(
                root / "job", "Original", root, source_id="abcdefghijk",
                client=FakeClient('{"rewritten_title":"Clean Title"}'),
            )
            jobs = build_render_jobs(plan(result["filename_base"]), root)
        self.assertEqual(result["filename_base"], "Clean Title_abcdefghijk")
        self.assertEqual([item["path"].name for item in jobs], [
            "Clean Title_abcdefghijk_PART_1.mp4",
            "Clean Title_abcdefghijk_PART_2.mp4",
            "Clean Title_abcdefghijk_PART_3.mp4",
        ])

    def test_normal_two_and_enhanced_three_part_naming(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vietnamese = "T\u00ean Vi\u1ec7t"
            japanese = "\u65e5\u672c\u8a9e"
            self.assertEqual([item["path"].name for item in build_render_jobs(plan(vietnamese, 2), root)], [
                f"{vietnamese}_PART_1.mp4", f"{vietnamese}_PART_2.mp4",
            ])
            self.assertEqual([item["path"].name for item in build_render_jobs(plan(japanese, 3), root)], [
                f"{japanese}_PART_1.mp4", f"{japanese}_PART_2.mp4", f"{japanese}_PART_3.mp4",
            ])

    def test_artifact_schema_persists_filename_source_of_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            result = rewrite_title_once(
                directory, "Original", directory, source_id="id", client=FakeClient(),
            )
            stored = json.loads((Path(directory) / "title_rewrite.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, result)
        self.assertEqual(stored["model_load_count"], 0)


if __name__ == "__main__":
    unittest.main()
