"""Pack fetched real geodata into a few files the artifact page loads, and build the page.

Inputs : data/ (from fetch.py), scripts/stops.json, app/page.template.html, maplibre css
Outputs: build/medellin-trip-map.html, build/data/*.bin
"""
import copy, json, math, os, sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BUILD = os.path.join(ROOT, "build")
os.makedirs(os.path.join(BUILD, "data"), exist_ok=True)
REGION = [-75.72, 6.05, -75.08, 6.40]

# ---------- 1. pack tiles into bins ----------
def walk(sub):
    base = os.path.join(DATA, sub)
    for d, _, files in os.walk(base):
        for f in files:
            full = os.path.join(d, f)
            yield os.path.relpath(full, DATA).replace(os.sep, "/"), full

entries = {}
def bin_for(path):
    parts = path.split("/")
    kind = parts[0]
    if kind == "terrain":
        z = int(parts[1])
        if z < 12:
            return "base.bin"
        x = int(parts[2])
        return "terrain12-w.bin" if x < 1190 else "terrain12-e.bin"
    if kind == "vector":
        z, x = int(parts[1]), int(parts[2])
        if z <= 12:
            return "base.bin"
        if z == 13:
            return "vector13.bin"
        # z14: split by x so each pack stays a few MB
        return f"vector14-{(x - 4745) // 8}.bin"
    return "base.bin"

for sub in ("vector", "terrain", "glyphs", "sprites"):
    for rel, full in walk(sub):
        if rel.endswith("tilejson.json"):
            continue
        entries[rel] = full

bins = {}
index = {}
for rel in sorted(entries):
    b = bin_for(rel)
    bins.setdefault(b, bytearray())
    data = open(entries[rel], "rb").read()
    index[rel] = [b, len(bins[b]), len(data)]
    bins[b].extend(data)
sizes = {}
for b, buf in bins.items():
    open(os.path.join(BUILD, "data", b), "wb").write(buf)
    sizes[b] = round(len(buf) / 1e6, 2)
print("bins MB:", sizes, "entries:", len(index))
assert max(sizes.values()) < 14.5, "a pack exceeds the 15 MB per-file limit"

# Real extent of what was downloaded at the deepest zooms (tile edges, not the request box)
def tile_lon(x, z): return x / 2 ** z * 360 - 180
def tile_lat(y, z):
    n = math.pi - 2 * math.pi * y / 2 ** z; return math.degrees(math.atan(math.sinh(n)))
xs = sorted(int(x) for x in os.listdir(os.path.join(DATA, "terrain", "12")))
ys = sorted(int(f.split(".")[0]) for x in xs for f in os.listdir(os.path.join(DATA, "terrain", "12", str(x))))
data_bounds = [round(tile_lon(xs[0], 12), 4), round(tile_lat(ys[-1] + 1, 12), 4), round(tile_lon(xs[-1] + 1, 12), 4), round(tile_lat(ys[0], 12), 4)]
print("data bounds", data_bounds)

# ---------- 2. style: Liberty, restyled, pointed at local packs ----------
style = json.load(open(os.path.join(DATA, "styles", "liberty.json")))
vtj = json.load(open(os.path.join(DATA, "vector", "tilejson.json")))
ttj = json.load(open(os.path.join(DATA, "terrain", "tilejson.json")))
style["sources"] = {"openmaptiles": {"type": "vector", "tiles": ["trip://vector/{z}/{x}/{y}.pbf"], "minzoom": 0, "maxzoom": 14,
                                      "bounds": data_bounds}}
style["glyphs"] = "trip://glyphs/{fontstack}/{range}.pbf"
style["sprite"] = "trip://sprites/default"
drop = {"natural_earth", "highway-shield-us-interstate", "road_shield_us"}
style["layers"] = [l for l in style["layers"] if l["id"] not in drop]
style.pop("terrain", None); style.pop("sky", None)

fonts_ok = {"Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic"}
for l in style["layers"]:
    f = l.get("layout", {}).get("text-font")
    if isinstance(f, list) and all(isinstance(s, str) for s in f):
        l["layout"]["text-font"] = [s for s in f if s in fonts_ok] or ["Noto Sans Regular"]

