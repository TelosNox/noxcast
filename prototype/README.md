# Prototype

Exploratory scripts that validated the Noxcast approach on a real installation. Not part of the
integration; they read Home Assistant data through the WebSocket API.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows; use .venv/bin/python elsewhere
cp config.example.json config.local.json                    # fill in location, arrays, sensors
```

Create a long-lived access token in Home Assistant (Profile → Security) and store it in
`ha_token.txt` (or point `home_assistant.token_file` elsewhere). Both files are git-ignored.

## Scripts

| Script | Purpose |
|---|---|
| `fetch_history.py` | Download hourly long-term statistics (array power, battery SOC) into `data/` |
| `shading_report.py` | Shading per month × hour and beam-transmission map over sun position → `out/` |
| `backtest.py --config … --statistics … [--satellite … --temperature … --archive …]` | The integration's PV backtest on exported data, metrics as JSON; run it from two checkouts on the same files to compare model versions (see `docs/concept.md`, PV backtest) |
| `forecast.py [ha\|openmeteo]` | Three-day forecast from learned geometry/shading and weather irradiance, compared with actuals and an optional other forecast |

## Method in short

- Clear-sky irradiance (pvlib, simplified Solis) on the plane of each array, incidence-angle
  losses (physical IAM), computed at 10-minute resolution and averaged per hour.
- System efficiency: 95th percentile of actual / expected at strong expected output.
- Shading: 90th percentile of actual / expected per month × hour (typical clear day), and beam
  transmission per sun-position cell where direct light is geometrically possible.
- Learning excludes curtailment: configured periods (e.g. holidays with zero export) and hours
  with a full battery.
- Weather: Home Assistant weather entity with `sun_irradiance` (GHI, split with Erbs) or
  Open-Meteo (GHI / DNI / DHI).
