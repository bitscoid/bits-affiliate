"""Logika durasi adaptif: pick_seconds, parse_cost, classify."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bits_affiliate import flow_stage, pipeline  # noqa: E402

lulus = 0


def ok(kondisi, nama):
    global lulus
    assert kondisi, nama
    lulus += 1


# pick_seconds: slider raw max -> durasi terbaik
ok(flow_stage.pick_seconds(10) == 13, "raw 10 -> 13 dtk")
ok(flow_stage.pick_seconds(99) == 13, "raw besar -> 13 dtk")
ok(flow_stage.pick_seconds(7) == 10, "raw 7 -> 10 dtk")
ok(flow_stage.pick_seconds(8) == 10, "raw 8 -> 10 dtk")
ok(flow_stage.pick_seconds(5) == 0, "raw 5 -> tak didukung")
ok(flow_stage.pick_seconds(0) == 0, "raw 0 -> tak didukung")

# parse_cost: chip biaya ID/EN
ok(flow_stage.parse_cost("Generating will use 48 credits") == 48, "EN 48")
ok(flow_stage.parse_cost("Generating akan menggunakan 36 kredit") == 36, "ID 36")
ok(flow_stage.parse_cost("Video · 720p · 13s crop_9_16 x1") == 0, "tanpa angka biaya")
ok(flow_stage.parse_cost("") == 0, "kosong")

# classify: prompt mengikuti durasi dan ada/tidaknya foto model
from bits_affiliate import prompts  # noqa: E402

ok(
    pipeline.classify("p", "m", 13)[0] == "product+model",
    "13 dtk + model -> product+model",
)
ok(
    pipeline.classify("p", None, 13)[0] == "product-only",
    "13 dtk tanpa model -> product-only",
)
ok("exactly 13 seconds" in pipeline.classify("p", "m", 13)[1], "13 dtk -> spec 13")
ok("exactly 10 seconds" in pipeline.classify("p", None, 10)[1], "10 dtk -> spec 10")
ok(
    "Wardrobe continuity lock" in pipeline.classify("p", "m", 13)[1],
    "model -> aturan wardrobe",
)
ok(
    "Wardrobe continuity lock" not in pipeline.classify("p", None, 13)[1],
    "tanpa model -> no wardrobe",
)
# durasi tak dikenal jatuh ke default, tidak meledak
ok(
    "exactly 13 seconds" in pipeline.classify("p", None, 99)[1],
    "durasi aneh -> default aman",
)
for d in (13, 10):
    ok(d in prompts.SPEC, f"spec {d} dtk tersedia")

print(f"{lulus}/{lulus} lulus")
