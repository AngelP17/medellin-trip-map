"""Download real geodata for the Medellín trip map.

Runs on a GitHub Actions runner (which has normal internet access) and writes
everything under data/ so the static map can work without outside requests.

Sources (all keyless):
  - Terrain: Mapterhorn terrarium tiles (tiles.mapterhorn.com)
  - Basemap: OpenFreeMap vector tiles, OpenMapTiles schema, OSM data
  - Geocoding: Nominatim + Overpass (OSM), 1 request/second
  - Road routes: OSRM public demo server (driving profile)
"""
import gzip, json, math, os, sys, time, urllib.error, urllib.parse, urllib.request

UA = "medellin-trip-map/1.0 (personal trip planner; github.com/AngelP17)"
OUT = "data"
LOG = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "failures": [], "counts": {}}

REGION = (-75.72, 6.05, -75.08, 6.40)          # Medellín to Guatapé
CITY = (-75.645, 6.145, -75.52, 6.315)          # Aburrá valley core
GUATAPE = (-75.215, 6.185, -75.135, 6.265)      # reservoir, town, rock
AIRPORT = (-75.445, 6.145, -75.400, 6.185)      # JMC Rionegro
ARVI = (-75.53, 6.25, -75.48, 6.30)             # Arví station area


def get(url, retries=4, accept=None):
    last = None
    for i in range(retries):
        try:
            h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
            if accept:
                h["Accept"] = accept
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=90) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip" or b[:2] == b"\x1f\x8b":
                    b = gzip.decompress(b)
                return r.status, b
        except urllib.error.HTTPError as e:
            if e.code in (204, 404):
                return e.code, b""
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(2 * (i + 1))
    return 0, last.encode() if last else b""


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def tx(lon, z):
    return int((lon + 180) / 360 * 2 ** z)


def ty(lat, z):
    r = math.radians(lat)
    return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)


def tiles(bbox, z):
    w, s, e, n = bbox
    return [(z, x, y) for x in range(tx(w, z), tx(e, z) + 1) for y in range(ty(n, z), ty(s, z) + 1)]


def fetch_tiles(kind, template, plan, ext):
    seen, ok, empty = set(), 0, 0
    for bbox, zooms in plan:
        for z in zooms:
            for t in tiles(bbox, z):
                if t in seen:
                    continue
                seen.add(t)
                z_, x, y = t
                url = template.replace("{z}", str(z_)).replace("{x}", str(x)).replace("{y}", str(y))
                status, body = get(url)
                if status == 200 and body:
                    save(f"{OUT}/{kind}/{z_}/{x}/{y}.{ext}", body)
                    ok += 1
                elif status in (204, 404):
                    empty += 1
                else:
                    LOG["failures"].append({"kind": kind, "tile": t, "status": status, "detail": body[:200].decode("utf-8", "replace")})
    LOG["counts"][kind] = {"requested": len(seen), "saved": ok, "empty": empty}
    print(kind, LOG["counts"][kind], flush=True)


def terrain():
    status, body = get("https://tiles.mapterhorn.com/tilejson.json")
    if status != 200:
        LOG["failures"].append({"kind": "terrain-tilejson", "status": status})
        return
    tj = json.loads(body)
    save(f"{OUT}/terrain/tilejson.json", json.dumps(tj, indent=2).encode())
    template = tj["tiles"][0]
    ext = template.rsplit(".", 1)[-1].split("?")[0]
    maxz = int(tj.get("maxzoom", 12))
    plan = [(REGION, range(5, min(12, maxz) + 1))]
    if maxz >= 13:
        plan += [(CITY, [13]), (GUATAPE, [13]), (ARVI, [13])]
    LOG["terrain_ext"] = ext
    fetch_tiles("terrain", template, plan, ext)


def basemap():
    for name in ("liberty", "positron", "bright"):
        st, body = get(f"https://tiles.openfreemap.org/styles/{name}")
        if st == 200:
            save(f"{OUT}/styles/{name}.json", body)
    style = json.load(open(f"{OUT}/styles/liberty.json"))
    src = style["sources"]["openmaptiles"]
    st, body = get(src["url"])
    tj = json.loads(body)
    save(f"{OUT}/vector/tilejson.json", json.dumps(tj, indent=2).encode())
    # Whole trip area down to z14 (the OpenMapTiles max zoom), so streets and
    # buildings exist wherever the viewer zooms in. MapLibre overzooms z14.
    plan = [(REGION, range(0, 15))]
    fetch_tiles("vector", tj["tiles"][0], plan, "pbf")
    # glyphs for every font stack used
    stacks = set()
    for layer in style["layers"]:
        f = layer.get("layout", {}).get("text-font")
        if isinstance(f, list) and all(isinstance(s, str) for s in f):
            stacks.add(",".join(f))
    stacks |= {"Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic"}
    n = 0
    for stack in sorted(stacks):
        for rng in ("0-255", "256-511", "8192-8447"):
            url = style["glyphs"].replace("{fontstack}", urllib.parse.quote(stack)).replace("{range}", rng)
            st, body = get(url)
            if st == 200:
                save(f"{OUT}/glyphs/{stack}/{rng}.pbf", body)
                n += 1
            else:
                LOG["failures"].append({"kind": "glyph", "stack": stack, "range": rng, "status": st})
    LOG["counts"]["glyphs"] = n
    sprites = style.get("sprite")
    if isinstance(sprites, str):
        sprites = [{"id": "default", "url": sprites}]
    for sp in sprites or []:
        for suf in (".json", ".png", "@2x.json", "@2x.png"):
            st, body = get(sp["url"] + suf)
            if st == 200:
                save(f"{OUT}/sprites/{sp['id']}{suf}", body)


