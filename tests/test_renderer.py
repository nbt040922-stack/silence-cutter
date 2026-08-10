import json
import re
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
    def metric(self, output, source, graph, pattern):
        completed = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(output), "-i", str(source),
             "-filter_complex", graph, "-f", "null", "-"],
            check=True, capture_output=True, text=True,
        )
        return float(re.findall(pattern, completed.stderr)[-1])

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

    def test_non_keyframe_keep_intervals_map_exact_video_and_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "timestamped.mp4"
            output = Path(directory) / "output.mp4"
            subprocess.run(
                [
                    FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=6",
                    "-f", "lavfi", "-i",
                    "aevalsrc=sin(2*PI*(200*t+100*t*t)):s=48000:d=6",
                    "-c:v", "mpeg4", "-g", "180", "-q:v", "3",
                    "-c:a", "aac", "-shortest", str(source),
                ],
                check=True, capture_output=True,
            )
            keep = [{"start": 2.5, "end": 3.5}, {"start": 4.5, "end": 5.5}]
            diagnostics = {}
            render_video(source, output, keep, diagnostics=diagnostics)

            self.assertNotIn(" -ss ", f" {diagnostics['ffmpeg_command']} ")
            self.assertNotIn("-copyts", diagnostics["ffmpeg_argv"])
            self.assertIn("trim=start=2.500000000", diagnostics["ffmpeg_command"])
            self.assertEqual(
                diagnostics["segments"],
                [
                    {"output_start": 0.0, "output_end": 1.0,
                     "source_start": 2.5, "source_end": 3.5},
                    {"output_start": 1.0, "output_end": 2.0,
                     "source_start": 4.5, "source_end": 5.5},
                ],
            )

            exact_video = self.metric(
                output, source,
                "[0:v]trim=0:0.8,setpts=PTS-STARTPTS[a];"
                "[1:v]trim=2.5:3.3,setpts=PTS-STARTPTS[b];[a][b]ssim",
                r"All:([0-9.]+)",
            )
            rounded_video = self.metric(
                output, source,
                "[0:v]trim=0:0.8,setpts=PTS-STARTPTS[a];"
                "[1:v]trim=2:2.8,setpts=PTS-STARTPTS[b];[a][b]ssim",
                r"All:([0-9.]+)",
            )
            exact_audio = self.metric(
                output, source,
                "[0:a]atrim=0:0.8,asetpts=PTS-STARTPTS[a];"
                "[1:a]atrim=2.5:3.3,asetpts=PTS-STARTPTS[b];[a][b]asisdr",
                r"SI-SDR ch\d+: ([-0-9.]+)",
            )
            rounded_audio = self.metric(
                output, source,
                "[0:a]atrim=0:0.8,asetpts=PTS-STARTPTS[a];"
                "[1:a]atrim=2:2.8,asetpts=PTS-STARTPTS[b];[a][b]asisdr",
                r"SI-SDR ch\d+: ([-0-9.]+)",
            )
            second_video = self.metric(
                output, source,
                "[0:v]trim=1:1.8,setpts=PTS-STARTPTS[a];"
                "[1:v]trim=4.5:5.3,setpts=PTS-STARTPTS[b];[a][b]ssim",
                r"All:([0-9.]+)",
            )
            second_audio = self.metric(
                output, source,
                "[0:a]atrim=1:1.8,asetpts=PTS-STARTPTS[a];"
                "[1:a]atrim=4.5:5.3,asetpts=PTS-STARTPTS[b];[a][b]asisdr",
                r"SI-SDR ch\d+: ([-0-9.]+)",
            )
            self.assertGreater(exact_video, 0.95)
            self.assertGreater(exact_video, rounded_video + 0.05)
            self.assertGreater(exact_audio, 10)
            self.assertGreater(exact_audio, rounded_audio + 10)
            self.assertGreater(second_video, 0.95)
            self.assertGreater(second_audio, 10)

            probe = json.loads(subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type,duration", "-of", "json", str(output)],
                check=True, capture_output=True, text=True,
            ).stdout)
            streams = {item["codec_type"]: float(item["duration"])
                       for item in probe["streams"]}
            self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.08)
            self.assertLessEqual(abs(streams["video"] - streams["audio"]), 0.05)

    def test_common_positive_stream_offset_is_normalized_before_media_time_trim(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.mkv"
            offset = Path(directory) / "offset.mkv"
            output = Path(directory) / "output.mp4"
            subprocess.run(
                [
                    FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=8",
                    "-f", "lavfi", "-i",
                    "aevalsrc=sin(2*PI*(200*t+100*t*t)):s=48000:d=8",
                    "-c:v", "mpeg4", "-g", "240", "-q:v", "3",
                    "-c:a", "pcm_s16le", "-shortest", str(base),
                ],
                check=True, capture_output=True,
            )
            subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(base),
                 "-map", "0", "-c", "copy", "-output_ts_offset", "6", str(offset)],
                check=True, capture_output=True,
            )
            probe = json.loads(subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries",
                 "stream=codec_type,start_time", "-of", "json", str(offset)],
                check=True, capture_output=True, text=True,
            ).stdout)
            starts = {item["codec_type"]: float(item["start_time"])
                      for item in probe["streams"]}
            self.assertGreaterEqual(starts["video"], 5.9)
            self.assertGreaterEqual(starts["audio"], 5.9)

            render_video(offset, output, [{"start": 3.0, "end": 5.0}])
            video_match = self.metric(
                output, base,
                "[0:v]trim=0:0.8,setpts=PTS-STARTPTS[a];"
                "[1:v]trim=3:3.8,setpts=PTS-STARTPTS[b];[a][b]ssim",
                r"All:([0-9.]+)",
            )
            audio_match = self.metric(
                output, base,
                "[0:a]atrim=0:0.8,asetpts=PTS-STARTPTS[a];"
                "[1:a]atrim=3:3.8,asetpts=PTS-STARTPTS[b];[a][b]asisdr",
                r"SI-SDR ch\d+: ([-0-9.]+)",
            )
            rendered = json.loads(subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type,duration", "-of", "json", str(output)],
                check=True, capture_output=True, text=True,
            ).stdout)
            durations = {item["codec_type"]: float(item["duration"])
                         for item in rendered["streams"]}
            self.assertGreater(video_match, 0.95)
            self.assertGreater(audio_match, 10)
            self.assertAlmostEqual(float(rendered["format"]["duration"]), 2.0, delta=0.08)
            self.assertLessEqual(abs(durations["video"] - durations["audio"]), 0.05)


if __name__ == "__main__":
    unittest.main()
