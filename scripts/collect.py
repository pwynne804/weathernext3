#!/usr/bin/env python3
"""Collect today's 7-day forecasts (Google Weather API / WeatherNext, and NWS)
and yesterday's observed truth from an Ambient Weather station.

Writes:
  data/forecasts/YYYY-MM-DD.json   forecasts issued on that date
  data/obs/YYYY-MM-DD.json         what actually happened on that date

Definitions (all local time, America/New_York by default):
  "high" for day D  = max temperature between 07:00 D and 19:00 D
  "low"  for day D  = min temperature between 19:00 D and 07:00 D+1
  "rain" for day D  = rainfall between 07:00 D and 07:00 D+1 (inches)
These line up with how both forecast sources define a day's high, night's low,
and daytime precipitation chance.

Environment variables:
  GOOGLE_WEATHER_KEY   Google Maps Platform key with the Weather API enabled
  AMBIENT_API_KEY      from ambientweather.net -> Account -> API Keys
  AMBIENT_APP_KEY      from the same page ("Application Key")
  AMBIENT_MAC          optional; defaults to the first station on the account
  LAT, LON             optional; default 37.513, -77.566 (Richmond, VA 23235)
  TZ_NAME              optional; default America/New_York
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAT = float(os.environ.get("LAT", "37.513"))
LON = float(os.environ.get("LON", "-77.566"))
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/New_York"))
UA = "nextcast-verify (https://github.com/pwynne804/weathernext3)"


def get(url, headers=None, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET failed for {url.split('?')[0]}: {last}")


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    print("wrote", os.path.relpath(path, ROOT))


# ---------------------------------------------------------------- forecasts
def google_days(key):
    q = urllib.parse.urlencode({
        "key": key, "location.latitude": LAT, "location.longitude": LON,
        "days": 7, "pageSize": 7, "unitsSystem": "IMPERIAL",
    })
    j = get("https://weather.googleapis.com/v1/forecast/days:lookup?" + q)
    out = {}
    for d in j.get("forecastDays", []):
        dd = d["displayDate"]
        date = f"{dd['year']:04d}-{dd['month']:02d}-{dd['day']:02d}"
        day = d.get("daytimeForecast", {})
        night = d.get("nighttimeForecast", {})
        out[date] = {
            "hi": d.get("maxTemperature", {}).get("degrees"),
            "lo": d.get("minTemperature", {}).get("degrees"),
            "popDay": day.get("precipitation", {}).get("probability", {}).get("percent"),
            "popNight": night.get("precipitation", {}).get("probability", {}).get("percent"),
            "qpfIn": (day.get("precipitation", {}).get("qpf", {}).get("quantity") or 0)
                     + (night.get("precipitation", {}).get("qpf", {}).get("quantity") or 0),
            "text": day.get("weatherCondition", {}).get("description", {}).get("text"),
        }
    return out


def nws_days():
    p = get(f"https://api.weather.gov/points/{LAT},{LON}")["properties"]
    f = get(p["forecast"])["properties"]
    out = {}
    for per in f.get("periods", []):
        start = dt.datetime.fromisoformat(per["startTime"]).astimezone(TZ)
        date = start.date().isoformat()
        row = out.setdefault(date, {"hi": None, "lo": None, "popDay": None, "popNight": None, "text": None})
        pop = (per.get("probabilityOfPrecipitation") or {}).get("value")
        if per["isDaytime"]:
            row["hi"] = per["temperature"]
            row["popDay"] = pop
            row["text"] = per.get("shortForecast")
        else:
            row["lo"] = per["temperature"]
            row["popNight"] = pop
    return {"grid": f"{p['gridId']}/{p['gridX']},{p['gridY']}", "updated": f.get("updateTime"), "days": out}


def collect_forecasts(today):
    rec = {
        "issued": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "issuedLocalDate": today.isoformat(),
        "lat": LAT, "lon": LON,
    }
    key = os.environ.get("GOOGLE_WEATHER_KEY")
    if key:
        try:
            rec["google"] = google_days(key)
        except Exception as e:  # noqa: BLE001
            print("google failed:", e, file=sys.stderr)
            rec["googleError"] = str(e)
    else:
        print("GOOGLE_WEATHER_KEY not set; skipping Google", file=sys.stderr)
    try:
        n = nws_days()
        rec["nws"] = n["days"]
        rec["nwsMeta"] = {"grid": n["grid"], "updated": n["updated"]}
    except Exception as e:  # noqa: BLE001
        print("nws failed:", e, file=sys.stderr)
        rec["nwsError"] = str(e)
    write(os.path.join(ROOT, "data", "forecasts", f"{today.isoformat()}.json"), rec)


# ------------------------------------------------------------ observations
def ambient_records(api_key, app_key, mac, pages=3):
    """Return station records (newest first), `pages` x 288 five-minute rows."""
    base = "https://api.ambientweather.net/v1/devices"
    auth = {"apiKey": api_key, "applicationKey": app_key}
    if not mac:
        devices = get(base + "?" + urllib.parse.urlencode(auth))
        if not devices:
            raise RuntimeError("no Ambient devices on this account")
        mac = devices[0]["macAddress"]
        print("using station", mac, devices[0].get("info", {}).get("name"))
        time.sleep(1.2)  # Ambient allows 1 request per second
    rows = {}
    end = int(time.time() * 1000)
    for _ in range(pages):
        q = urllib.parse.urlencode({**auth, "limit": 288, "endDate": end})
        page = get(f"{base}/{mac}?{q}")
        if not page:
            break
        for r in page:
            rows[r["dateutc"]] = r
        end = min(r["dateutc"] for r in page) - 1
        time.sleep(1.2)
    return mac, [rows[k] for k in sorted(rows)]


def summarize_day(rows, day):
    """Observed high/low/rain for local date `day` from 5-minute rows."""
    t0 = dt.datetime.combine(day, dt.time(7), tzinfo=TZ)
    t1 = t0 + dt.timedelta(hours=12)        # 19:00 D
    t2 = t0 + dt.timedelta(hours=24)        # 07:00 D+1
    ms = lambda t: int(t.timestamp() * 1000)  # noqa: E731
    win = [r for r in rows if ms(t0) <= r["dateutc"] < ms(t2)]
    if not win:
        return None
    temps_day = [r["tempf"] for r in win if r["dateutc"] < ms(t1) and r.get("tempf") is not None]
    temps_night = [r["tempf"] for r in win if r["dateutc"] >= ms(t1) and r.get("tempf") is not None]
    # Rain: sum positive increments of the station's daily accumulator so the
    # midnight reset does not count as negative rain.
    rain = 0.0
    prev = None
    for r in win:
        v = r.get("dailyrainin")
        if v is None:
            continue
        if prev is not None and v > prev:
            rain += v - prev
        prev = v
    expected = 24 * 12
    return {
        "date": day.isoformat(),
        "hi": max(temps_day) if temps_day else None,
        "lo": min(temps_night) if temps_night else None,
        "rainIn": round(rain, 3),
        "samples": len(win),
        "coverage": round(len(win) / expected, 2),
        "complete": len(temps_day) > 0 and len(temps_night) > 0 and len(win) >= expected * 0.8,
    }


def collect_obs(today):
    api_key, app_key = os.environ.get("AMBIENT_API_KEY"), os.environ.get("AMBIENT_APP_KEY")
    if not (api_key and app_key):
        print("AMBIENT_API_KEY / AMBIENT_APP_KEY not set; skipping observations", file=sys.stderr)
        return
    mac, rows = ambient_records(api_key, app_key, os.environ.get("AMBIENT_MAC"))
    print(f"{len(rows)} station rows")
    for back in (1, 2):  # yesterday, and the day before if it was missed
        day = today - dt.timedelta(days=back)
        path = os.path.join(ROOT, "data", "obs", f"{day.isoformat()}.json")
        if back == 2 and os.path.exists(path):
            continue
        s = summarize_day(rows, day)
        if s is None:
            print("no station data for", day, file=sys.stderr)
            continue
        s["station"] = mac
        write(path, s)


def list_stations():
    """Print the stations on the Ambient account, so AMBIENT_MAC can be chosen."""
    api_key, app_key = os.environ.get("AMBIENT_API_KEY"), os.environ.get("AMBIENT_APP_KEY")
    if not (api_key and app_key):
        sys.exit("set AMBIENT_API_KEY and AMBIENT_APP_KEY first")
    q = urllib.parse.urlencode({"apiKey": api_key, "applicationKey": app_key})
    for d in get("https://api.ambientweather.net/v1/devices?" + q):
        info = d.get("info", {})
        last = d.get("lastData", {})
        print(f"{d['macAddress']}  {info.get('name', '?')}  coords={info.get('coords', {}).get('coords')}  "
              f"last tempf={last.get('tempf')} dailyrainin={last.get('dailyrainin')}")


def main():
    if "--list" in sys.argv:
        list_stations()
        return
    today = dt.datetime.now(TZ).date()
    collect_forecasts(today)
    collect_obs(today)


if __name__ == "__main__":
    main()
