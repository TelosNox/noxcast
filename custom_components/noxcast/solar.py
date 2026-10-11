"""Solar geometry and irradiance physics in pure Python (no third-party dependencies).

Validated against pvlib in tests/test_solar.py. All angles in degrees, times as UNIX seconds (UTC).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_RAD = math.pi / 180.0


@dataclass(frozen=True)
class Site:
    latitude: float
    longitude: float
    altitude: float = 0.0

    @property
    def pressure(self) -> float:
        """Standard-atmosphere pressure in Pa for the site altitude."""
        return 100.0 * ((44331.514 - self.altitude) / 11880.516) ** (1 / 0.1902632)


@dataclass(frozen=True)
class SunPosition:
    azimuth: float          # degrees clockwise from north
    elevation: float        # apparent elevation (with refraction)
    zenith: float           # true zenith (without refraction)

    @property
    def apparent_zenith(self) -> float:
        return 90.0 - self.elevation


def sun_position(site: Site, ts: float) -> SunPosition:
    """NOAA solar position algorithm (accuracy well below 0.1° for 1900–2100)."""
    jd = ts / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360.0
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    c = (math.sin(m * _RAD) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(2 * m * _RAD) * (0.019993 - 0.000101 * jc)
         + math.sin(3 * m * _RAD) * 0.000289)
    omega = 125.04 - 1934.136 * jc
    app_long = l0 + c - 0.00569 - 0.00478 * math.sin(omega * _RAD)
    mean_obliq = 23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    obliq = mean_obliq + 0.00256 * math.cos(omega * _RAD)
    decl = math.asin(math.sin(obliq * _RAD) * math.sin(app_long * _RAD))
    y = math.tan(obliq * _RAD / 2) ** 2
    eq_time = 4.0 / _RAD * (y * math.sin(2 * l0 * _RAD) - 2 * e * math.sin(m * _RAD)
                            + 4 * e * y * math.sin(m * _RAD) * math.cos(2 * l0 * _RAD)
                            - 0.5 * y * y * math.sin(4 * l0 * _RAD) - 1.25 * e * e * math.sin(2 * m * _RAD))
    minutes_utc = (ts % 86400.0) / 60.0
    true_solar_time = (minutes_utc + eq_time + 4.0 * site.longitude) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0

    lat = site.latitude * _RAD
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle * _RAD)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    denom = math.cos(lat) * math.sin(zen)
    if abs(denom) < 1e-9:
        azimuth = 180.0
    else:
        cos_az = max(-1.0, min(1.0, (math.sin(lat) * math.cos(zen) - math.sin(decl)) / denom))
        az = math.acos(cos_az) / _RAD
        azimuth = (az + 180.0) % 360.0 if hour_angle > 0 else (540.0 - az) % 360.0

    elev = 90.0 - zen / _RAD
    return SunPosition(azimuth, elev + _refraction(elev), zen / _RAD)


def _refraction(elev: float) -> float:
    """Atmospheric refraction correction in degrees (NOAA approximation)."""
    if elev > 85.0:
        return 0.0
    t = math.tan(elev * _RAD)
    if elev > 5.0:
        arcsec = 58.1 / t - 0.07 / t ** 3 + 0.000086 / t ** 5
    elif elev > -0.575:
        arcsec = 1735.0 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711)))
    else:
        arcsec = -20.772 / t
    return arcsec / 3600.0


def extra_radiation(ts: float) -> float:
    """Extraterrestrial normal irradiance in W/m² (Spencer 1971)."""
    b = 2 * math.pi * (_day_of_year(ts) - 1) / 365.0
    r =1.00011 + 0.034221 * math.cos(b) + 0.00128 * math.sin(b) + 0.000719 * math.cos(2 * b) + 0.000077 * math.sin(2 * b)
    return 1366.1 * r


def _day_of_year(ts: float) -> int:
    days = int(ts // 86400)
    # civil-from-days (Howard Hinnant), returns year to compute day of year
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy_mar = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy_mar + 2) // 153
    month = mp + 3 if mp < 10 else mp - 9
    year = y + (1 if month <= 2 else 0)
    jan1 = _days_from_civil(year, 1, 1)
    return days - jan1 + 1


def _days_from_civil(y: int, m: int, d: int) -> int:
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def clearsky_simplified_solis(apparent_elevation: float, pressure: float, dni_extra: float,
                              aod700: float = 0.1, precipitable_water: float = 1.0) -> tuple[float, float, float]:
    """Clear-sky (GHI, DNI, DHI) in W/m² after Ineichen's simplified Solis model."""
    if apparent_elevation <= 0:
        return 0.0, 0.0, 0.0
    w = max(precipitable_water, 0.2)
    lw = math.log(w)
    lp = math.log(pressure / 101325.0)
    a = aod700

    i0p = dni_extra * (0.12 * w ** 0.56 * a * a + 0.97 * w ** 0.032 * a + 1.08 * w ** 0.0051 + 0.071 * lp)
    taub = (1.82 + 0.056 * lw + 0.0071 * lw * lw) * a + (0.33 + 0.045 * lw + 0.0096 * lw * lw) + (0.0089 * w + 0.13) * lp
    b = (0.00925 * a * a + 0.0148 * a - 0.0172) * lw + (-0.7565 * a * a + 0.5057 * a + 0.4557)
    taug = (1.24 + 0.047 * lw + 0.0061 * lw * lw) * a + (0.27 + 0.043 * lw + 0.0090 * lw * lw) + (0.0079 * w + 0.1) * lp
    g = -0.0147 * lw - 0.3079 * a * a + 0.2846 * a + 0.3798
    if a < 0.05:
        td = (86 * w - 13800, -3.11 * w + 79.4, -0.23 * w + 74.8, 0.092 * w - 8.86, 0.0042 * w + 3.12)
        tdp = -0.83 * (1 + a) ** -17.2
    else:
        td = (-0.21 * w + 11.6, 0.27 * w - 20.7, -0.134 * w + 15.5, 0.0554 * w - 5.71, 0.0057 * w + 2.94)
        tdp = -0.71 * (1 + a) ** -15.0
    taud = td[0] * a ** 4 + td[1] * a ** 3 + td[2] * a ** 2 + td[3] * a + td[4] + tdp * lp
    d = -0.337 * a * a + 0.63 * a + 0.116 + lp / (18 + 152 * a)

    s = math.sin(apparent_elevation * _RAD)
    dni = i0p * math.exp(-taub / s ** b)
    ghi = i0p * math.exp(-taug / s ** g) * s
    dhi = i0p * math.exp(-taud / s ** d)
    return ghi, dni, dhi


