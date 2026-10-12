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
CONF_TEMP_COEFF = "temp_coeff"           # datasheet Pmax temperature coefficient in %/K (optional)

# options (advanced): history archives used for learning
CONF_ARCHIVE_TEMPERATURE = "archive_temperature"
CONF_ARCHIVE_URL = "archive_url"
CONF_SATELLITE = "satellite"
CONF_SATELLITE_URL = "satellite_url"
CONF_SATELLITE_MODEL = "satellite_model"
CONF_API_KEY = "api_key"
CONF_COMPARE_OPEN_METEO = "compare_open_meteo"   # archive Open-Meteo next to a weather entity for comparison
DEFAULT_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_SATELLITE_URL = "https://satellite-api.open-meteo.com/v1/archive"
DEFAULT_SATELLITE_MODEL = "satellite_radiation_seamless"

# irradiance attributes recognised in weather entity hourly forecasts (W/m², mean of the preceding hour)
IRRADIANCE_KEYS = ("sun_irradiance", "solar_irradiance", "irradiance", "global_horizontal_irradiance")

FORECAST_DAYS = 3
UPDATE_MINUTES = (0, 30)                 # forecast refresh at these minutes of every hour, plus once at start
STALE_AFTER = timedelta(hours=6)         # sensors stay available this long after the last successful update
POWER_UNIT_FACTORS = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}
LEARN_TIME = (1, 30)                     # local time of the nightly learning run
HISTORY_YEARS = 2                        # history window used for learning

STORAGE_VERSION = 1
ARCHIVE_DAYS = 120                       # day-ahead forecasts kept for evaluation
ERROR_DAYS = 30
MORNING_HOUR = 6                         # local hour at which today's forecast is fixed
COMPARE_SOURCE = "open_meteo"                          # window of the forecast error sensor
ATTR_SCHEMA_VERSION = 1

SERVICE_EXCLUDE_PERIOD = "exclude_period"
SERVICE_RELEARN = "relearn"
