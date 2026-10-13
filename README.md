# Nextcast

A phone-sized weather page for Richmond, VA 23235 built on Google's Weather API
(WeatherNext 3), with a running scorecard that checks WeatherNext and the
National Weather Service against an Ambient Weather station.

Live: https://pwynne804.github.io/weathernext3/

## How the scorecard works

Every morning (12:15 UTC) a GitHub Actions workflow runs `scripts/collect.py`:

- logs today's 7-day forecast from Google and from NWS into `data/forecasts/`
- pulls yesterday's 5-minute station records from Ambient Weather and writes the
  observed high, low and rainfall into `data/obs/`

Then `scripts/score.py` compares every past forecast with what was observed and
writes `data/scorecard.json`, which the page reads.

Definitions: high = max 07:00-19:00, low = min 19:00-07:00 next day,
rain = at least 0.01 in between 07:00 and 07:00 next day. All local time.

## Secrets the workflow needs (repo Settings -> Secrets and variables -> Actions)

| Name | Where it comes from |
|---|---|
| `GOOGLE_WEATHER_KEY` | Google Cloud -> Credentials -> "Nextcast collector (GitHub Actions)" |
| `AMBIENT_API_KEY` | ambientweather.net -> Account -> API Keys -> Create API Key |
| `AMBIENT_APP_KEY` | same page -> Create Application Key |
| `AMBIENT_MAC` | optional; only needed if the account has more than one station |

To see the stations on the account:

    AMBIENT_API_KEY=... AMBIENT_APP_KEY=... python3 scripts/collect.py --list

## Running locally

    python3 scripts/collect.py   # needs the env vars above
    python3 scripts/score.py
    python3 -m http.server 8765  # then open http://localhost:8765/
