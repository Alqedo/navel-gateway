"""Regenerate the original integration icon using Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw

base = Path(__file__).resolve().parent
base.mkdir(exist_ok=True)
# Code-native mark: stove enclosure, flame and wireless arcs.
im = Image.new("RGBA", (1024, 1024))
d = ImageDraw.Draw(im)
d.rounded_rectangle((80, 80, 944, 944), radius=210, fill="#123B4A")
d.rounded_rectangle((280, 410, 744, 820), radius=60, outline="#E8F5F5", width=32)
d.line((330, 865, 330, 820), fill="#E8F5F5", width=30)
d.line((694, 865, 694, 820), fill="#E8F5F5", width=30)
d.polygon(
    [
        (512, 460),
        (610, 594),
        (622, 675),
        (580, 735),
        (507, 754),
        (438, 724),
        (400, 666),
        (415, 591),
        (460, 640),
        (470, 550),
    ],
    fill="#FFB65C",
)
for box in [(335, 185, 689, 485), (400, 255, 624, 445)]:
    d.arc(box, 210, 330, fill="#80DFD4", width=29)
d.ellipse((494, 349, 530, 385), fill="#80DFD4")
for name, size in [("icon.png", 256), ("icon@2x.png", 512)]:
    im.resize((size, size), Image.Resampling.LANCZOS).save(base / name)
(base / "README.md").write_text(
    "Original Navel Gateway mark, drawn from geometric primitives. Source: `generate_icon.py`.\n"
)
