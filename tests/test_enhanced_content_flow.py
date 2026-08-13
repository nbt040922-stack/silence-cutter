import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enhanced_content_flow.flow import EnhancedFlowSkipped, _format_plan, recover_three_parts
from formatter.renderer import build_render_jobs


def candidates():
    return [
        {"center": 100, "score": .95, "topic": "one", "reason": "one"},
        {"center": 350, "score": .90, "topic": "two", "reason": "two"},
        {"center": 650, "score": .85, "topic": "three", "reason": "three"},
        {"center": 900, "score": .80, "topic": "four", "reason": "four"},
    ]


class EnhancedContentFlowTests(unittest.TestCase):
    def test_expands_short_part_then_preserves_three_part_identity(self):
        calls = []
        def process(candidate, scope):
            calls.append((candidate["topic"], scope["end"] - scope["start"]))
            length = scope["end"] - scope["start"]
            final = 50 if candidate["topic"] == "one" and length < 300 else 100
            return {"final_duration": final, "final_keep": [scope],
                    "silence_keep": [scope], "semantic_removed": []}
        parts, _rejected = recover_three_parts(candidates(), 1200, process)
        self.assertEqual([item["part_index"] for item in parts], [1, 2, 3])
        self.assertIn(("one", 300.0), calls)
        self.assertTrue(all(60 < item["final_duration"] <= 300 for item in parts))

    def test_alternate_candidate_replaces_unrecoverable_candidate(self):
        def process(candidate, scope):
            final = 40 if candidate["topic"] == "one" else 100
            return {"final_duration": final, "final_keep": [scope],
                    "silence_keep": [scope], "semantic_removed": []}
        parts, rejected = recover_three_parts(candidates(), 1200, process)
        self.assertNotIn("one", [item["candidate"]["topic"] for item in parts])
        self.assertEqual(len(parts), 3)
        self.assertTrue(any(item["topic"] == "one" for item in rejected))

    def test_failure_to_recover_exactly_three_fails_open(self):
        with self.assertRaises(EnhancedFlowSkipped):
            recover_three_parts(candidates()[:2], 1200, lambda _candidate, scope: {
                "final_duration": 100, "final_keep": [scope],
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
            def detect(self, _source, _duration):
                self.calls += 1
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
                {"part_index": index, "topic": item["topic"]}
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
        self.assertEqual(result, outputs)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(detector.calls, 1)
        self.assertEqual(semantic.call_count, 3)


if __name__ == "__main__":
    unittest.main()
