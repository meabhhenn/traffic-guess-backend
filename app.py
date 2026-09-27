"""
Traffic Guess Game - backend API (HW4).

The HW3 game was one Flask app that rendered HTML pages. This version splits it:
  - This backend (deployed on Render) owns the data, the answers, the scoring,
    and the Mapbox API key.
  - The frontend (a static page on GitHub Pages) calls these JSON endpoints.

Endpoints:
  GET  /            -> health check
  GET  /api/round   -> a new round's clues (no answer included)
  POST /api/guess   -> score a guess, reveal the real speed
  GET  /api/map     -> map image of the region (fetched from Mapbox server-side)
"""

import csv
import json
import os
import random
import urllib.parse
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

load_dotenv()  # reads .env when running locally; on Render, env vars come from the dashboard

app = Flask(__name__)

# Only these sites may call the API from a browser.
ALLOWED_ORIGINS = [
    "https://meabhhenn.github.io",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",   # VS Code Live Server
    "http://127.0.0.1:5500",
]
CORS(app, origins=ALLOWED_ORIGINS)

# The secret. Read from the environment, never hard-coded, never sent to the browser.
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN")

TIGHT_MPH = 1.0   # within 1 mph  -> 3 points
LOOSE_MPH = 3.0   # within 3 mph  -> 1 point
MAX_REASONABLE_MPH = 100
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
REGION_NAME = "Chicago Loop"
REGION_DESCRIPTION = "Downtown Chicago's central business district, bounded roughly by the Chicago River, Roosevelt Road and Lake Michigan."
REGION_BOUNDS = {"west": -87.647208, "east": -87.62308, "south": 41.866129, "north": 41.88886}
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "joined.csv")


# ---------- Load the dataset once at startup ----------

def load_rows(path):
    """Read joined.csv (made by prepare_data.py in the HW3 repo) into a list of dicts."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "hour_ts": datetime.fromisoformat(r["hour_ts"]),
                    "avg_speed_mph": float(r["avg_speed_mph"]),
                    "temp_f": float(r["temp_f"]),
                    "precip_mm": float(r["precip_mm"]),
                    "wind_mph": float(r["wind_mph"]),
                })
            except (KeyError, ValueError):
                continue  # skip malformed rows instead of crashing
    return rows


ROWS = load_rows(DATA_PATH)
if not ROWS:
    raise RuntimeError(f"No usable rows found in {DATA_PATH}")

SPEEDS = [r["avg_speed_mph"] for r in ROWS]
STATS = {
    "min": round(min(SPEEDS), 1),
    "max": round(max(SPEEDS), 1),
    "mean": round(sum(SPEEDS) / len(SPEEDS), 1),
    "rows": len(ROWS),
}


def is_interesting(r):
    """Same idea as HW3: extreme weather hours are 3x more likely to be picked."""
    return r["precip_mm"] > 0 or r["wind_mph"] > 20 or r["temp_f"] < 20 or r["temp_f"] > 90


WEIGHTS = [3 if is_interesting(r) else 1 for r in ROWS]


def error(message, status):
    """Every error comes back as JSON, so the frontend can always show a message."""
    return jsonify({"error": message}), status


# ---------- Endpoints ----------

@app.get("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "traffic-guess-backend",
        "rows_loaded": STATS["rows"],
        "map_configured": bool(MAPBOX_TOKEN),
        "endpoints": ["GET /api/round", "POST /api/guess", "GET /api/map"],
    })


@app.get("/api/round")
def new_round():
    """Pick a random historical hour and return the clues, but NOT the speed."""
    idx = random.choices(range(len(ROWS)), weights=WEIGHTS, k=1)[0]
    r = ROWS[idx]
    ts = r["hour_ts"]
    dow = ts.weekday()
    return jsonify({
        "round_id": idx,
        "region": REGION_NAME,
        "region_description": REGION_DESCRIPTION,
        "date": ts.strftime("%B %d, %Y"),
        "time": ts.strftime("%I:%M %p").lstrip("0"),
        "day_name": DAY_NAMES[dow],
        "day_type": "Weekend" if dow >= 5 else "Weekday",
        "weather": {
            "temp_f": round(r["temp_f"]),
            "precip_mm": round(r["precip_mm"], 1),
            "wind_mph": round(r["wind_mph"]),
        },
        "speed_range": {"min": STATS["min"], "max": STATS["max"], "mean": STATS["mean"]},
    })


@app.post("/api/guess")
def score_guess():
    """Body: {"round_id": int, "guess": number}. Returns the real speed and points earned."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("Request body must be JSON like {\"round_id\": 12, \"guess\": 18.5}.", 400)

    round_id = body.get("round_id")
    if isinstance(round_id, bool) or not isinstance(round_id, int) or not (0 <= round_id < len(ROWS)):
        return error("Missing or invalid round_id. Start a new round with GET /api/round.", 400)

    raw_guess = body.get("guess")
    if raw_guess is None or (isinstance(raw_guess, str) and not raw_guess.strip()):
        return error("Please enter a guess.", 400)
    try:
        guess = float(raw_guess)
    except (TypeError, ValueError):
        return error(f"'{raw_guess}' isn't a number. Try again.", 400)
    if guess != guess or guess < 0 or guess > MAX_REASONABLE_MPH:  # guess != guess catches NaN
        return error(f"Guess must be between 0 and {MAX_REASONABLE_MPH} mph.", 400)

    actual = ROWS[round_id]["avg_speed_mph"]
    diff = abs(guess - actual)
    if diff <= TIGHT_MPH:
        points, verdict = 3, "Excellent! Within 1 mph."
    elif diff <= LOOSE_MPH:
        points, verdict = 1, "Close! Within 3 mph."
    else:
        points, verdict = 0, "Not quite."

    return jsonify({
        "guess": round(guess, 1),
        "actual": round(actual, 1),
        "diff": round(diff, 1),
        "points": points,
        "verdict": verdict,
    })


