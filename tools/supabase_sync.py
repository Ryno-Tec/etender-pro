#!/usr/bin/env python3
"""
Sync eTender Pro into the Supabase tender tracker shown on tenders.ryno-tec.co.za.

Runs in the GitHub Action after each data refresh, so no Claude session is needed.
It uses the same rules as tools/hub_sync.py:
  - Ryno-Tec ("rtc"): open IT tenders with an online briefing (data/tenders.json).
  - Lesego Laka Projects ("llp"): open tenders in Gauteng (data/tenders-llp.json).
New tenders are added with status "New". Tenders already in the tracker get fresh feed details
(dates, briefing, links) but their status and notes are never touched.
Then it deletes tenders nobody worked on (status "New", no notes) that closed more than
RETENTION_DAYS days ago (see purge_expired_tenders in supabase/schema.sql).

Usage:
    SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_KEY=... python3 tools/supabase_sync.py data/tenders.json

If either variable is missing it prints a notice and exits 0, so the Action keeps working
before Supabase is set up.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hub_sync import in_gauteng, to_hub  # noqa: E402  (same mapping and Gauteng rule as the hub)

RETENTION_DAYS = 7
COLUMNS = {  # hub field -> Supabase column
    "title": "title", "tenderNo": "tender_no", "buyer": "buyer", "province": "province", "category": "category",
    "value": "value", "closing": "closing", "briefing": "briefing", "compulsory": "compulsory",
    "briefingAt": "briefing_at", "briefingVenue": "briefing_venue", "briefingLink": "briefing_link",
    "sourceUrl": "source_url", "docsUrl": "docs_url",
}
TIMES = ("closing", "briefing_at")


def api(base, key, method, path, body=None, prefer=None):
    req = urllib.request.Request(base.rstrip("/") + "/rest/v1/" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("apikey", key)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"Supabase {method} {path.split('?')[0]} failed: HTTP {e.code} {e.read()[:300]!r}")
    return json.loads(raw) if raw else None


def row(company, t):
    hub = to_hub(t)
    r = {"ocid": hub["ocid"], "company": company}
    for k, col in COLUMNS.items():
        v = hub[k]
        if col in TIMES:
            v = v + ":00+02:00" if v else None  # hub times are South African local time
        r[col] = v
    return r


def main():
    base, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not base or not key:
        print("Supabase tracker not set up (SUPABASE_URL / SUPABASE_SERVICE_KEY secrets missing): skipping.")
        return
    feed_path = sys.argv[1] if len(sys.argv) > 1 else "data/tenders.json"
    with open(feed_path, encoding="utf-8") as f:
        feed = json.load(f)
    llp_path = os.path.join(os.path.dirname(feed_path) or ".", "tenders-llp.json")
    llp_feed = json.load(open(llp_path, encoding="utf-8")) if os.path.exists(llp_path) else {}

    existing = set()
    offset = 0
    while True:
        page = api(base, key, "GET", f"tenders?select=ocid&order=ocid&limit=1000&offset={offset}")
        existing.update(r["ocid"] for r in page)
        if len(page) < 1000:
            break
        offset += 1000

    now = datetime.now(timezone.utc)
    rows, added, updated = {}, {"rtc": 0, "llp": 0}, {"rtc": 0, "llp": 0}
    items = [("rtc", t) for t in feed.get("tenders", [])] + [("llp", t) for t in llp_feed.get("tenders", [])]
    for company, t in items:
        try:
            if t.get("closingDate") and datetime.fromisoformat(t["closingDate"].replace("Z", "+00:00")) < now:
                continue
        except ValueError:
            pass
        ocid = str(t.get("id") or "")
        if not ocid or ocid in rows:
            continue
        wanted = bool((t.get("briefing") or {}).get("online")) if company == "rtc" else in_gauteng(t)
        if ocid in existing:
            rows[ocid] = row(company, t)
            updated[company] += 1
        elif wanted:
            rows[ocid] = row(company, t)
            added[company] += 1

    stamp = now.isoformat()
    batch = [{**r, "updated_at": stamp} for r in rows.values()]
    for i in range(0, len(batch), 500):
        # merge-duplicates updates only the columns sent, so status, notes and created_at are kept
        api(base, key, "POST", "tenders?on_conflict=ocid", batch[i:i + 500],
            prefer="resolution=merge-duplicates,return=minimal")
    removed = api(base, key, "POST", "rpc/purge_expired_tenders", {"days": RETENTION_DAYS})
    print(f"Supabase tracker: Ryno-Tec {added['rtc']} added, {updated['rtc']} refreshed; "
          f"Lesego Laka {added['llp']} added, {updated['llp']} refreshed; "
          f"{removed} untouched tenders closed more than {RETENTION_DAYS} days ago deleted.")


if __name__ == "__main__":
    main()
