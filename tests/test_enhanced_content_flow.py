import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enhanced_content_flow.flow import EnhancedFlowSkipped, _format_plan, recover_enhanced_parts, recover_three_parts
from formatter.renderer import build_render_jobs


def candidates():
    return [
        {"center": 100, "score": .95, "topic": "one", "reason": "one"},
        {"center": 350, "score": .90, "topic": "two", "reason": "two"},
        {"center": 650, "score": .85, "topic": "three", "reason": "three"},
        {"center": 900, "score": .80, "topic": "four", "reason": "four"},
    ]


class EnhancedContentFlowTests(unittest.TestCase):
    @patch("enhanced_content_flow.flow.probe_media", return_value={"duration": 1200})
    def test_unusable_selection_is_classified_without_formatting(self, _probe):
        from enhanced_content_flow.flow import run_enhanced_content_flow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            with self.assertRaises(EnhancedFlowSkipped):
                run_enhanced_content_flow(
                    source, root, "Title", root / "job",
                    selector=lambda *_a, **_k: {"status": "LONG_VIDEO_SELECTOR_SKIPPED", "reason": "one candidate"},
                )
            artifact = json.loads((root / "job" / "enhanced_content_selection.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "ENHANCED_NOT_USABLE")
        self.assertEqual(artifact["part_count"], 0)

    def test_expands_short_part_then_preserves_three_part_identity(self):
        calls = []
        def process(candidate, scope):
            calls.append((candidate["topic"], scope["end"] - scope["start"]))
            length = scope["end"] - scope["start"]
            final = 50 if candidate["topic"] == "one" and length < 300 else 200
            return {"final_duration": final, "final_keep": [scope],
                    "silence_keep": [scope], "semantic_removed": []}
        parts, _rejected = recover_three_parts(candidates(), 1200, process)
        self.assertEqual([item["part_index"] for item in parts], [1, 2, 3])
        self.assertIn(("one", 300.0), calls)
        self.assertTrue(all(180 <= item["final_duration"] <= 300 for item in parts))

    def test_alternate_candidate_replaces_unrecoverable_candidate(self):
        def process(candidate, scope):
            final = 40 if candidate["topic"] == "one" else 200
            return {"final_duration": final, "final_keep": [scope],
                    "silence_keep": [scope], "semantic_removed": []}
        parts, rejected = recover_three_parts(candidates(), 1200, process)
        self.assertNotIn("one", [item["candidate"]["topic"] for item in parts])
        self.assertEqual(len(parts), 3)
        self.assertTrue(any(item["topic"] == "one" for item in rejected))

    def test_failure_to_recover_fewer_than_two_fails_open(self):
        with self.assertRaises(EnhancedFlowSkipped):
            recover_three_parts(candidates()[:1], 1200, lambda _candidate, scope: {
                "final_duration": 200, "final_keep": [scope],
                "silence_keep": [scope], "semantic_removed": [],
            })

    def test_two_valid_candidates_produce_two_parts(self):
        parts, _rejected = recover_enhanced_parts(candidates()[:2], 1200, lambda _candidate, scope: {
            "final_duration": 200, "final_keep": [scope],
            "silence_keep": [scope], "semantic_removed": [],
        })
        self.assertEqual([item["part_index"] for item in parts], [1, 2])

    def test_one_valid_candidate_fails_open(self):
        with self.assertRaises(EnhancedFlowSkipped):
            recover_enhanced_parts(candidates()[:1], 1200, lambda _candidate, scope: {
                "final_duration": 200, "final_keep": [scope],
                "silence_keep": [scope], "semantic_removed": [],
            })

    @patch("enhanced_content_flow.flow.probe_video_geometry", return_value=(1920, 1080))
    def test_fixed_plan_maps_best_n_directly_to_part_n_without_resplitting(self, _probe):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            parts = []
            for index, start in enumerate((100, 400, 700), 1):
                keep = [{"start": start, "end": start + 80}]
                parts.append({"part_index": index, "final_keep": keep, "final_duration": 80})
            plan_path = _format_plan(source, root / "out", "Title", root, parts, 1000)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            jobs = build_render_jobs(plan, root / "out")
        self.assertEqual(plan["part_count"], 3)
        self.assertTrue(plan["enhanced_content_selection"])
        self.assertEqual([[item["start"], item["end"]] for item in (job["source_segments"][0] for job in jobs)], [
            [100.0, 180.0], [400.0, 480.0], [700.0, 780.0],
        ])

    @patch("enhanced_content_flow.flow.probe_video_geometry", return_value=(1920, 1080))
    def test_fixed_plan_records_two_selected_parts(self, _probe):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            parts = [
                {"part_index": 1, "final_keep": [{"start": 100, "end": 280}], "final_duration": 180},
                {"part_index": 2, "final_keep": [{"start": 500, "end": 680}], "final_duration": 180},
            ]
            plan_path = _format_plan(source, root / "out", "Title", root, parts, 1000)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            jobs = build_render_jobs(plan, root / "out")
        self.assertEqual(plan["part_count"], 2)
        self.assertEqual(len(plan["parts"]), 2)
        self.assertEqual(len(jobs), 2)

    @patch("enhanced_content_flow.flow.probe_media", return_value={"duration": 1200})
    @patch("enhanced_content_flow.flow._format_plan", return_value=Path("format_plan.json"))
    @patch("enhanced_content_flow.flow.apply_semantic_cleaner")
    def test_qwen_semantic_model_loads_once_not_per_part(self, semantic, _plan, _probe):
        class Runtime:
            calls = 0
            def process(self, _source, _output, **options):
                self.calls += 1
                scope = options["allowed_ranges"][0]
                report = {"keep_intervals": [scope], "no_speech_detected": False}
                Path(options["report_path"]).write_text(json.dumps(report), encoding="utf-8")
        class Detector:
            calls = 0
            ranges = None
            def detect_ranges(self, _source, _duration, ranges, _cache, **_options):
                self.calls += 1
                self.ranges = ranges
                return {"segments": []}
        detector, factory_calls = Detector(), []
        def factory():
            factory_calls.append(True)
            return detector
        def clean(_source, _report, output, **_options):
            Path(output).write_text("{}", encoding="utf-8")
            return {"status": "APPLIED", "removed_segments": []}
        semantic.side_effect = clean
        selection = {
            "status": "APPLIED", "generation_count": 1, "model_load_time": 1,
            "ranked_candidates": candidates()[:3],
            "selected_ranges": [
                {"part_index": index, "topic": item["topic"],
                 "start": max(0, item["center"] - 108),
                 "end": max(0, item["center"] - 108) + 216}
                for index, item in enumerate(candidates()[:3], 1)
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            outputs = [root / f"PART_{index}.mp4" for index in range(1, 4)]
            for output in outputs:
                output.write_bytes(b"part")
            from enhanced_content_flow.flow import run_enhanced_content_flow
            result = run_enhanced_content_flow(
                source, root, "Title", root / "job",
                selector=lambda *_args, **_kwargs: selection,
                runtime=Runtime(), semantic_detector_factory=factory,
                renderer=lambda _path: {"formatter_status": "DONE", "formatted_outputs": [
                    {"path": str(path)} for path in outputs
                ]},
            )
            artifact = json.loads((root / "job" / "enhanced_content_selection.json").read_text(encoding="utf-8"))
        self.assertEqual(result, outputs)
        self.assertEqual(artifact["part_count"], 3)
        self.assertEqual(artifact["status"], "ENHANCED_SUCCESS_3")
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(detector.calls, 1)
        self.assertEqual(len(detector.ranges), 3)
        self.assertEqual(semantic.call_count, 3)

    @patch("enhanced_content_flow.flow.probe_media", return_value={"duration": 1200})
    @patch("enhanced_content_flow.flow.rewrite_title_once", return_value={
        "rewritten_title": "Title", "filename_base": "Title", "status": "APPLIED",
        "total_seconds": 0, "queue_wait_seconds": 0, "generation_seconds": 0,
        "model_load_count": 0, "generation_count": 0,
    })
    @patch("enhanced_content_flow.flow._format_plan", return_value=Path("format_plan.json"))
    @patch("enhanced_content_flow.flow.apply_semantic_cleaner")
    def test_two_part_success_runs_semantic_cleaner_for_both_ranges(
        self, semantic, _plan, _rewrite, _probe,
    ):
        class Runtime:
            def process(self, _source, _output, **options):
                scope = options["allowed_ranges"][0]
                Path(options["report_path"]).write_text(json.dumps({
                    "input_duration": 1200, "keep_intervals": [scope],
                    "original_keep_intervals": [scope], "no_speech_detected": False,
                }), encoding="utf-8")

        class Detector:
            def detect_ranges(self, _source, _duration, ranges, _cache, **_options):
                return {"segments": [], "generation_count": 1}

        def clean(_source, _report, output, **_options):
            output = Path(output)
            output.write_text("{}", encoding="utf-8")
            return {"status": "APPLIED", "removed_segments": []}

        semantic.side_effect = clean
        selected = [
            {"part_index": 1, "topic": "one", "center": 200, "start": 92, "end": 308},
            {"part_index": 2, "topic": "two", "center": 700, "start": 592, "end": 808},
        ]
        selection = {
            "status": "APPLIED", "ranked_candidates": selected,
            "selected_ranges": selected, "generation_count": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            outputs = [root / "PART_1.mp4", root / "PART_2.mp4"]
            for output in outputs:
                output.write_bytes(b"part")
            from enhanced_content_flow.flow import run_enhanced_content_flow
            result = run_enhanced_content_flow(
                source, root, "Title", root / "job", selector=lambda *_a, **_k: selection,
                runtime=Runtime(), semantic_detector_factory=Detector,
                renderer=lambda _path: {"formatter_status": "DONE", "formatted_outputs": [
                    {"path": str(path)} for path in outputs
                ]},
            )
            artifact = json.loads((root / "job" / "enhanced_content_selection.json").read_text(encoding="utf-8"))
        self.assertEqual(result, outputs)
        self.assertEqual(artifact["status"], "ENHANCED_SUCCESS_2")
        self.assertEqual(artifact["part_count"], 2)
        self.assertEqual(semantic.call_count, 2)


if __name__ == "__main__":
    unittest.main()
