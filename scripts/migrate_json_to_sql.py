"""Migrate curated JSON into SQLite — the agent's query layer. Stdlib only.
Usage: python3 scripts/migrate_json_to_sql.py
Reads:  data/listings.json, data/hazards.json
Writes: data/listings.db (listings + hazards tables, fresh each run)
Pipeline: listings_hyd.csv --validate--> listings.json --migrate--> listings.db <--server
"""
import json, os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "listings.json")
HAZ = os.path.join(ROOT, "data", "hazards.json")
DST = os.path.join(ROOT, "data", "listings.db")

SCHEMA = """
DROP TABLE IF EXISTS listings;
CREATE TABLE listings (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  listing_type TEXT NOT NULL DEFAULT 'sale',
  price INTEGER,
  beds REAL,
  floor INTEGER,
  total_floors INTEGER,
  property_type TEXT,
  area_sqft INTEGER,
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  area TEXT,
  url TEXT,
  seller TEXT,
  seller_type TEXT,
  posted TEXT,
  description TEXT,
  source TEXT
);
CREATE INDEX idx_listings_type ON listings(listing_type, property_type);
CREATE INDEX idx_listings_price ON listings(price);
CREATE INDEX idx_listings_area ON listings(area);
DROP TABLE IF EXISTS hazards;
CREATE TABLE hazards (
  name TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  radius_km REAL NOT NULL
);
"""

def main():
    with open(SRC) as f:
        rows = json.load(f)
    try:
        with open(HAZ) as f:
            hazards = json.load(f)
    except FileNotFoundError:
        hazards = []
    if os.path.exists(DST):
        os.remove(DST)
    con = sqlite3.connect(DST)
    con.executescript(SCHEMA)
    cols = ["id", "title", "listing_type", "price", "beds", "floor", "total_floors",
            "property_type", "area_sqft", "lat", "lng", "area", "url", "seller",
            "seller_type", "posted", "description", "source"]
    for i, r in enumerate(rows, start=1):
        if not r.get("id") or r.get("lat") is None or r.get("lng") is None:
            print(f"INVALID row {i}: id/lat/lng required"); sys.exit(1)
        con.execute(f"INSERT INTO listings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [r.get("id"), r.get("title"), r.get("listing_type", "sale"), r.get("price"),
                     r.get("beds"), r.get("floor"), r.get("total_floors"), r.get("property_type"),
                     r.get("area_sqft"), r.get("lat"), r.get("lng"), r.get("area"), r.get("url"),
                     r.get("seller"), r.get("seller_type"), r.get("posted"), r.get("description"),
                     r.get("source", "curated")])
    for h in hazards:
        con.execute("INSERT INTO hazards (name, kind, lat, lng, radius_km) VALUES (?,?,?,?,?)",
                    [h["name"], h["kind"], h["lat"], h["lng"], h.get("radius_km", 1.0)])
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    m = con.execute("SELECT COUNT(*) FROM hazards").fetchone()[0]
    con.close()
    print(f"OK: {n} listings, {m} hazards → data/listings.db")

if __name__ == "__main__":
    main()
