"""
MediPulse AI - Flask backend
Core route: /nearby_search (hospitals + pharmacies via Overpass/OSM)

Merge the /nearby_search route, its helpers, and OVERPASS_ENDPOINTS /
OVERPASS_HEADERS into your existing app.py. This file is also runnable
standalone for testing.
"""

import os
from math import radians, sin, cos, sqrt, atan2

import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# =========================================================
# CONFIG
# =========================================================

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

OVERPASS_HEADERS = {
    # Overpass asks for a real contact in the User-Agent for heavy use.
    "User-Agent": "MediPulseAI/1.0 (contact: sahayasathish60@gmail.com)"
}


# =========================================================
# HELPERS
# =========================================================

def calculate_distance_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km between two coordinates."""
    R = 6371.0

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def normalize_osm_element(element):
    """
    Convert an OSM node/way/relation into a flat dict with lat/lon.

    Nodes: element["lat"], element["lon"]
    Ways/relations (from `out center`): element["center"]["lat"/"lon"]
    This is the fix for the original bug where way/relation results
    were silently dropped because the frontend only read place.lat/lon.
    """

    lat = element.get("lat")
    lon = element.get("lon")

    if lat is None or lon is None:
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")

    if lat is None or lon is None:
        return None

    tags = element.get("tags", {})

    name = (
        tags.get("name")
        or tags.get("official_name")
        or tags.get("short_name")
        or "Unnamed medical facility"
    )

    address = tags.get("addr:full") or " ".join(
        filter(
            None,
            [
                tags.get("addr:housenumber"),
                tags.get("addr:street"),
                tags.get("addr:city"),
            ],
        )
    )

    return {
        "id": f"{element.get('type', 'osm')}_{element.get('id')}",
        "osm_id": element.get("id"),
        "osm_type": element.get("type"),
        "lat": float(lat),
        "lon": float(lon),
        "tags": tags,
        "name": name,
        "address": address,
        "phone": tags.get("phone") or tags.get("contact:phone") or "",
        "website": tags.get("website") or tags.get("contact:website") or "",
    }


# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def index():
    return render_template("medical_map.html")


@app.route("/nearby_search", methods=["GET"])
def nearby_search():
    try:
        place_type = request.args.get("type", "").lower()
        raw_lat = request.args.get("lat")
        raw_lng = request.args.get("lng")

        if not raw_lat or not raw_lng:
            return jsonify({
                "status": "error",
                "error": "Latitude and longitude are required."
            }), 400

        try:
            lat = float(raw_lat)
            lng = float(raw_lng)
        except ValueError:
            return jsonify({
                "status": "error",
                "error": "Latitude and longitude must be numbers."
            }), 400

        if place_type not in ["hospital", "pharmacy"]:
            return jsonify({
                "status": "error",
                "error": "type must be hospital or pharmacy."
            }), 400

        # -------------------------------------------------
        # SEARCH TAGS
        # -------------------------------------------------

        if place_type == "hospital":
            osm_filter = """
                nwr["amenity"="hospital"](around:{radius},{lat},{lng});
                nwr["healthcare"="hospital"](around:{radius},{lat},{lng});
                nwr["amenity"="clinic"](around:{radius},{lat},{lng});
                nwr["healthcare"="clinic"](around:{radius},{lat},{lng});
                nwr["healthcare"="centre"](around:{radius},{lat},{lng});
                nwr["healthcare"="doctor"](around:{radius},{lat},{lng});
            """
        else:
            osm_filter = """
                nwr["amenity"="pharmacy"](around:{radius},{lat},{lng});
                nwr["healthcare"="pharmacy"](around:{radius},{lat},{lng});
                nwr["shop"="chemist"](around:{radius},{lat},{lng});
            """

        # -------------------------------------------------
        # TRY MULTIPLE RADII: 10km -> 25km -> 50km
        # -------------------------------------------------

        radii = [10000, 25000, 50000]
        all_results = []
        errors = []

        for radius in radii:
            query = f"""
            [out:json][timeout:25];
            (
                {osm_filter.format(radius=radius, lat=lat, lng=lng)}
            );
            out center;
            """

            found = False

            for endpoint in OVERPASS_ENDPOINTS:
                try:
                    response = requests.post(
                        endpoint,
                        data={"data": query},
                        headers=OVERPASS_HEADERS,
                        timeout=30,
                    )

                    if response.status_code != 200:
                        errors.append(f"{endpoint}: HTTP {response.status_code}")
                        continue

                    result = response.json()

                    # Overpass can return HTTP 200 while reporting an
                    # internal timeout/error via "remark".
                    if result.get("remark"):
                        errors.append(f"{endpoint}: {result.get('remark')}")
                        continue

                    elements = result.get("elements", [])

                    for element in elements:
                        normalized = normalize_osm_element(element)
                        if not normalized:
                            continue

                        distance = calculate_distance_km(
                            lat, lng, normalized["lat"], normalized["lon"]
                        )
                        normalized["distance_km"] = round(distance, 2)
                        all_results.append(normalized)

                    found = True
                    break

                except Exception as e:
                    errors.append(f"{endpoint}: {str(e)}")

            # Got a successful response with results -> stop widening radius.
            if found and all_results:
                break
            # Successful but empty -> try the next, wider radius.

        # -------------------------------------------------
        # DEDUPE
        # -------------------------------------------------

        unique = {}
        for item in all_results:
            key = (
                item["name"].lower().strip(),
                round(item["lat"], 5),
                round(item["lon"], 5),
            )
            unique[key] = item

        results = list(unique.values())
        results.sort(key=lambda x: x.get("distance_km", 999999))

        if results:
            return jsonify({
                "status": "success",
                "type": place_type,
                "count": len(results),
                "elements": results,
                "user_location": {"lat": lat, "lng": lng},
                "search_radius_km": max(x["distance_km"] for x in results),
            })

        return jsonify({
            "status": "empty",
            "type": place_type,
            "count": 0,
            "elements": [],
            "user_location": {"lat": lat, "lng": lng},
            "message": f"No {place_type}s were found in OpenStreetMap near your location.",
            "debug": errors[-5:],
        })

    except Exception as e:
        print("NEARBY SEARCH CRITICAL ERROR:", str(e))
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/map_ai", methods=["POST"])
def map_ai():
    """
    Minimal placeholder for the AI chat endpoint used by medical_map.html.
    Wire this up to your existing Anthropic API call / prompt logic.
    The frontend already has a local fallback if this fails or 404s.
    """
    data = request.get_json(force=True) or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"reply": "I didn't catch a question — try asking again."})

    # TODO: replace with a real call to your AI provider using `data`
    # (message, destination, distance, duration, latitude, longitude, speed)
    return jsonify({
        "reply": "AI assistant is not fully configured yet on the backend — "
                 "using local navigation info in the meantime."
    })


if __name__ == "__main__":
    app.run(debug=True)
