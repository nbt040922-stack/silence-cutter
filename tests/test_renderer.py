import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from silence_cutter.renderer import _filter_graph, render_video


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg and ffprobe are required")
class RendererIntegrationTests(unittest.TestCase):
    def test_renders_synchronized_audio_and_video(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
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
                    "testsrc2=size=160x90:rate=30:duration=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=10",
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

            render_video(
                source,
                output,
                [{"start": 0.0, "end": 3.0}, {"start": 6.0, "end": 10.0}],
            )
            completed = subprocess.run(
                [
                    FFPROBE,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,duration",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            probe = json.loads(completed.stdout)

            self.assertTrue(output.is_file())
            streams = {stream["codec_type"]: stream for stream in probe["streams"]}
            self.assertIn("video", streams)
            self.assertIn("audio", streams)
            actual_duration = float(probe["format"]["duration"])
            video_duration = float(streams["video"]["duration"])
            audio_duration = float(streams["audio"]["duration"])
            self.assertAlmostEqual(actual_duration, 7.0, delta=0.25)
            self.assertLessEqual(abs(video_duration - audio_duration), 0.25)

    def test_empty_keep_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "keep timeline must not be empty"):
            _filter_graph([])


if __name__ == "__main__":
    unittest.main()
