# =========================================================
# NEARBY SEARCH (Overpass / OpenStreetMap - free, no signup)
# =========================================================
# Replace everything in app.py from the "NEARBY SEARCH" header down to
# (but not including) the "API CONFIG" header with this file.
#
# What was broken:
#  1. medical_map.html expects {status, elements:[{id, lat, lon, name, address,
#     distance_km, ...}], search_radius_km}. The old route returned the raw
#     Overpass JSON: no "status", no "name", no "distance_km", and results
#     mapped as OSM "ways" keep their coordinates in "center", so the page
#     dropped them.
#  2. overpass.osm.ch only serves Switzerland, so it always came back empty
#     for India. It is removed.
#  3. Overpass "remark" errors (timeouts) were treated like real answers.
#
# Response contract:
#   success: {status:"success", elements:[...], search_radius_km, source, user_location}
#   empty:   {status:"empty",   elements:[],    message, search_radius_km}
#   error:   {status:"error",   error}

import time
from math import radians, sin, cos, asin, sqrt

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

OVERPASS_HEADERS = {
    "User-Agent": "MediPulseAI/1.0 (nearby medical facility search; contact: sahayasathish60@gmail.com)"
}

RADII_METERS = [10000, 25000, 50000]   # widen only if the smaller radius finds too little
MIN_GOOD_RESULTS = 3                   # stop widening once we have this many
MAX_RESULTS = 25
MIRROR_TIMEOUT_S = 15                  # per Overpass mirror
SEARCH_TIME_BUDGET_S = 40              # whole request, all radii and mirrors
CACHE_TTL_S = 300                      # repeat searches from the same spot are instant

_search_cache = {}


def distance_km(lat1, lon1, lat2, lon2):
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def build_overpass_query(place_type, lat, lng, radius):
    around = f"(around:{radius},{lat},{lng})"
    if place_type == "pharmacy":
        filters = ['["amenity"="pharmacy"]', '["healthcare"="pharmacy"]']
    else:
        filters = ['["amenity"~"^(hospital|clinic)$"]', '["healthcare"~"^(hospital|clinic)$"]']
    body = "\n".join(f"  nwr{f}{around};" for f in filters)
    # "out center" gives ways/relations a center point, which we read below
    return f"[out:json][timeout:20];\n(\n{body}\n);\nout center;"


def run_overpass(query, deadline):
    """Try each mirror in turn. Returns (elements, errors).
    elements is None when every mirror failed, which is different from []
    (a real answer meaning 'nothing there')."""
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        if time.monotonic() >= deadline:
            errors.append("time budget used up")
            break
        try:
            resp = requests.post(endpoint, data={"data": query}, headers=OVERPASS_HEADERS, timeout=MIRROR_TIMEOUT_S)
            resp.raise_for_status()
            result = resp.json()
            if result.get("remark"):
                errors.append(f"{endpoint} -> remark: {result['remark']}")
                continue
            return result.get("elements", []), errors
        except Exception as e:
            errors.append(f"{endpoint} -> {type(e).__name__}: {e}")
    return None, errors


def build_address(tags):
    if tags.get("addr:full"):
        return tags["addr:full"]
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    parts = [
        street,
        tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
        tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or tags.get("addr:district"),
        tags.get("addr:postcode"),
    ]
    return ", ".join(p for p in parts if p)


def normalize_element(el, place_type, user_lat, user_lng):
    tags = el.get("tags") or {}

    # nodes have lat/lon, ways and relations have center.lat/center.lon
    lat, lon = el.get("lat"), el.get("lon")
    if lat is None or lon is None:
        center = el.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None

    if place_type == "pharmacy":
        subtype = "pharmacy"
    else:
        subtype = "hospital" if "hospital" in (tags.get("amenity"), tags.get("healthcare")) else "clinic"

    return {
        "id": f"{el.get('type', 'node')}/{el.get('id')}",
        "lat": lat,
        "lon": lon,
        "name": tags.get("name") or tags.get("name:en") or tags.get("official_name") or tags.get("operator") or "",
        "address": build_address(tags),
        "phone": (tags.get("phone") or tags.get("contact:phone") or "").split(";")[0].strip(),
        "website": tags.get("website") or tags.get("contact:website") or "",
        "opening_hours": tags.get("opening_hours") or "",
        "subtype": subtype,
        "distance_km": round(distance_km(user_lat, user_lng, lat, lon), 3),
    }


