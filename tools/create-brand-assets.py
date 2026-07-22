from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "python_qt6" / "soundboard_qt6" / "assets"
WEB_LOGO = ROOT / "src" / "python_qt6" / "soundboard_qt6" / "web" / "brand-logo.png"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def color_lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


def gradient_square(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    cyan = (53, 215, 255)
    navy = (23, 42, 66)
    deep = (11, 19, 34)
    orange = (255, 116, 70)
    for y in range(size):
        for x in range(size):
            t = (x + y) / (size * 2)
            if t < 0.34:
                color = color_lerp(cyan, navy, t / 0.34)
            elif t < 0.72:
                color = color_lerp(navy, deep, (t - 0.34) / 0.38)
            else:
                color = color_lerp(deep, orange, (t - 0.72) / 0.28)
            pixels[x, y] = (*color, 255)
    return image


def draw_mark(size: int) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    margin = int(canvas_size * 0.075)
    radius = int(canvas_size * 0.24)
    rect = (margin, margin, canvas_size - margin, canvas_size - margin)

    draw.rounded_rectangle(
        (rect[0], rect[1] + int(canvas_size * 0.035), rect[2], rect[3] + int(canvas_size * 0.035)),
        radius=radius,
        fill=(5, 10, 18, 105),
    )

    base = gradient_square(rect[2] - rect[0])
    base.putalpha(rounded_mask(base.width, radius))
    canvas.alpha_composite(base, (rect[0], rect[1]))

    border = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border)
    border_draw.rounded_rectangle(
        rect,
        radius=radius,
        outline=(221, 248, 255, 255),
        width=max(7, int(canvas_size * 0.018)),
    )
    canvas.alpha_composite(border)

    clip = Image.new("L", (canvas_size, canvas_size), 0)
    clip_draw = ImageDraw.Draw(clip)
    clip_draw.rounded_rectangle(rect, radius=radius, fill=255)
    glow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (
            rect[0] - int(canvas_size * 0.22),
            rect[1] - int(canvas_size * 0.17),
            rect[0] + int(canvas_size * 0.30),
            rect[1] + int(canvas_size * 0.25),
        ),
        fill=(61, 214, 255, 90),
    )
    glow_draw.ellipse(
        (
            rect[2] - int(canvas_size * 0.36),
            rect[3] - int(canvas_size * 0.32),
            rect[2] + int(canvas_size * 0.22),
            rect[3] + int(canvas_size * 0.14),
        ),
        fill=(255, 112, 67, 86),
    )
    glow.putalpha(Image.composite(glow.getchannel("A"), Image.new("L", glow.size, 0), clip))
    canvas.alpha_composite(glow)

    draw = ImageDraw.Draw(canvas)
    pad_size = int(canvas_size * 0.215)
    gap = int(canvas_size * 0.055)
    grid_left = int(canvas_size * 0.268)
    grid_top = int(canvas_size * 0.225)
    pad_radius = int(canvas_size * 0.055)
    pad_colors = [(255, 112, 67), (45, 183, 240), (103, 195, 109), (180, 91, 214)]
    for index, color in enumerate(pad_colors):
        x = grid_left + (index % 2) * (pad_size + gap)
        y = grid_top + (index // 2) * (pad_size + gap)
        shadow_offset = int(canvas_size * 0.012)
        draw.rounded_rectangle(
            (x, y + shadow_offset, x + pad_size, y + pad_size + shadow_offset),
            radius=pad_radius,
            fill=(0, 0, 0, 92),
        )
        draw.rounded_rectangle(
            (x, y, x + pad_size, y + pad_size),
            radius=pad_radius,
            fill=(*color, 255),
            outline=(234, 247, 255, 225),
            width=max(5, int(canvas_size * 0.014)),
        )

    wave_y = int(canvas_size * 0.765)
    points: list[tuple[float, float]] = []
    for i in range(80):
        t = i / 79
        x = int(canvas_size * 0.22) + t * int(canvas_size * 0.58)
        y = wave_y + math.sin(t * math.pi * 4) * int(canvas_size * 0.065) * (1 - abs(0.5 - t) * 0.50)
        points.append((x, y))
    draw.line(points, fill=(45, 215, 255, 170), width=max(14, int(canvas_size * 0.052)), joint="curve")
    draw.line(points, fill=(255, 255, 255, 255), width=max(9, int(canvas_size * 0.034)), joint="curve")
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def load_brand_mark(size: int) -> Image.Image:
    if WEB_LOGO.is_file():
        with Image.open(WEB_LOGO) as image:
            image = image.convert("RGBA")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            x = (size - image.width) // 2
            y = (size - image.height) // 2
            canvas.alpha_composite(image, (x, y))
            return canvas
    return draw_mark(size)


def draw_wordmark(icon: Image.Image) -> Image.Image:
    width, height = 1400, 420
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.alpha_composite(icon.resize((300, 300), Image.Resampling.LANCZOS), (60, 60))
    draw = ImageDraw.Draw(image)
    title_font = font(104, True)
    subtitle_font = font(34, False)
    draw.text((400, 104), "Soundboard", font=title_font, fill=(244, 247, 251, 255))
    draw.text((1015, 104), "Studio", font=title_font, fill=(61, 214, 255, 255))
    draw.text((406, 236), "Clean mic mixing and phone remote control", font=subtitle_font, fill=(159, 180, 208, 255))
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = load_brand_mark(512)
    icon.save(ASSETS / "app_icon.png")
    icon.save(ASSETS / "app_icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    draw_wordmark(icon).save(ASSETS / "app_logo.png")
    print(f"Created brand assets in {ASSETS}")


if __name__ == "__main__":
    main()
