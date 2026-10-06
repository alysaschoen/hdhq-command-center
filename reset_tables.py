#!/usr/bin/env python3
"""Delete the sync's tables from the HDHQ Operations workspace so the next
sync recreates them from scratch (use after a schema/type change).

    python reset_tables.py            # deletes all sync tables (asks y/N)
    python reset_tables.py Payments   # just one
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))
import zoho_workspace as zw
from sync import ALL_TABLES, WORKSPACE_NAME

names = sys.argv[1:] or ALL_TABLES + ["SyncLog"]
ws = zw.ensure_workspace(WORKSPACE_NAME)
views = zw.list_views(ws)
todo = [n for n in names if n in views]
if not todo:
    print("nothing to delete"); sys.exit(0)
print("Will DELETE these tables from", WORKSPACE_NAME, ":", ", ".join(todo))
if input("Type y to confirm: ").strip().lower() != "y":
    sys.exit(1)
for n in todo:
    zw.delete_table(ws, views[n]); print("deleted", n)
