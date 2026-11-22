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

# classify: template mengikuti durasi
ok(pipeline.classify("p", "m", 13)[1] == pipeline.PROMPT_MODEL, "13 dtk -> model")
ok(pipeline.classify("p", None, 13)[1] == pipeline.PROMPT_ONLY, "13 dtk -> only")
ok(pipeline.classify("p", "m", 10)[1] == pipeline.PROMPT_MODEL_10, "10 dtk -> model-10")
ok(pipeline.classify("p", None, 10)[1] == pipeline.PROMPT_ONLY_10, "10 dtk -> only-10")
for p in (pipeline.PROMPT_ONLY_10, pipeline.PROMPT_MODEL_10):
    ok(os.path.exists(p), f"template ada: {os.path.basename(p)}")

print(f"{lulus}/{lulus} lulus")
