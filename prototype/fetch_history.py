"""Download hourly long-term statistics (array power, battery SOC) from Home Assistant into data/."""
from common import DATA, HomeAssistant, arrays, load_config

cfg = load_config()
ha = HomeAssistant(cfg)
DATA.mkdir(exist_ok=True)

power_ids = [s for a in arrays(cfg) for s in a.sensors]
power = ha.statistics(power_ids, cfg["history_start"], "mean")
power.to_pickle(DATA / "power.pkl")
print("power:", {k: f"{v.first_valid_index()} .. {v.last_valid_index()}" for k, v in power.items()})

soc_ids = cfg.get("curtailment", {}).get("soc_sensors", [])
if soc_ids:
    soc = ha.statistics(soc_ids, cfg["history_start"], "max")
    soc.to_pickle(DATA / "soc.pkl")
    print("soc:", len(soc), "hours")
