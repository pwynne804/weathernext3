#!/usr/bin/env python3
"""Score every forecast in data/forecasts against data/obs and write
data/scorecard.json, which the app reads.

For each forecast issued on day I and each lead L (0..6), the target day is
T = I + L days. If an observation for T exists, record the errors.

Scores per source and lead:
  n        number of scored days
  maeHigh  mean absolute error of the high, deg F
  maeLow   mean absolute error of the low, deg F
  biasHigh mean (forecast - observed) for highs; positive = ran warm
  biasLow  same for lows
  brier    Brier score of daytime rain chance vs. whether >= 0.01 in fell
           (0 = perfect, 0.25 = always saying 50%)
  rainHit  share of days where (pop >= 50%) matched (rain happened)
"""
import datetime as dt
import glob
import json
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ("google", "nws")
MAX_LEAD = 6
RAIN_THRESHOLD_IN = 0.01


def load_dir(name):
    out = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "data", name, "*.json"))):
        with open(p) as f:
            out[os.path.basename(p)[:-5]] = json.load(f)
    return out


def mean(xs):
    return round(statistics.fmean(xs), 2) if xs else None


def main():
    forecasts = load_dir("forecasts")
    obs = {k: v for k, v in load_dir("obs").items() if v.get("complete")}

    # errors[source][lead] -> dict of lists
    errors = {s: [dict(hi=[], lo=[], biasHi=[], biasLo=[], brier=[], hit=[]) for _ in range(MAX_LEAD + 1)]
              for s in SOURCES}
    recent = {}

    for issued, rec in forecasts.items():
        i_date = dt.date.fromisoformat(issued)
        for src in SOURCES:
            days = rec.get(src) or {}
            for target, fc in days.items():
                lead = (dt.date.fromisoformat(target) - i_date).days
                if not 0 <= lead <= MAX_LEAD:
                    continue
                o = obs.get(target)
                if not o:
                    continue
                e = errors[src][lead]
                if fc.get("hi") is not None and o.get("hi") is not None:
                    e["hi"].append(abs(fc["hi"] - o["hi"]))
                    e["biasHi"].append(fc["hi"] - o["hi"])
                if fc.get("lo") is not None and o.get("lo") is not None:
                    e["lo"].append(abs(fc["lo"] - o["lo"]))
                    e["biasLo"].append(fc["lo"] - o["lo"])
                if fc.get("popDay") is not None and o.get("rainIn") is not None:
                    rained = 1.0 if o["rainIn"] >= RAIN_THRESHOLD_IN else 0.0
                    p = fc["popDay"] / 100.0
                    e["brier"].append((p - rained) ** 2)
                    e["hit"].append(1.0 if (p >= 0.5) == bool(rained) else 0.0)
                if lead == 1:
                    r = recent.setdefault(target, {"date": target, "obs": {"hi": o.get("hi"), "lo": o.get("lo"), "rainIn": o.get("rainIn")}})
                    r[src] = {"hi": fc.get("hi"), "lo": fc.get("lo"), "popDay": fc.get("popDay")}

    card = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "forecastDays": len(forecasts),
        "observedDays": len(obs),
        "definitions": {
            "high": "max temp 07:00-19:00 local", "low": "min temp 19:00-07:00 next day",
            "rain": f"rain >= {RAIN_THRESHOLD_IN} in between 07:00 and 07:00 next day",
            "brier": "mean (pop - outcome)^2; lower is better", "unit": "F",
        },
        "sources": {},
        "recent": [recent[k] for k in sorted(recent)[-14:]],
    }
    for src in SOURCES:
        card["sources"][src] = {"byLead": []}
        for lead, e in enumerate(errors[src]):
            card["sources"][src]["byLead"].append({
                "lead": lead, "n": len(e["hi"]),
                "maeHigh": mean(e["hi"]), "maeLow": mean(e["lo"]),
                "biasHigh": mean(e["biasHi"]), "biasLow": mean(e["biasLo"]),
                "brier": round(statistics.fmean(e["brier"]), 3) if e["brier"] else None,
                "rainHit": mean(e["hit"]),
            })

    path = os.path.join(ROOT, "data", "scorecard.json")
    with open(path, "w") as f:
        json.dump(card, f, indent=1, sort_keys=True)
    print(f"scorecard: {len(forecasts)} forecast days, {len(obs)} observed days")


if __name__ == "__main__":
    main()