STOPS = {
    "H": {"fallback": [6.2120, -75.5690], "queries": ["Sloh Hotel Medellín", "Sloh Hotel Manila Medellín"], "overpass": "Sloh"},
    "ELC": {"fallback": [6.2078, -75.5640], "queries": ["Elcielo Medellín", "El Cielo restaurante El Poblado Medellín"], "overpass": "[Ee]l ?[Cc]ielo"},
    "C13": {"fallback": [6.2553, -75.6140], "queries": ["Escaleras eléctricas Comuna 13 Medellín", "Comuna 13 San Javier Medellín"], "overpass": "[Ee]scaleras [Ee]l[eé]ctricas"},
    "MUS": {"fallback": [6.2519, -75.5693], "queries": ["Museo de Antioquia Medellín"], "overpass": "Museo de Antioquia"},
    "PAI": {"fallback": [6.2347, -75.5790], "queries": ["Pueblito Paisa Medellín"], "overpass": "Pueblito Paisa"},
    "BOT": {"fallback": [6.2716, -75.5637], "queries": ["Jardín Botánico de Medellín"], "overpass": "Jard[ií]n Bot[aá]nico"},
    "ARV": {"fallback": [6.2760, -75.4985], "queries": ["Estación Arví Metrocable", "Parque Arví Medellín"], "overpass": "Arv[ií]"},
    "PEN": {"fallback": [6.2225, -75.1783], "queries": ["Piedra del Peñol", "Piedra del Peñol Guatapé"], "overpass": "Pe[nñ]ol"},
    "A": {"fallback": [6.1645, -75.4231], "queries": ["Aeropuerto Internacional José María Córdova"], "overpass": "Jos[eé] Mar[ií]a C[oó]rdova"},
}


def geocode():
    out = {}
    w, s, e, n = REGION
    for key, spec in STOPS.items():
        res = []
        for q in spec["queries"]:
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": q, "format": "jsonv2", "limit": 5, "viewbox": f"{w},{n},{e},{s}", "bounded": 1, "extratags": 1})
            st, body = get(url)
            time.sleep(1.2)
            if st == 200:
                for r in json.loads(body):
                    res.append({"query": q, "name": r.get("display_name"), "lat": float(r["lat"]), "lon": float(r["lon"]),
                                "category": r.get("category"), "type": r.get("type"), "osm": f"{r.get('osm_type')}/{r.get('osm_id')}"})
        out[key] = {"fallback": spec["fallback"], "nominatim": res}
    # Overpass name search
    for key, spec in STOPS.items():
        q = f'[out:json][timeout:60];nwr["name"~"{spec["overpass"]}"]({s},{w},{n},{e});out center tags 40;'
        st, body = get("https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": q}))
        time.sleep(1.5)
        hits = []
        if st == 200:
            for el in json.loads(body).get("elements", []):
                c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
                t = el.get("tags", {})
                hits.append({"osm": f"{el['type']}/{el['id']}", "name": t.get("name"), "lat": c.get("lat"), "lon": c.get("lon"),
                             "kind": {k: t[k] for k in ("tourism", "amenity", "aeroway", "natural", "leisure", "railway", "aerialway", "highway", "public_transport") if k in t}})
        else:
            LOG["failures"].append({"kind": "overpass", "stop": key, "status": st})
        out[key]["overpass"] = hits
    save(f"{OUT}/geocode.json", json.dumps(out, indent=2, ensure_ascii=False).encode())


def pick(key, geo):
    """Coordinate used for routing only; the map's final pins are chosen after review."""
    g = geo[key]
    if g["overpass"]:
        h = [x for x in g["overpass"] if x["lat"]]
        if h:
            return h[0]["lat"], h[0]["lon"]
    if g["nominatim"]:
        return g["nominatim"][0]["lat"], g["nominatim"][0]["lon"]
    return tuple(g["fallback"])


