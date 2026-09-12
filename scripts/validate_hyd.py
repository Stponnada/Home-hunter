"""Validate the curator's CSV and build listings_hyd.json. Stdlib only.
Usage: python3 scripts/validate_hyd.py
Reads:  data/listings_hyd.csv
Writes: data/listings_hyd.json (only if valid)
"""
import csv, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "listings_hyd.csv")
DST = os.path.join(ROOT, "data", "listings_hyd.json")
REQ = ["id", "title", "price_inr", "beds", "floor", "total_floors", "property_type",
       "lat", "lng", "area", "url", "seller", "seller_type", "posted", "description"]

def fail(msg):
    print("INVALID: " + msg); sys.exit(1)

def main():
    with open(SRC, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows: fail("CSV is empty")
    if [c for c in REQ if c not in (rows[0].keys() or [])]:
        fail(f"missing columns, need: {','.join(REQ)}")
    seen, out = set(), []
    for i, r in enumerate(rows, start=2):
        if r["id"] in seen: fail(f"row {i}: duplicate id {r['id']}")
        seen.add(r["id"])
        try:
            price = int(float(r["price_inr"])); beds = int(r["beds"]); fl = int(r["floor"])
            tfl = int(r["total_floors"]); lat = float(r["lat"]); lng = float(r["lng"])
        except ValueError:
            fail(f"row {i} ({r['id']}): price/beds/floor/total_floors/lat/lng must be numeric")
        if r["property_type"] not in ("apartment", "house"): fail(f"row {i}: property_type must be apartment|house")
        if r["seller_type"] not in ("owner", "agent", "unknown"): fail(f"row {i}: seller_type must be owner|agent|unknown")
        if not (17.2 < lat < 17.6 and 78.1 < lng < 78.6): fail(f"row {i}: lat/lng outside Hyderabad box")
        if not (1 <= fl <= tfl): fail(f"row {i}: floor must be 1..total_floors")
        if "example.com" in r["url"]: print(f"warn row {i}: placeholder url still present")
        out.append({"id": r["id"], "title": r["title"], "price": price, "beds": beds, "floor": fl,
                    "total_floors": tfl, "property_type": r["property_type"], "lat": lat, "lng": lng,
                    "area": r["area"], "url": r["url"], "seller": r["seller"],
                    "seller_type": r["seller_type"], "posted": r["posted"],
                    "description": r["description"], "source": "curated"})
    with open(DST, "w") as f:
        json.dump(out, f, indent=2)
    print(f"OK: {len(out)} listings → data/listings_hyd.json")

if __name__ == "__main__":
    main()