def dedupe(items):
    """OSM often has both a building outline and a point for the same place.
    Items must already be sorted by distance."""
    kept = []
    for item in items:
        name = item["name"].strip().lower()
        is_duplicate = any(
            name and name == k["name"].strip().lower()
            and distance_km(item["lat"], item["lon"], k["lat"], k["lon"]) < 0.15
            for k in kept
        )
        if not is_duplicate:
            kept.append(item)
    return kept


def process_elements(elements, place_type, lat, lng):
    items = [normalize_element(el, place_type, lat, lng) for el in elements]
    items = [i for i in items if i]
    items.sort(key=lambda i: i["distance_km"])
    return dedupe(items)


def nominatim_fallback(place_type, lat, lng):
    """Last resort if every Overpass mirror is down or empty."""
    term = "hospital" if place_type == "hospital" else "pharmacy"
    delta = 0.35
    params = {
        "format": "jsonv2",
        "q": term,
        "viewbox": f"{lng - delta},{lat + delta},{lng + delta},{lat - delta}",
        "bounded": 1,
        "limit": 25,
    }
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/search", params=params,
                            headers=OVERPASS_HEADERS, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print("NOMINATIM FALLBACK ERROR:", e)
        return []

    items = []
    for row in rows:
        try:
            r_lat, r_lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        parts = (row.get("display_name") or "").split(",")
        items.append({
            "id": f"nominatim/{row.get('place_id')}",
            "lat": r_lat,
            "lon": r_lon,
            "name": row.get("name") or parts[0].strip(),
            "address": ", ".join(p.strip() for p in parts[1:4]),
            "phone": "",
            "website": "",
            "opening_hours": "",
            "subtype": place_type,
            "distance_km": round(distance_km(lat, lng, r_lat, r_lon), 3),
        })
    items.sort(key=lambda i: i["distance_km"])
    return dedupe(items)


@app.route("/nearby_search", methods=["GET"])
def nearby_search():
    try:
        place_type = request.args.get("type")
        if place_type not in ("hospital", "pharmacy"):
            return jsonify({"status": "error", "error": "type must be 'hospital' or 'pharmacy'"}), 400

        try:
            lat = float(request.args.get("lat", ""))
            lng = float(request.args.get("lng", ""))
        except ValueError:
            return jsonify({"status": "error", "error": "lat and lng must be numbers"}), 400
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return jsonify({"status": "error", "error": "lat/lng out of range"}), 400

        label = "hospitals" if place_type == "hospital" else "pharmacies"

        # Same place, same answer: skip the slow Overpass round trip
        cache_key = (place_type, round(lat, 3), round(lng, 3))
        cached = _search_cache.get(cache_key)
        if cached and time.time() - cached[0] < CACHE_TTL_S:
            return jsonify(cached[1])

        deadline = time.monotonic() + SEARCH_TIME_BUDGET_S
        best_items, best_radius, overpass_answered, all_errors = [], None, False, []

        for radius in RADII_METERS:
            elements, errors = run_overpass(build_overpass_query(place_type, lat, lng, radius), deadline)
            all_errors += errors
            if elements is None:
                break  # every mirror failed; a bigger radius will not help
            overpass_answered = True
            best_radius = radius
            best_items = process_elements(elements, place_type, lat, lng)
            if len(best_items) >= MIN_GOOD_RESULTS:
                break

        source = "overpass"
        if not best_items:
            fallback_items = nominatim_fallback(place_type, lat, lng)
            if fallback_items:
                best_items, best_radius, source = fallback_items, 35000, "nominatim"

        if all_errors:
            print("NEARBY SEARCH mirror errors:", all_errors)

        if best_items:
            payload = {
                "status": "success",
                "elements": best_items[:MAX_RESULTS],
                "search_radius_km": (best_radius or RADII_METERS[-1]) / 1000,
                "source": source,
                "user_location": {"lat": lat, "lng": lng},
            }
            if len(_search_cache) > 200:
                _search_cache.clear()
            _search_cache[cache_key] = (time.time(), payload)
            return jsonify(payload)

        if overpass_answered:
            radius_km = (best_radius or RADII_METERS[-1]) / 1000
            return jsonify({
                "status": "empty",
                "elements": [],
                "message": f"No {label} found within {radius_km:.0f} km of your location.",
                "search_radius_km": radius_km,
            })

        return jsonify({
            "status": "error",
            "error": "The map data servers are busy right now. Please try again in a moment.",
        }), 503

    except Exception as e:
        print("nearby_search crashed:", e)
        return jsonify({"status": "error", "error": "Something went wrong while searching. Please try again."}), 500
