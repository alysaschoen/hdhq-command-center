#!/usr/bin/env python3
"""Quick MoeGo API probe: shows raw status + body for a few calls so we can see
exactly what the API accepts.   python probe.py [LOCATION_KEY]"""
import csv, json, os, sys
from pathlib import Path
import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
KEY = os.environ["MOEGO_API_KEY_B64"].strip()
H = {"Authorization": f"Basic {KEY}", "Content-Type": "application/json", "Accept": "application/json"}
BASE = "https://openapi.moego.pet"

key = sys.argv[1] if len(sys.argv) > 1 else "OMAHA"
loc = next(r for r in csv.DictReader(open(HERE / "locations.csv")) if r["location_key"] == key)
cid, bid = loc["company_id"], loc["business_id"]


def call(name, path, body):
    r = requests.post(BASE + path, headers=H, json=body, timeout=60)
    print(f"\n## {name}  ->  HTTP {r.status_code}")
    txt = r.text
    try:
        d = r.json()
        for k, v in d.items():
            if isinstance(v, list):
                print(f"   {k}: {len(v)} items; first = {json.dumps(v[0])[:400] if v else '[]'}")
            else:
                print(f"   {k}: {str(v)[:200]}")
    except ValueError:
        print("   " + txt[:400])


r = requests.post(BASE + "/v1/companies:list", headers=H, json={"pagination": {"pageSize": 100, "pageToken": "1"}}, timeout=60).json()
print("## companies:list ->", len(r.get("companies", [])), "companies")
known = {x["company_id"]: x["location_key"] for x in csv.DictReader(open(HERE / "locations.csv"))}
for c in r.get("companies", []):
    print(f"   {c['id']:9} {c.get('name','')[:32]:32} tz={(c.get('timezone') or {}).get('id','')}  sheet={known.get(c['id'], '** NOT IN locations.csv **')}")
api_ids = {c["id"] for c in r.get("companies", [])}
print("   in locations.csv but NOT returned by API:", [k for i, k in known.items() if i not in api_ids])

P1 = {"pageSize": 5, "pageToken": "1"}
WIN = {"startTime": "2026-09-15T00:00:00Z", "endTime": "2026-09-22T23:59:59Z"}
call(f"staffs:list {cid}", "/v1/staffs:list", {"companyId": cid, "pagination": P1})
call(f"services:list {cid}", f"/v1/setting/companies/{cid}/services:list", {"companyId": cid, "pagination": P1})
call(f"customers:list {cid} (no filter)", "/v1/customers:list", {"companyId": cid, "pagination": P1})
call(f"customers:list {cid} lastUpdated 7d", "/v1/customers:list", {"companyId": cid, "pagination": P1, "filter": {"lastUpdatedTime": WIN}})
call(f"pets:list {cid} lastUpdated 7d", "/v1/pets:list", {"companyId": cid, "pagination": P1, "filter": {"lastUpdatedTime": WIN}})
call(f"appointments:list {cid}/{bid} (no filter)", "/v1/appointments:list", {"companyId": cid, "businessIds": [bid], "pagination": P1})
call(f"appointments:list {cid}/{bid} startTime 7d", "/v1/appointments:list", {"companyId": cid, "businessIds": [bid], "pagination": P1, "filter": {"startTime": WIN}})
call(f"appointments:list {cid}/{bid} lastUpdated 7d", "/v1/appointments:list", {"companyId": cid, "businessIds": [bid], "pagination": P1, "filter": {"lastUpdatedTime": WIN}})
call(f"appointments:list {cid} no businessIds, startTime 7d", "/v1/appointments:list", {"companyId": cid, "pagination": P1, "filter": {"startTime": WIN}})
call(f"orders:list {cid}/{bid} (no filter)", "/v1/orders:list", {"companyId": cid, "businessIds": [bid], "pagination": P1})
call(f"orders:list {cid}/{bid} lastUpdated 7d", "/v1/orders:list", {"companyId": cid, "businessIds": [bid], "pagination": P1, "filter": {"lastUpdatedTime": WIN}})
