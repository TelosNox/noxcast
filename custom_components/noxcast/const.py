"""Constants for Noxcast."""
from datetime import timedelta

DOMAIN = "noxcast"

# main entry
CONF_WEATHER_SOURCE = "weather_source"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_SOC_SENSORS = "soc_sensors"
CONF_SOC_FULL = "soc_full_pct"
WEATHER_SOURCE_ENTITY = "entity"
WEATHER_SOURCE_OPEN_METEO = "open_meteo"
DEFAULT_SOC_FULL = 95

# array subentry
SUBENTRY_ARRAY = "array"
CONF_NAME = "name"
CONF_POWER_SENSORS = "power_sensors"
CONF_TILT = "tilt"
CONF_AZIMUTH = "azimuth"
CONF_PEAK_POWER = "peak_power"
CONF_VALID_SINCE = "valid_since"

# irradiance attributes recognised in weather entity hourly forecasts (W/m², mean of the preceding hour)
IRRADIANCE_KEYS = ("sun_irradiance", "solar_irradiance", "irradiance", "global_horizontal_irradiance")

FORECAST_DAYS = 3
UPDATE_INTERVAL = timedelta(minutes=30)
STALE_AFTER = timedelta(hours=6)         # sensors stay available this long after the last successful update
POWER_UNIT_FACTORS = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}
LEARN_TIME = (1, 30)                     # local time of the nightly learning run
HISTORY_YEARS = 2                        # history window used for learning

STORAGE_VERSION = 1
ATTR_SCHEMA_VERSION = 1

SERVICE_EXCLUDE_PERIOD = "exclude_period"
SERVICE_RELEARN = "relearn"