# The map image is fetched once from Mapbox using the secret token, then cached in memory.
_map_cache = {"png": None}


@app.get("/api/map")
def region_map():
    if _map_cache["png"]:
        return Response(_map_cache["png"], mimetype="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    if not MAPBOX_TOKEN:
        return error("Map is unavailable: MAPBOX_TOKEN is not set on the server.", 503)

    b = REGION_BOUNDS
    geojson = {
        "type": "Feature",
        "properties": {"stroke": "#ff3b30", "stroke-width": 3, "fill": "#ff3b30", "fill-opacity": 0.15},
        "geometry": {"type": "Polygon", "coordinates": [[
            [b["west"], b["south"]], [b["east"], b["south"]],
            [b["east"], b["north"]], [b["west"], b["north"]], [b["west"], b["south"]],
        ]]},
    }
    url = ("https://api.mapbox.com/styles/v1/mapbox/streets-v12/static/"
           f"geojson({urllib.parse.quote(json.dumps(geojson))})/auto/900x700")
    try:
        resp = requests.get(url, params={"access_token": MAPBOX_TOKEN}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        # Don't echo the exception: it contains the URL, which contains the token.
        return error("Couldn't fetch the map from Mapbox right now.", 502)

    _map_cache["png"] = resp.content
    return Response(resp.content, mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.errorhandler(404)
def not_found(_e):
    return error("No such endpoint. Try GET /api/round.", 404)


@app.errorhandler(405)
def wrong_method(_e):
    return error("Wrong HTTP method for this endpoint.", 405)


@app.errorhandler(500)
def server_error(_e):
    return error("Something went wrong on the server.", 500)


if __name__ == "__main__":
    # Port 5050, not 5000: on macOS, AirPlay Receiver grabs 5000 (the HW3 "403" bug).
    port = int(os.environ.get("PORT", 5050))
    app.run(host="127.0.0.1", port=port, debug=True)
