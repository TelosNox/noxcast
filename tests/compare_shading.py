"""Compare the shading of two versions of model.py on the synthetic site (not a test).

    python tests/compare_shading.py <repository> [case,case,...]

Learns each case of synthetic_site.py with the model.py of the given checkout and prints the learned
shade and the forecast error (mean absolute hourly error relative to the mean true power) over all
daylight hours, clear hours and the clear noon hours of February and October, plus the share of
free sun positions learned as shade (transmission below 0.9).
"""
import importlib
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import synthetic_site as ss

repo = pathlib.Path(sys.argv[1]).resolve()
name = f"noxcast_{abs(hash(str(repo)))}"
package = types.ModuleType(name)
package.__path__ = [str(repo / "custom_components" / "noxcast")]
sys.modules[name] = package
solar = importlib.import_module(f"{name}.solar")
model = importlib.import_module(f"{name}.model")

CASES = {}
for split in ("solis", "erbs"):
    CASES[f"{split}:free_60_135"] = ("free", (60, 135), split)
    for mask in ("free", "wall", "overhang", "tree"):
        CASES[f"{split}:{mask}"] = (mask, (35, 180), split)

for case in sys.argv[2].split(",") if len(sys.argv) > 2 else CASES:
    mask_name, (tilt, azimuth), split = CASES[case]
    geo = model.ArrayGeometry(tilt=tilt, azimuth=azimuth, peak_w=1000)
    mask = ss.MASKS[mask_name]
    data = ss.generate(solar, model, geo, mask, split=split)
    learned = model.learn(data["site"], geo, data["samples"], data["now"], satellite_ghi=data["sat"],
                          steady=data["steady"])
    daylight = [t for t, v in data["truth"].items() if v > 0]
    false_shade, shade_error = ss.shade_errors(solar, model, geo, learned, data, mask)
    result = {"mae_all": round(100 * ss.forecast_error(model, geo, learned, data, daylight), 2),
              "mae_clear": round(100 * ss.forecast_error(model, geo, learned, data, data["clear"]), 2),
              "mae_noon": round(100 * ss.forecast_error(model, geo, learned, data, data["noon"]), 2),
              "false_shade": round(100 * false_shade, 1), "shade_error": round(shade_error, 3),
              "shaded_cells": sum(v < 0.9 for v in learned.cells.values()), "coverage": learned.coverage}
    if getattr(learned, "contour", None):
        result["edges"] = {k: v for k, v in learned.contour.items() if "low" in v or "high" in v}
    print(case, json.dumps(result), flush=True)