def erbs(ghi: float, zenith: float, dni_extra: float) -> tuple[float, float]:
    """Split global horizontal irradiance into (DNI, DHI) with the Erbs model."""
    cos_z = math.cos(zenith * _RAD)
    if ghi <= 0:
        return 0.0, 0.0
    kt = min(max(ghi / (dni_extra * max(cos_z, 0.065)), 0.0), 2.0)
    if kt <= 0.22:
        df = 1 - 0.09 * kt
    elif kt <= 0.8:
        df = 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4
    else:
        df = 0.165
    dhi = df * ghi
    dni = (ghi - dhi) / cos_z if zenith < 87.0 and cos_z > 0 else 0.0
    return max(dni, 0.0), dhi


def angle_of_incidence(tilt: float, azimuth: float, sun_zenith: float, sun_azimuth: float) -> float:
    cos_aoi = (math.cos(tilt * _RAD) * math.cos(sun_zenith * _RAD)
               + math.sin(tilt * _RAD) * math.sin(sun_zenith * _RAD) * math.cos((sun_azimuth - azimuth) * _RAD))
    return math.acos(max(-1.0, min(1.0, cos_aoi))) / _RAD


def iam_martin_ruiz(aoi: float, a_r: float = 0.16) -> float:
    """Incidence angle modifier for the direct component (Martin & Ruiz 2001)."""
    if aoi >= 90.0:
        return 0.0
    return (1 - math.exp(-math.cos(aoi * _RAD) / a_r)) / (1 - math.exp(-1 / a_r))


def plane_of_array(tilt: float, azimuth: float, sun: SunPosition, dni: float, dhi: float, ghi: float,
                   albedo: float = 0.2) -> tuple[float, float]:
    """Effective (beam, diffuse) irradiance on the module plane in W/m².

    Beam includes incidence angle losses; diffuse is isotropic sky plus ground reflection.
    """
    if sun.elevation <= 0:
        return 0.0, 0.0
    aoi = angle_of_incidence(tilt, azimuth, sun.apparent_zenith, sun.azimuth)
    beam = max(dni * math.cos(aoi * _RAD), 0.0) * iam_martin_ruiz(aoi)
    cos_t = math.cos(tilt * _RAD)
    diffuse = dhi * (1 + cos_t) / 2 + ghi * albedo * (1 - cos_t) / 2
    return beam, max(diffuse, 0.0)
