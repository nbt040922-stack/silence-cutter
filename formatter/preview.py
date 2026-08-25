from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from silence_cutter.runtime_paths import find_executable

from .planner import ROOT, _is_emoji, _source_at


def _font(relative: str, size: int, *, bold_variable: bool = False):
    font = ImageFont.truetype(str(ROOT / relative), size=size)
    if bold_variable:
        try:
            font.set_variation_by_name("Bold")
        except (OSError, ValueError):
            pass
    return font


def _draw_centered_mixed(draw, line: str, primary, emoji, y: float, center_x: float) -> None:
    pieces: list[tuple[str, object]] = []
    for character in line:
        font = emoji if _is_emoji(character) else primary
        if pieces and pieces[-1][1] is font:
            pieces[-1] = (pieces[-1][0] + character, font)
        else:
            pieces.append((character, font))
    widths = [draw.textlength(text, font=font) for text, font in pieces]
    cursor_x = center_x - sum(widths) / 2
    for (text, font), width in zip(pieces, widths):
        draw.text((cursor_x, y), text, font=font, fill="black")
        cursor_x += width


def _extract_frame(video: Path, output: Path, timestamp: float) -> None:
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found")
    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{timestamp:.6f}", "-i", str(video), "-frames:v", "1", str(output),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(f"preview frame extraction failed: {completed.stderr.strip()}")


def render_overlay(plan: dict, output_path: Path, label: str | None = None) -> Path:
    layout = plan["layout"]
    canvas_info = layout["canvas"]
    canvas = Image.new(
        "RGBA", (canvas_info["width"], canvas_info["height"]), (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(canvas)
    title_banner = layout["title_banner_geometry"]
    draw.rounded_rectangle(
        (
            title_banner["x"], title_banner["y"],
            title_banner["x"] + title_banner["width"],
            title_banner["y"] + title_banner["height"],
        ),
        radius=title_banner["radius"], fill="white",
    )
    title = plan["title"]
    title_font = _font(
        title["font_file"], title["rendered_size_px"],
        bold_variable=title["selected_font"].startswith("Noto"),
    )
    emoji_font = _font(title["emoji_font_file"], title["rendered_size_px"], bold_variable=True)
    lines = title["wrapped_lines"]
    line_height = title["line_height"]
    cursor_y = title_banner["y"] + (title_banner["height"] - len(lines) * line_height) / 2
    for line in lines:
        _draw_centered_mixed(
            draw, line, title_font, emoji_font, cursor_y,
            title_banner["x"] + title_banner["width"] / 2,
        )
        cursor_y += line_height

    part_banner = layout.get("part_banner_geometry")
    if part_banner and label:
        draw.rounded_rectangle(
            (
                part_banner["x"], part_banner["y"],
                part_banner["x"] + part_banner["width"],
                part_banner["y"] + part_banner["height"],
            ),
            radius=part_banner["radius"], fill="white",
        )
        part_info = layout["part_label_font"]
        part_font = _font(
            part_info["font_file"], part_info["rendered_size_px"],
            bold_variable=part_info["bold_variable"],
        )
        box = draw.textbbox((0, 0), label, font=part_font)
        draw.text(
            (
                (canvas.width - (box[2] - box[0])) / 2,
                part_banner["y"] + (part_banner["height"] - (box[3] - box[1])) / 2 - box[1],
            ),
            label, font=part_font, fill="black",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def render_preview(plan: dict, output_path: Path) -> Path:
    layout = plan["layout"]
    canvas_info = layout["canvas"]
    canvas = Image.new(
        "RGB", (canvas_info["width"], canvas_info["height"]), canvas_info["background"]
    )
    with tempfile.TemporaryDirectory(prefix="formatter-preview-") as directory:
        directory_path = Path(directory)
        frame_path = directory_path / "frame.png"
        overlay_path = directory_path / "overlay.png"
        preview_time = min(1.0, plan["parts"][0]["duration"] / 2)
        direct = bool(plan.get("direct_source_render"))
        source_time = _source_at(preview_time, plan.get("render_segments") or []) if direct else preview_time
        _extract_frame(
            Path(plan["source_video_path"] if direct else plan["clean_video_path"]),
            frame_path, source_time if source_time is not None else preview_time,
        )
        frame = Image.open(frame_path).convert("RGB")
        crop = layout["crop_geometry"]
        frame = frame.crop((
            crop["x"], crop["y"], crop["x"] + crop["width"], crop["y"] + crop["height"]
        ))
        video = layout["video_placement"]
        frame = frame.resize((video["width"], video["height"]), Image.Resampling.LANCZOS)
        canvas.paste(frame, (video["x"], video["y"]))
        render_overlay(plan, overlay_path, plan["parts"][0]["label"])
        canvas = Image.alpha_composite(canvas.convert("RGBA"), Image.open(overlay_path)).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path
