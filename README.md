# Medellín trip map data

Real geodata for a personal Medellín to Guatapé trip map (Nov 25 to 29, 2026).

`scripts/fetch.py` runs in GitHub Actions and saves into `data/`:

| Data | Source | License / attribution |
|---|---|---|
| Terrain (terrarium DEM tiles) | [Mapterhorn](https://mapterhorn.com) | See `data/terrain/tilejson.json` attribution |
| Basemap vector tiles, glyphs, sprites | [OpenFreeMap](https://openfreemap.org), OpenMapTiles schema | © OpenMapTiles, © OpenStreetMap contributors |
| Stop geocodes | Nominatim and Overpass | © OpenStreetMap contributors (ODbL) |
| Road routes | OSRM public demo server | © OpenStreetMap contributors |
