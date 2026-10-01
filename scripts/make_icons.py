"""Generate the app icon set (PNG + ICO) with Pillow.

The mark is the same coral square with a white X that the UI header
uses, so the taskbar, window, and installer all match the app.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(r"C:\Users\George\Desktop\Automated-X-Manager\src-tauri\icons")
OUT.mkdir(parents=True, exist_ok=True)

CORAL = (255, 107, 90, 255)
DARK = (20, 24, 29, 255)


def draw_mark(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square badge with a margin, matching the CSS brand-mark.
    m = max(1, size // 12)
    radius = max(1, size // 5)
    d.rounded_rectangle([m, m, size - m, size - m], radius=radius, fill=CORAL)

    # White X, drawn as two strokes so it scales cleanly at 16px.
    pad = int(size * 0.30)
    width = max(1, int(size * 0.11))
    d.line([pad, pad, size - pad, size - pad], fill=(255, 255, 255, 255), width=width)
    d.line([size - pad, pad, pad, size - pad], fill=(255, 255, 255, 255), width=width)
    return img


def main() -> int:
    sizes = [32, 128, 256, 512]
    for s in sizes:
        mark = draw_mark(s)
        mark.save(OUT / f"{s}x{s}.png")
        if s == 512:
            mark.save(OUT / "icon.png")
            mark.save(OUT / "StoreLogo.png", sizes=[(50, 50)])

    # ICO needs at least one full-size frame; embed the standard ladder.
    ico_base = draw_mark(256)
    ico_base.save(
        OUT / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    # A square glyph for the tray/spaced look.
    print("wrote:", ", ".join(sorted(p.name for p in OUT.iterdir())))
    return 0


if __name__ == "__main__":
    sys.exit(main())