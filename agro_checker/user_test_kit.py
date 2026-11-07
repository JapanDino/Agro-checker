"""Prepare separate local usability slots from synthetic fixtures, never field data."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3

from .field_store import FieldStore, digest, seed_demo, utc_now
from .pipeline import ROOT


def prepare_user_test(directory, now=None):
    now = now or utc_now()
    target = Path(directory).resolve()
    # Exclusive creation protects prior sessions, including empty existing directories.
    target.mkdir(parents=True, exist_ok=False)
    base = FieldStore(target / "fixtures").init()
    seed_demo(base, now)
    cases = []
    for kind in ["hit", "miss", "false_alarm"]:
        assessment = next(a for a in base.state(now)["assessments"] if a["outcome"] == kind)
        fore = next(f for f in base.records("forecast") if f["id"] == assessment["forecast_id"])
        cases.append({"task": len(cases)+1, "forecast_id": fore["id"],
                      "night_date_local": fore["payload"]["night_date_local"],
                      "forecast_sha256": digest(fore["payload"]), "facilitator_only_outcome": kind})
    slots = []
    for code in ["P01", "P02", "P03", "P04", "P05"]:
        state = target / code / "state"
        state.mkdir(parents=True)
        with base.connect() as source:
            with closing(sqlite3.connect(state / "field.sqlite3")) as destination:
                source.backup(destination)
        (target / code / "SESSION.md").write_text(
            (ROOT / "docs/user-testing/SESSION_TEMPLATE.md").read_text(encoding="utf-8"), encoding="utf-8")
        slots.append({"reserved_code": code, "status": "not_started", "state_dir": str(state),
                      "session_file": str(target / code / "SESSION.md")})
    for name in ["FACILITATOR.md", "PARTICIPANT_TASKS.md", "CHANGES_TEMPLATE.md"]:
        (target / name).write_text((ROOT / "docs/user-testing" / name).read_text(encoding="utf-8"), encoding="utf-8")
    manifest = {"prepared_at_utc": now.isoformat(), "protocol_version": "journey_review_v1",
                "actual_user_tests": 0, "slots": slots, "cases": cases,
                "server_ui_sha256": {name: hashlib.sha256((ROOT / "web/field" / name).read_bytes()).hexdigest()
                    for name in ["index.html", "app.js", "journey.js", "styles.css"]},
                "fixture_journal_ids": [r["id"] for r in base.records("journal")],
                "note": "Reserved slots are not participants. Synthetic fixture decisions remain labelled; "
                        "participant records are isolated. Keep each slot for repeats. Do not merge cloned fixtures. "
                        "UI hashes describe server files at preparation, not client attestation."}
    (target / "kit.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"directory": str(target), "reserved_slots": len(slots), "actual_user_tests": 0,
            "first_state_dir": slots[0]["state_dir"], "facilitator": str(target / "FACILITATOR.md")}
