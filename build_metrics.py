#!/usr/bin/env python3
"""Create/refresh the metric query tables in HDHQ Operations that the
leadership dashboards are built on.  Safe to re-run (updates in place).

    python build_metrics.py            # create/update all
    python build_metrics.py QT_Revenue_Monthly
"""
import json, sys, urllib.parse, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))
import zoho_loader as zl
import zoho_workspace as zw
from sync import WORKSPACE_NAME

# Revenue definition (leadership): cash basis = payments collected minus refunds.
QUERIES = {
"QT_Revenue_Monthly": """
SELECT p.location_key AS location_key, p.location_name AS location_name, l.region AS region, l.state AS state,
       DATE_FORMAT(p.payment_date, '%Y-%m') AS month, p.payment_date AS day,
       SUM(p.amount) AS gross_collected,
       SUM(p.refund_amount) AS refunds,
       SUM(p.amount) - SUM(p.refund_amount) AS net_revenue,
       SUM(p.processing_fee) AS processing_fees,
       COUNT(p.id) AS payments,
       COUNT(DISTINCT p.customer_id) AS paying_customers
FROM "Payments" p LEFT JOIN "Locations" l ON l.location_key = p.location_key
WHERE p.status IN ('PAID','COMPLETED') AND p.payment_date >= '2022-01-01' AND p.payment_date <= '2027-12-31'
GROUP BY p.location_key, p.location_name, l.region, l.state, DATE_FORMAT(p.payment_date, '%Y-%m'), p.payment_date
""",
"QT_Appointments_Daily": """
SELECT a.location_key AS location_key, a.location_name AS location_name, l.region AS region, l.state AS state,
       a.appointment_date AS day, DATE_FORMAT(a.appointment_date, '%Y-%m') AS month,
       COUNT(a.id) AS appointments,
       SUM(CASE WHEN a.status = 'FINISHED' THEN 1 ELSE 0 END) AS finished,
       SUM(CASE WHEN a.status = 'CANCELED' THEN 1 ELSE 0 END) AS canceled,
       SUM(CASE WHEN a.no_show = true THEN 1 ELSE 0 END) AS no_shows,
       SUM(CASE WHEN a.status IN ('CONFIRMED','UNCONFIRMED','CHECKED_IN','READY') THEN 1 ELSE 0 END) AS upcoming_or_open,
       COUNT(DISTINCT a.customer_id) AS unique_customers,
       SUM(a.pet_count) AS pets_served,
       SUM(CASE WHEN a.status = 'FINISHED' THEN a.total_amount ELSE 0 END) AS finished_ticket_value,
       SUM(CASE WHEN a.status = 'FINISHED' THEN a.scheduled_minutes ELSE 0 END) AS finished_scheduled_minutes,
       SUM(CASE WHEN a.status = 'FINISHED' THEN a.actual_minutes ELSE 0 END) AS finished_actual_minutes
FROM "Appointments" a LEFT JOIN "Locations" l ON l.location_key = a.location_key
WHERE a.is_deleted = false AND a.appointment_date >= '2022-01-01' AND a.appointment_date <= '2027-12-31'
GROUP BY a.location_key, a.location_name, l.region, l.state, a.appointment_date
""",
"QT_New_Customers_Monthly": """
SELECT c.location_key AS location_key, c.location_name AS location_name, l.region AS region,
       DATE_FORMAT(c.created_date, '%Y-%m') AS month, c.created_date AS day,
       COUNT(c.id) AS new_customers,
       SUM(CASE WHEN c.email <> '' THEN 1 ELSE 0 END) AS with_email
FROM "Customers" c LEFT JOIN "Locations" l ON l.location_key = c.location_key
WHERE c.is_deleted = false AND c.created_date >= '2015-01-01' AND c.created_date <= '2027-12-31'
GROUP BY c.location_key, c.location_name, l.region, DATE_FORMAT(c.created_date, '%Y-%m'), c.created_date
""",
"QT_Customer_Activity": """
SELECT c.location_key AS location_key, c.location_name AS location_name, c.id AS customer_id, c.full_name AS customer_name,
       c.phone AS phone, c.email AS email, c.source AS source, c.referral_source AS referral_source, c.status AS status, c.created_date AS created_date,
       c.last_appointment_utc AS last_appointment_utc, c.next_appointment_utc AS next_appointment_utc,
       DATEDIFF(NOW(), c.last_appointment_utc) AS days_since_last_visit,
       CASE WHEN c.next_appointment_utc IS NOT NULL AND c.next_appointment_utc > NOW() THEN 'Booked'
            WHEN c.last_appointment_utc IS NULL THEN 'Never visited'
            WHEN DATEDIFF(NOW(), c.last_appointment_utc) <= 60 THEN 'Active (0-60d)'
            WHEN DATEDIFF(NOW(), c.last_appointment_utc) <= 120 THEN 'Cooling (61-120d)'
            ELSE 'Lapsed (120d+)' END AS activity_bucket
FROM "Customers" c
WHERE c.is_deleted = false
""",
"QT_Customer_Visits": """
SELECT a.location_key AS location_key, a.location_name AS location_name, a.customer_id AS customer_id, a.customer_name AS customer_name,
       COUNT(a.id) AS finished_visits,
       MIN(a.appointment_date) AS first_visit, MAX(a.appointment_date) AS last_visit,
       SUM(a.total_amount) AS lifetime_ticket_value,
       CASE WHEN COUNT(a.id) = 1 THEN 'One-time' WHEN COUNT(a.id) <= 3 THEN '2-3 visits'
            WHEN COUNT(a.id) <= 8 THEN '4-8 visits' ELSE '9+ visits' END AS frequency_bucket
FROM "Appointments" a
WHERE a.status = 'FINISHED' AND a.is_deleted = false
GROUP BY a.location_key, a.location_name, a.customer_id, a.customer_name
""",
"QT_Groomer_Monthly": """
SELECT s.location_key AS location_key, s.location_name AS location_name, s.staff_id AS staff_id, s.staff_name AS staff_name,
       DATE_FORMAT(s.appointment_date, '%Y-%m') AS month, s.appointment_date AS day,
       COUNT(DISTINCT s.appointment_id) AS appointments,
       COUNT(s.id) AS services,
       COUNT(DISTINCT s.pet_id) AS pets,
       SUM(CASE WHEN s.appointment_status = 'FINISHED' THEN s.price ELSE 0 END) AS finished_service_value,
       SUM(CASE WHEN s.appointment_status = 'FINISHED' THEN s.service_minutes ELSE 0 END) AS finished_service_minutes,
       SUM(CASE WHEN s.appointment_status = 'CANCELED' THEN 1 ELSE 0 END) AS canceled_services
FROM "AppointmentServices" s
WHERE s.staff_name <> '' AND s.staff_name IS NOT NULL AND s.appointment_date >= '2022-01-01' AND s.appointment_date <= '2027-12-31'
GROUP BY s.location_key, s.location_name, s.staff_id, s.staff_name, DATE_FORMAT(s.appointment_date, '%Y-%m'), s.appointment_date
""",
"QT_Groomer_Weekday": """
SELECT a.location_key AS location_key, a.location_name AS location_name, a.primary_staff_name AS staff_name,
       DAYNAME(a.start_local) AS weekday, DAYOFWEEK(a.start_local) AS weekday_num,
       HOUR(a.start_local) AS start_hour,
       COUNT(a.id) AS appointments,
       SUM(CASE WHEN a.status = 'FINISHED' THEN 1 ELSE 0 END) AS finished,
       AVG(CASE WHEN a.status = 'FINISHED' THEN a.actual_minutes END) AS avg_actual_minutes,
       AVG(CASE WHEN a.status = 'FINISHED' THEN a.scheduled_minutes END) AS avg_scheduled_minutes
FROM "Appointments" a
WHERE a.is_deleted = false AND a.primary_staff_name <> '' AND a.appointment_date >= '2022-01-01' AND a.appointment_date <= '2027-12-31'
GROUP BY a.location_key, a.location_name, a.primary_staff_name, DAYNAME(a.start_local), DAYOFWEEK(a.start_local), HOUR(a.start_local)
""",
"QT_Service_Mix": """
SELECT s.location_key AS location_key, s.location_name AS location_name, DATE_FORMAT(s.appointment_date, '%Y-%m') AS month,
       s.service_name AS service_name, s.service_category AS service_category, s.service_item_type AS service_item_type,
       COUNT(s.id) AS services_sold,
       SUM(CASE WHEN s.appointment_status = 'FINISHED' THEN s.price ELSE 0 END) AS finished_value,
       AVG(s.price) AS avg_price
FROM "AppointmentServices" s
WHERE s.appointment_date >= '2022-01-01' AND s.appointment_date <= '2027-12-31'
GROUP BY s.location_key, s.location_name, DATE_FORMAT(s.appointment_date, '%Y-%m'), s.service_name, s.service_category, s.service_item_type
""",
}