# Palettes: [light, dark]. Clean, low-chroma base so the trip colours carry the story.
P = {
    "background": ["#EEF0EC", "#111915"],
    "residential": ["#E7E8E3", "#18211C"],
    "wood": ["#D2E1C8", "#16251B"],
    "grass": ["#DDE8D2", "#18261C"],
    "park": ["#D4E5C8", "#173020"],
    "park_line": ["#B9D3A8", "#22402B"],
    "water": ["#A7CCDD", "#17384A"],
    "waterway": ["#8DBCD3", "#1F4A60"],
    "landuse_other": ["#E4E3DC", "#1B231F"],
    "aeroway": ["#DADBD5", "#232C27"],
    "runway": ["#FFFFFF", "#38433D"],
    "road_minor": ["#FFFFFF", "#2A3530"],
    "road_major": ["#FFFFFF", "#3A4741"],
    "road_motorway": ["#F6DDA9", "#5A5340"],
    "casing": ["#D3D0C7", "#0E1612"],
    "path": ["#C9C5BB", "#3A4540"],
    "rail": ["#B8B5AD", "#46504B"],
    "building": ["#DCD8D0", "#232D28"],
    "building3d": ["#E2DDD4", "#2B3631"],
    "boundary": ["#B8B1C9", "#4A4560"],
    "label": ["#3E4740", "#C8D3CC"],
    "label_halo": ["#F7F8F5", "#0E1612"],
    "water_label": ["#3E6E86", "#86BCD6"],
}
def group(lid, typ):
    if lid == "background": return "background"
    if lid == "landuse_residential": return "residential"
    if lid == "landcover_wood": return "wood"
    if lid in ("landcover_grass", "landcover_wetland"): return "grass"
    if lid == "park": return "park"
    if lid == "park_outline": return "park_line"
    if lid == "water": return "water"
    if lid.startswith("waterway") and typ == "line": return "waterway"
    if lid.startswith("landuse_") or lid in ("landcover_ice", "landcover_sand"): return "landuse_other"
    if lid == "aeroway_fill": return "aeroway"
    if lid in ("aeroway_runway", "aeroway_taxiway"): return "runway"
    if lid == "building": return "building"
    if lid == "building-3d": return "building3d"
    if lid.startswith("boundary"): return "boundary"
    if typ == "line" and ("rail" in lid):
        return None if "hatching" in lid else "rail"
    if typ == "line" and lid.endswith("_casing"): return "casing"
    if typ == "line" and "path_pedestrian" in lid: return "path"
    if typ == "line" and "motorway" in lid: return "road_motorway"
    if typ == "line" and ("trunk_primary" in lid or "secondary_tertiary" in lid): return "road_major"
    if typ == "line" and any(k in lid for k in ("minor", "street", "service_track", "link")): return "road_minor"
    return None

PROP = {"background": "background-color", "fill": "fill-color", "line": "line-color", "fill-extrusion": "fill-extrusion-color"}
theme = {}
for l in style["layers"]:
    g = group(l["id"], l["type"])
    paint = l.setdefault("paint", {})
    if g and l["type"] in PROP:
        prop = PROP[l["type"]]
        paint[prop] = P[g][0]
        theme.setdefault(l["id"], {})[prop] = P[g]
        if l["type"] == "fill":
            paint.pop("fill-pattern", None)
            if "fill-outline-color" in paint:
                paint["fill-outline-color"] = P[g][0]
                theme[l["id"]]["fill-outline-color"] = P[g]
    if l["type"] == "symbol" and "text-field" in l.get("layout", {}):
        is_water = l["id"].startswith("water")
        paint["text-color"] = P["water_label" if is_water else "label"][0]
        paint["text-halo-color"] = P["label_halo"][0]
        theme.setdefault(l["id"], {})["text-color"] = P["water_label" if is_water else "label"]
        theme[l["id"]]["text-halo-color"] = P["label_halo"]
