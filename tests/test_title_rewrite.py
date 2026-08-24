import json
import tempfile
import unittest
from pathlib import Path

from formatter.renderer import build_render_jobs
from formatter.title_rewrite import TITLE_REWRITE_PROMPT, _compact_title, rewrite_title_once, safe_filename_title


class FakeClient:
    def __init__(self, response='{"rewritten_title":"50 Dollar Tree Deals Worth Buying"}'):
        self.response = response
        self.calls = []
        self.last_queue_wait = 0.1
        self.last_generation_time = 0.4

    def generate_text(self, images, prompt, **options):
        self.calls.append((images, prompt, options))
        response = self.response.pop(0) if isinstance(self.response, list) else self.response
        if isinstance(response, Exception):
            raise response
        return response


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
    def test_qwen_prompt_requires_mandatory_same_language_safe_rewrite(self):
        self.assertIn("Always rewrite the input title", TITLE_REWRITE_PROMPT)
        self.assertIn("same language and writing system", TITLE_REWRITE_PROMPT)
        self.assertIn("TikTok Community Guidelines", TITLE_REWRITE_PROMPT)
        self.assertIn("never return the original title", TITLE_REWRITE_PROMPT)

    def test_compact_fallback_preserves_meaningful_prefix_for_long_japanese_title(self):
        original = "【コストコ保存】購入品のその後はこうやって仕分け・冷凍してます！近況報告もあります。冷凍方法と保存期間を詳しく紹介します"
        compact = _compact_title(original)
        self.assertLessEqual(len(compact), 42)
        self.assertIn("コストコ", compact)
        self.assertIn("仕分け", compact)

    def test_long_model_title_falls_back_to_compact_title(self):
        original = "【コストコ保存】購入品のその後はこうやって仕分け・冷凍してます！近況報告もあります。冷凍方法と保存期間を詳しく紹介します"
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([
                json.dumps({"rewritten_title": original}, ensure_ascii=False),
                json.dumps({"rewritten_title": original}, ensure_ascii=False),
            ])
            result = rewrite_title_once(
                directory, original, directory, source_id="id", client=client,
            )
        self.assertEqual(result["status"], "FALLBACK")
        self.assertLess(len(result["rewritten_title"]), len(original))
        self.assertIn("コストコ", result["rewritten_title"])

    def test_cached_long_title_is_not_reused(self):
        original = "A very long source title that should be shortened before it reaches the banner"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "title_rewrite.json"
            path.write_text(json.dumps({
                "rewritten_title": original,
                "filename_base": original,
            }), encoding="utf-8")
            result = rewrite_title_once(
                directory, original, directory, source_id="id",
                client=FakeClient('{"rewritten_title":"Why This Shorter Title Matters"}'),
            )
        self.assertEqual(result["rewritten_title"], "Why This Shorter Title Matters")

    def test_old_prompt_cache_is_rewritten(self):
        original = "Original title"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "title_rewrite.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "rewritten_title": "Old cached title",
                "filename_base": "Old cached title",
            }), encoding="utf-8")
            result = rewrite_title_once(
                directory, original, directory, source_id="id",
                client=FakeClient('{"rewritten_title":"Why This Original Title Matters"}'),
            )
        self.assertEqual(result["rewritten_title"], "Why This Original Title Matters")

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

    def test_quality_guard_retries_once_then_applies(self):
        original = "50 *NEW* Dollar Tree Deals you NEED to buy! (from the pro!)"
        bad_titles = [
            "Dollar Tree Deals",
            "50 Dollar Tree Deals",
            "Here is the rewritten title: 50 Dollar Tree Deals",
            "50 DOLLAR TREE DEALS YOU NEED NOW",
            "50 Dollar Tree Deals You Need!!!",
            "40 Dollar Tree Finds Actually Worth Buying",
            "50 Dollar Tree Finds " + "Actually Worth Buying " * 10,
        ]
        good = '{"rewritten_title":"50 Dollar Tree Finds Actually Worth Buying"}'
        for bad in bad_titles:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory:
                client = FakeClient([
                    json.dumps({"rewritten_title": bad}), good,
                ])
                result = rewrite_title_once(
                    directory, original, directory, source_id="id", client=client,
                )
                self.assertEqual(result["status"], "APPLIED")
                self.assertEqual(result["generation_count"], 2)
                self.assertEqual(result["retry_count"], 1)
                self.assertEqual(len(result["guard_rejections"]), 1)

    def test_quality_guard_retries_at_most_once_then_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([
                '{"rewritten_title":"Retirement"}',
                '{"rewritten_title":"Retirement Tips"}',
            ])
            result = rewrite_title_once(
                directory, "Why Retirement Changed Everything!", directory,
                source_id="id", client=client,
            )
        self.assertEqual(result["status"], "FALLBACK")
        self.assertEqual(result["generation_count"], 2)
        self.assertEqual(len(client.calls), 2)

    def test_worker_failure_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient(TimeoutError("timeout"))
            result = rewrite_title_once(
                directory, "Original Title", directory, source_id="id", client=client,
            )
        self.assertEqual(result["status"], "FALLBACK")
        self.assertEqual(result["generation_count"], 1)
        self.assertEqual(len(client.calls), 1)

    def test_markdown_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([
                '```json\n{"rewritten_title":"A Better Title"}\n```',
                '{"rewritten_title":"Why This Better Title Matters"}',
            ])
            result = rewrite_title_once(
                directory, "Why This Title Matters", directory,
                source_id="id", client=client,
            )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["generation_count"], 2)

    def test_source_language_change_is_retried(self):
        cases = [
            ("Tôi sống một mình 30 ngày và điều này đã thay đổi tôi",
             "30 Days Alone Changed My Life",
             "Vì sao 30 ngày sống một mình đã thay đổi tôi"),
            ("移居日本前一定要知道的7件事",
             "7 Things to Know Before Moving to Japan",
             "移居日本前必须知道的7件事"),
        ]
        for original, bad, good in cases:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                client = FakeClient([
                    json.dumps({"rewritten_title": bad}, ensure_ascii=False),
                    json.dumps({"rewritten_title": good}, ensure_ascii=False),
                ])
                result = rewrite_title_once(
                    directory, original, directory, source_id="id", client=client,
                )
                self.assertEqual(result["status"], "APPLIED")
                self.assertEqual(result["rewritten_title"], good)
                self.assertEqual(result["retry_count"], 1)

    def test_retry_reuses_artifact_without_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            first = FakeClient('{"rewritten_title":"Why the Original Story Still Matters"}')
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
            (root / "Clean Title Worth Reading_PART_1.mp4").write_bytes(b"other")
            result = rewrite_title_once(
                root / "job", "Original", root, source_id="abcdefghijk",
                client=FakeClient('{"rewritten_title":"Clean Title Worth Reading"}'),
            )
            jobs = build_render_jobs(plan(result["filename_base"]), root)
        self.assertEqual(result["filename_base"], "Clean Title Worth Reading_abcdefghijk")
        self.assertEqual([item["path"].name for item in jobs], [
            "Clean Title Worth Reading_abcdefghijk_PART_1.mp4",
            "Clean Title Worth Reading_abcdefghijk_PART_2.mp4",
            "Clean Title Worth Reading_abcdefghijk_PART_3.mp4",
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

    def test_quality_fixture_has_required_coverage(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "title_rewrite_quality_titles.json")
            .read_text(encoding="utf-8")
        )
        categories = {item["category"] for item in fixture}
        self.assertGreaterEqual(len(fixture), 30)
        self.assertTrue({
            "retirement", "personal_finance", "grocery_frugal", "travel_expat",
            "lifestyle", "housing", "list", "warning", "question", "personal_story",
        }.issubset(categories))


if __name__ == "__main__":
    unittest.main()