LEGS = [("wed", "A", "H"), ("thu", "H", "C13"), ("thu", "C13", "MUS"), ("thu", "MUS", "H"), ("fri", "H", "PEN"), ("fri", "PEN", "H"),
        ("sat", "H", "ARV"), ("sat", "ARV", "PAI"), ("sat", "PAI", "ELC"), ("sun", "H", "BOT"), ("sun", "BOT", "A")]


def routes():
    geo = json.load(open(f"{OUT}/geocode.json"))
    override = {}
    if os.path.exists("scripts/stops.json"):
        override = json.load(open("scripts/stops.json"))
    feats = []
    for day, a, b in LEGS:
        pa = override.get(a) or pick(a, geo)
        pb = override.get(b) or pick(b, geo)
        url = f"https://router.project-osrm.org/route/v1/driving/{pa[1]},{pa[0]};{pb[1]},{pb[0]}?overview=full&geometries=geojson"
        st, body = get(url)
        time.sleep(1.2)
        if st == 200:
            r = json.loads(body)["routes"][0]
            feats.append({"type": "Feature", "properties": {"day": day, "from": a, "to": b, "km": round(r["distance"] / 1000, 1), "min": round(r["duration"] / 60)},
                          "geometry": r["geometry"]})
        else:
            LOG["failures"].append({"kind": "route", "leg": [a, b], "status": st})
    save(f"{OUT}/routes.geojson", json.dumps({"type": "FeatureCollection", "features": feats}).encode())
    LOG["counts"]["routes"] = len(feats)


def _overpass(q):
    st, body = get("https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": q}))
    time.sleep(1.5)
    if st != 200:
        LOG["failures"].append({"kind": "overpass2", "status": st, "q": q[:120]})
        return []
    return json.loads(body).get("elements", [])


def geocode2():
    """Targeted lookups for stops the name search could not place."""
    out = {}
    pob = "6.195,-75.580,6.225,-75.555"  # El Poblado south of Calle 10
    # Street intersections: nodes shared by two named streets
    def corner(a, b):
        q = (f'[out:json][timeout:60];way["highway"]["name"~"{a}"]({pob})->.a;'
             f'way["highway"]["name"~"{b}"]({pob})->.b;node(w.a)(w.b);out;'
             f'.a out tags 5;.b out tags 5;')
        return _overpass(q)
    out["H_corner_calle12_cra43D"] = corner("^Calle 12$", "^Carrera 43D$")
    out["H_corner_calle12_cra43E"] = corner("^Calle 12$", "^Carrera 43E$")
    out["H_named"] = _overpass(f'[out:json][timeout:60];nwr["name"~"[Ss][Ll][Oo][Hh]"](6.0,-75.8,6.5,-75.3);out center tags;')
    out["H_addr"] = _overpass(f'[out:json][timeout:60];nwr["addr:street"~"Calle 12"]["addr:housenumber"~"43D"]({pob});out center tags;')
    out["ELC_corner_calle7D_cra43C"] = corner("^Calle 7D$", "^Carrera 43C$")
    out["ELC_named"] = _overpass(f'[out:json][timeout:60];nwr["name"~"[Ee]l ?[Cc]ielo"]({pob});out center tags;')
    out["ELC_street_7D"] = _overpass(f'[out:json][timeout:60];way["highway"]["name"~"^Calle 7D$"]({pob});out center tags;')
    out["C13_conveying"] = _overpass('[out:json][timeout:60];way["conveying"](6.245,-75.63,6.27,-75.60);out center tags;')
    out["C13_attractions"] = _overpass('[out:json][timeout:60];nwr["tourism"](6.250,-75.625,6.262,-75.610);out center tags 40;')
    out["A_terminal"] = _overpass('[out:json][timeout:60];nwr["aeroway"="terminal"](6.15,-75.44,6.18,-75.41);out center tags;')
    for q in ["Calle 12 43D-114, El Poblado, Medellín", "Carrera 43E 12-12, Medellín", "Calle 7D 43C-36, Medellín"]:
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 3, "countrycodes": "co"})
        st, body = get(url)
        time.sleep(1.2)
        out["nominatim:" + q] = json.loads(body) if st == 200 else {"status": st}
    save(f"{OUT}/geocode2.json", json.dumps(out, indent=2, ensure_ascii=False).encode())


if __name__ == "__main__":
    import traceback
    steps = sys.argv[1:] or ["terrain", "basemap", "geocode", "routes"]
    try:
        for step in steps:
            globals()[step]()
    except Exception:  # noqa: BLE001
        LOG["failures"].append({"kind": "crash", "trace": traceback.format_exc()})
        print(traceback.format_exc(), flush=True)
    LOG["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    LOG["steps"] = steps
    save(f"{OUT}/fetch-log.json", json.dumps(LOG, indent=2).encode())
    print(json.dumps(LOG["counts"]), "failures:", len(LOG["failures"]))