for l in style["layers"]:
    if l["id"] == "building-3d":
        l["minzoom"] = 13.5
        l["paint"]["fill-extrusion-opacity"] = 0.92
        l["paint"]["fill-extrusion-vertical-gradient"] = True
    if l["id"] == "building":
        l["paint"]["fill-opacity"] = ["interpolate", ["linear"], ["zoom"], 13, 0.6, 14, 1]

# Metrocable gondolas and metro lines from OSM (transportation class aerialway / transit)
cable = {"id": "metrocable", "type": "line", "source": "openmaptiles", "source-layer": "transportation", "minzoom": 11,
         "filter": ["==", ["get", "class"], "aerialway"],
         "layout": {"line-cap": "round"},
         "paint": {"line-color": "#7A5BC9", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.2, 16, 3], "line-dasharray": [2, 1.4]}}
cable_label = {"id": "metrocable-label", "type": "symbol", "source": "openmaptiles", "source-layer": "transportation", "minzoom": 13,
               "filter": ["==", ["get", "class"], "aerialway"],
               "layout": {"symbol-placement": "line", "text-field": "Metrocable", "text-font": ["Noto Sans Italic"], "text-size": 11},
               "paint": {"text-color": "#6A4FB5", "text-halo-color": P["label_halo"][0], "text-halo-width": 1.5}}
theme["metrocable"] = {"line-color": ["#7A5BC9", "#B9A3FF"]}
theme["metrocable-label"] = {"text-color": ["#6A4FB5", "#C9B8FF"], "text-halo-color": P["label_halo"]}
ins = next(i for i, l in enumerate(style["layers"]) if l["id"] == "building")
style["layers"][ins:ins] = [cable]
style["layers"].append(cable_label)

# ---------- 3. trip data ----------
stops_coord = json.load(open(os.path.join(ROOT, "scripts", "stops.json")))
def elevation(lat, lon, z=12):
    n = 2 ** z; xf = (lon + 180) / 360 * n; r = math.radians(lat)
    yf = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    x, y = int(xf), int(yf)
    im = Image.open(os.path.join(DATA, "terrain", str(z), str(x), f"{y}.webp")).convert("RGB")
    R, G, B = im.getpixel((min(511, int((xf - x) * 512)), min(511, int((yf - y) * 512))))
    return round(R * 256 + G + B / 256 - 32768)

meta = {
    "A": dict(name="José María Córdova Airport", short="Airport", mark="A", day="wed", q="Aeropuerto Internacional José María Córdova, Rionegro"),
    "H": dict(name="Sloh Hotel & Bar Manila", short="Sloh Hotel", mark="H", day="base", q="Sloh Hotel & Bar Manila, Calle 12 #43D-114, Medellín"),
    "C13": dict(name="Comuna 13 escalators", short="Comuna 13", mark="1", day="thu", q="Escaleras eléctricas Comuna 13, Medellín"),
    "MUS": dict(name="Museo de Antioquia", short="Museo de Antioquia", mark="2", day="thu", q="Museo de Antioquia, Medellín"),
    "PEN": dict(name="Piedra del Peñol", short="Piedra del Peñol", mark="3", day="fri", q="Piedra del Peñol, Guatapé"),
    "ARV": dict(name="Parque Arví (Metrocable station)", short="Parque Arví", mark="4", day="sat", q="Estación Arví Metrocable, Medellín"),
    "PAI": dict(name="Pueblito Paisa", short="Pueblito Paisa", mark="5", day="sat", q="Pueblito Paisa, Cerro Nutibara, Medellín"),
    "ELC": dict(name="El Cielo", short="El Cielo", mark="6", day="sat", q="Elcielo, Calle 7D #43C-36, Medellín"),
    "BOT": dict(name="Jardín Botánico", short="Jardín Botánico", mark="7", day="sun", q="Jardín Botánico de Medellín"),
}
stops = {}
for k, m in meta.items():
    lat, lon = stops_coord[k]
    stops[k] = dict(m, lat=lat, lng=lon, elev=elevation(lat, lon))

