#!/usr/bin/env python3
"""Smoke test: push 5 fake rows into a throwaway Zoho Analytics table.

Verifies the whole path end-to-end:
    refresh token -> access token -> bulk import -> table populated.

Usage
-----
    python test_load.py

Behaviour
---------
* If ``ZOHO_TEST_VIEW_ID`` is set, the rows are loaded into that existing table
  via ``load_table`` with importType=updateadd (matched on ``id``).
* Otherwise it creates/replaces a throwaway table named ``ZOHO_TEST_TABLE_NAME``
  (default: ``zz_sync_smoke_test``) via ``create_table``.

Nothing here touches real MoeGo data — the rows are hard-coded fakes.
"""

import json
import os
import sys

from dotenv import load_dotenv

import zoho_loader

load_dotenv()

FAKE_ROWS = [
    {"id": 1, "pet_name": "Rex",     "breed": "Labrador",      "weight_lb": 72.5, "checked_in": "true"},
    {"id": 2, "pet_name": "Biscuit", "breed": "Poodle",        "weight_lb": 18.0, "checked_in": "false"},
    {"id": 3, "pet_name": "Momo",    "breed": "Shiba Inu",     "weight_lb": 21.3, "checked_in": "true"},
    {"id": 4, "pet_name": "Clover",  "breed": "Border Collie", "weight_lb": 39.9, "checked_in": "false"},
    {"id": 5, "pet_name": "Waffles", "breed": "Corgi",         "weight_lb": 27.1, "checked_in": "true"},
]


def main() -> int:
    print("1/2  Refreshing access token ...")
    try:
        token = zoho_loader.get_access_token(force_refresh=True)
    except Exception as exc:  # noqa: BLE001 - surface any config/HTTP issue plainly
        print(f"      FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"      OK (token ...{token[-6:]})")

    view_id = os.getenv("ZOHO_TEST_VIEW_ID")
    table_name = os.getenv("ZOHO_TEST_TABLE_NAME", "zz_sync_smoke_test")

    try:
        if view_id:
            print(f"2/2  Loading 5 rows into existing view {view_id} (updateadd on 'id') ...")
            result = zoho_loader.load_table(
                view_id,
                FAKE_ROWS,
                import_type="updateadd",
                matching_columns=["id"],
            )
        else:
            print(f"2/2  Creating/loading throwaway table '{table_name}' with 5 rows ...")
            result = zoho_loader.create_table(table_name, FAKE_ROWS)
    except Exception as exc:  # noqa: BLE001
        print(f"      FAILED: {exc}", file=sys.stderr)
        return 1

    print("      OK. Response:")
    print(json.dumps(result, indent=2)[:2000])
    print("\nDone. Check the table in Zoho Analytics to confirm the 5 rows landed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
