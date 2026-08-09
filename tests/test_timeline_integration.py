import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caption_engine.models import CaptionSegment, WordTimestamp
from caption_engine.report import write_caption_report
from caption_engine.srt import write_srt
from silence_cutter.config import SilenceCutterConfig
from timeline_engine.pipeline import run_integrated_pipeline


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg and ffprobe are required")
class TimelineIntegrationTests(unittest.TestCase):
    def test_full_pipeline_renders_video_and_synced_caption_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mp4"
            output = root / "input.cut.mp4"
            subprocess.run(
                [
                    FFMPEG,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=30:duration=4",
                    "-f",
                    "lavfi",
                    "-i",
                    "aevalsrc=0.2*sin(2*PI*440*t)*not(between(t\\,1\\,3)):s=48000:d=4",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "5",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )

            def fake_captions(_source, output_srt, *, config, report_path):
                del config
                words = [
                    WordTimestamp("Hello", 0.2, 0.5),
                    WordTimestamp("removed", 1.5, 1.8),
                    WordTimestamp("again", 3.2, 3.5),
                ]
                captions = [CaptionSegment(0.2, 3.5, "Hello removed again", words)]
                write_srt(Path(output_srt), captions)
                write_caption_report(
                    Path(report_path),
                    {"captions": [caption.to_dict() for caption in captions]},
                )
                return {
                    "report_path": str(report_path),
                    "total_processing_time": 0.01,
                }

            with (
                patch(
                    "timeline_engine.pipeline.generate_captions",
                    side_effect=fake_captions,
                ),
                patch(
                    "silence_cutter.pipeline.detect_speech",
                    return_value=[
                        {"start": 0.0, "end": 1.0},
                        {"start": 3.0, "end": 4.0},
                    ],
                ),
            ):
                result = run_integrated_pipeline(
                    source,
                    output,
                    silence_config=SilenceCutterConfig(
                        speech_pad_before=0,
                        speech_pad_after=0,
                        merge_gap=0,
                    ),
                )

            for key in ("output_video", "output_srt", "captions_report", "timeline_report"):
                self.assertTrue(Path(result[key]).is_file())
            self.assertAlmostEqual(result["expected_output_duration"], 2.0, delta=0.05)
            self.assertAlmostEqual(result["actual_output_duration"], 2.0, delta=0.25)
            self.assertLessEqual(result["duration_error"], 0.25)
            self.assertEqual((result["words_before"], result["words_after"]), (3, 2))
            self.assertEqual((result["captions_before"], result["captions_after"]), (1, 2))

            captions = json.loads(
                Path(result["captions_report"]).read_text(encoding="utf-8")
            )["captions"]
            self.assertEqual([item["text"] for item in captions], ["Hello", "again"])
            self.assertTrue(
                all(item["end"] <= result["actual_output_duration"] for item in captions)
            )


if __name__ == "__main__":
    unittest.main()