days = json.load(open(os.path.join(ROOT, "app", "itinerary.json")))
routes = json.load(open(os.path.join(DATA, "routes.geojson")))
for f in routes["features"]:  # trim precision to keep the page small
    f["geometry"]["coordinates"] = [[round(c[0], 5), round(c[1], 5)] for c in f["geometry"]["coordinates"]]

trip = {
    "region": REGION,
    "stops": stops,
    "days": days,
    "routes": routes,
    "packIndex": index,
    "packMB": round(sum(sizes.values())),
    "style": style,
    "themePatch": theme,
    "labelFont": ["Noto Sans Bold"],
    "exaggeration": 1.2,
    "minZoom": 9.4,
    "dataBounds": data_bounds,
    "demSource": {"type": "raster-dem", "tiles": ["trip://terrain/{z}/{x}/{y}.webp"], "tileSize": 512, "encoding": "terrarium",
                  "minzoom": 5, "maxzoom": 12, "bounds": data_bounds},
    "hillshadeBefore": "waterway_tunnel",
    "sky": {
        "light": {"sky-color": "#C9DCE6", "horizon-color": "#EEF2EE", "fog-color": "#EEF0EC", "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.6, "fog-ground-blend": 0.35, "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 12, 0.6, 15, 0.2]},
        "dark": {"sky-color": "#0A1418", "horizon-color": "#1A2A2A", "fog-color": "#111915", "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.6, "fog-ground-blend": 0.35, "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 12, 0.6, 15, 0.2]},
    },
    "cameras": {
        "valley": {"center": [-75.578, 6.238], "zoom": 11.6, "pitch": 58, "bearing": -22},
        "dayBearing": {"wed": -40, "thu": -15, "fri": -75, "sat": -30, "sun": -20},
        "stops": {
            "H": {"zoom": 16.1, "pitch": 64, "bearing": -25},
            "ELC": {"zoom": 16.2, "pitch": 64, "bearing": 20},
            "C13": {"zoom": 15.6, "pitch": 66, "bearing": 60},
            "MUS": {"zoom": 16.0, "pitch": 62, "bearing": -10},
            "PAI": {"zoom": 15.4, "pitch": 63, "bearing": 40},
            "BOT": {"zoom": 15.6, "pitch": 60, "bearing": -30},
            "ARV": {"zoom": 14.6, "pitch": 66, "bearing": -60},
            "PEN": {"zoom": 14.4, "pitch": 66, "bearing": -50},
            "A": {"zoom": 14.0, "pitch": 58, "bearing": -40},
        },
    },
    "attribution": ttj["attribution"] + " · <a href='https://openfreemap.org'>OpenFreeMap</a> <a href='https://www.openmaptiles.org/'>© OpenMapTiles</a> <a href='https://www.openstreetmap.org/copyright'>© OpenStreetMap contributors</a> · Routes: <a href='https://project-osrm.org'>OSRM</a>",
    "sourcesHtml": "Terrain: <a href='https://mapterhorn.com/attribution' target='_blank' rel='noopener noreferrer'>Mapterhorn</a> elevation tiles. Streets, water, buildings and stop locations: <a href='https://www.openstreetmap.org/copyright' target='_blank' rel='noopener noreferrer'>OpenStreetMap contributors</a> via <a href='https://openfreemap.org' target='_blank' rel='noopener noreferrer'>OpenFreeMap</a>. Road routes: <a href='https://project-osrm.org' target='_blank' rel='noopener noreferrer'>OSRM</a>. Downloaded Oct 8, 2026.",
}

# ---------- 4. page ----------
tpl = open(os.path.join(ROOT, "app", "page.template.html"), encoding="utf-8").read()
css = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "app", "maplibre-gl.css"), encoding="utf-8").read()
page = tpl.replace("/*MAPLIBRE_CSS*/", css).replace("/*TRIP_DATA*/", "window.TRIP=" + json.dumps(trip, ensure_ascii=False, separators=(",", ":")) + ";")
open(os.path.join(BUILD, "medellin-trip-map.html"), "w", encoding="utf-8").write(page)
print("page KB:", round(len(page.encode()) / 1024), "stops:", {k: (v["lat"], v["lng"], v["elev"]) for k, v in stops.items()})