def create_or_update(ws, name, sql, views):
    cfg = {"sqlQuery": sql.strip(), "queryTableName": name,
           "description": "HDHQ metric layer (auto-managed by build_metrics.py)"}
    if name in views:
        url = f"{zl.ANALYTICS_URL}/restapi/v2/workspaces/{ws}/querytables/{views[name]}?" + \
              urllib.parse.urlencode({"CONFIG": json.dumps({"sqlQuery": sql.strip()})})
        r = requests.put(url, headers=zl._headers(), timeout=120)
        action = "updated"
    else:
        url = f"{zl.ANALYTICS_URL}/restapi/v2/workspaces/{ws}/querytables?" + \
              urllib.parse.urlencode({"CONFIG": json.dumps(cfg)})
        r = requests.post(url, headers=zl._headers(), timeout=120)
        action = "created"
    if r.status_code not in (200, 201, 204):
        print(f"  FAILED {name}: HTTP {r.status_code} {r.text[:400]}")
        return False
    print(f"  {action} {name}")
    return True


if __name__ == "__main__":
    ws = zw.ensure_workspace(WORKSPACE_NAME)
    data = zw._get(f"/workspaces/{ws}/views").get("data", {})
    views = {v["viewName"]: str(v["viewId"]) for v in data.get("views", [])}
    names = sys.argv[1:] or list(QUERIES)
    ok = all([create_or_update(ws, n, QUERIES[n], views) for n in names])
    sys.exit(0 if ok else 1)
