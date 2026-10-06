#!/usr/bin/env python3
"""One-time: load the existing data/*.csv snapshots into the local SQLite warehouse
(data/hdhq.sqlite) that the dashboard reads. Later syncs upsert into it automatically."""
import sys, time
sys.argv = [sys.argv[0]]
import sync
t = time.time()
for tbl in sync.ALL_TABLES:
    n = sync.upsert_sqlite(tbl, sync.read_csv(tbl))
    print(f"{tbl:20s} {n:7d} rows  ({time.time()-t:.0f}s)")
print("warehouse:", sync.WAREHOUSE)
