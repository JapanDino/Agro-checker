"""Read-only QC of a prospective inspection round; never imports or trains."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math


def pool_hash(candidates):
    """Digest of the complete ordered pool, not only the selected places."""
    return sha256(json.dumps(candidates, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed


def check_collection(round_data, result_data, window_hours=48):
    """Technical eligibility is provisional, not certification of field facts."""
    errors, warnings = [], []
    report = dict(status="no_data", errors=errors, warnings=warnings,
                  round_count=0, assignment_count=0, result_count=0,
                  provisional_labels={}, unknown_visits=[],
                  system_precision_at_k=None, routine_precision_at_k=None,
                  precision_difference=None, completion=None,
                  control_completion=None, control_useful=None,
                  training_allowed=False, automatic_admission=False)
    results = result_data.get("results", [])
    if not isinstance(results, list):
        errors.append("results must be a list")
        report["status"] = "invalid"
        return report
    if not round_data.get("round_id"):
        if results or any(round_data.get(k) for k in
                          ("candidates", "assignments", "system_top_k", "routine_top_k", "selected_controls")):
            errors.append("data without round_id")
        report["status"] = "invalid" if errors else "no_data"
        return report
    report["round_count"] = 1

    def timestamp(obj, key, context, required=True):
        if not required and obj.get(key) in (None, ""):
            return None
        try:
            return _time(obj[key])
        except (KeyError, ValueError, TypeError, AttributeError):
            errors.append(f"{context}: invalid {key}; ISO8601 with timezone required")
            return None

    decision = timestamp(round_data, "decision_at_utc", "round")
    snapshot = timestamp(round_data, "snapshot_saved_at_utc", "round")
    routine_saved = timestamp(round_data, "routine_list_saved_at_utc", "round")
    if decision and any(t and t > decision for t in (snapshot, routine_saved)):
        errors.append("round snapshot/routine list saved after decision cutoff")
    if round_data.get("routine_list_before_system_seen") is not True:
        errors.append("routine list was not independently frozen")
    if not round_data.get("rule_version"):
        errors.append("missing rule_version")
    if not isinstance(window_hours, (int, float)) or not math.isfinite(window_hours) or window_hours <= 0:
        errors.append("window_hours must be finite and positive")
        window_hours = 48
    candidates = round_data.get("candidates", [])
    assignments = round_data.get("assignments", [])
    sources = round_data.get("sources", [])
    if not all(isinstance(v, list) for v in (candidates, assignments, sources)):
        errors.append("candidates/assignments/sources must be lists")
        report["status"] = "invalid"
        return report

    def index(rows, key, context):
        indexed = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
                errors.append(f"{context}: missing {key}")
                continue
            if row[key] in indexed:
                errors.append(f"{context}: duplicate {key} {row[key]}")
            indexed[row[key]] = row
        return indexed

    pool = index(candidates, "place_id", "pool")
    visits = index(assignments, "visit_id", "assignments")
    observed = index(results, "visit_id", "results")
    source_map = index(sources, "source_id", "sources")
    report.update(assignment_count=len(assignments), result_count=len(results))
    if not pool or not visits:
        errors.append("nonempty candidate pool and assignments required")
    if round_data.get("pool_sha256") != pool_hash(candidates):
        errors.append("pool_sha256 mismatch")
    for sid, source in source_map.items():
        available = timestamp(source, "available_at_utc", sid)
        acquired = timestamp(source, "acquired_or_issued_at_utc", sid)
        if decision and any(t and t > decision for t in (available, acquired)):
            errors.append(f"{sid}: future input / availability leakage")
        digest = source.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            errors.append(f"{sid}: missing/invalid sha256")
    for pid, candidate in pool.items():
        if not candidate.get("field_id") or not candidate.get("geometry_version"):
            errors.append(f"{pid}: missing field or geometry version")
        if candidate.get("boundary_status") not in ("confirmed", "approximate_research"):
            errors.append(f"{pid}: explicit boundary_status required")
        if candidate.get("boundary_status") == "approximate_research":
            warnings.append(f"{pid}: approximate boundary, not a confirmed farm field")
        if candidate.get("eligible") is not True and not candidate.get("exclusion_reason"):
            errors.append(f"{pid}: excluded candidate without reason")
        if not isinstance(candidate.get("source_ids"), list):
            errors.append(f"{pid}: source_ids list required (empty if no external inputs)")
        elif any(s not in source_map for s in candidate["source_ids"]):
            errors.append(f"{pid}: unknown source reference")

    lists = {}
    for key in ("system_top_k", "routine_top_k", "selected_controls"):
        value = round_data.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            errors.append(f"{key}: expected list of place IDs")
            value = []
        if len(set(value)) != len(value):
            errors.append(f"{key}: duplicate place")
        if any(p not in pool or pool[p].get("eligible") is not True for p in value):
            errors.append(f"{key}: place outside eligible pool")
        lists[key] = value
    system, routine, controls = (lists[k] for k in lists)
    union = set(system) | set(routine)
    remaining = {p for p, c in pool.items() if c.get("eligible") is True} - union
    if not system or len(system) != len(routine):
        errors.append("system/routine must have identical nonzero K")
    if not controls or union.intersection(controls):
        errors.append("random control required, separate from both top-K lists")
    if not isinstance(round_data.get("random_seed"), int) or isinstance(round_data.get("random_seed"), bool):
        errors.append("random_seed integer required")
    # Random selection itself must be generated and frozen before outcomes;
    # this checker validates design/probabilities, not the honesty of randomisation.
    assigned_places = {}
    for vid, assignment in visits.items():
        pid = assignment.get("place_id")
        if pid in assigned_places:
            errors.append(f"{vid}: same place assigned twice")
        assigned_places[pid] = vid
        expected = 1.0 if pid in union else len(controls) / len(remaining) if remaining and pid in controls else None
        probability = assignment.get("inclusion_probability")
        if (not isinstance(probability, (int, float)) or isinstance(probability, bool)
                or not math.isfinite(probability) or expected is None
                or not math.isclose(probability, expected, abs_tol=1e-9)):
            errors.append(f"{vid}: inclusion probability inconsistent with selection design")
    if set(assigned_places) != union | set(controls):
        errors.append("assignments must exactly cover union of lists and controls")
    for vid in observed.keys() - visits.keys():
        errors.append(f"{vid}: result without assignment")

    labels = report["provisional_labels"]
    for vid, assignment in visits.items():
        row = observed.get(vid)
        reasons = []
        if row is None:
            report["unknown_visits"].append({"visit_id": vid, "reasons": ["no_result"]})
            continue
        errors_before = len(errors)
        seen = timestamp(row, "observed_at_utc", vid,
                         required=row.get("completion") == "full")
        entered = timestamp(row, "recorded_at_utc", vid)
        label_at = timestamp(row, "label_available_at_utc", vid,
                             required=row.get("review_status") == "reviewed")
        if seen and entered and entered < seen or entered and label_at and label_at < entered:
            errors.append(f"{vid}: result/label time precedes observation or entry")
        if len(errors) > errors_before:
            reasons.append("invalid_time")
        if not decision or not seen or not (decision <= seen <= decision + timedelta(hours=window_hours)):
            reasons.append("outside_target_window")
        pid = assignment.get("place_id")
        if row.get("field_id") != pool.get(pid, {}).get("field_id"):
            reasons.append("field_mismatch")
        if row.get("position_match") is not True:
            reasons.append("position_unverified")
        lat, lon = row.get("actual_lat"), row.get("actual_lon")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            reasons.append("missing_or_invalid_position")
        if row.get("completion") != "full":
            reasons.append("not_full")
        if row.get("review_status") != "reviewed" or not row.get("reviewer_code"):
            reasons.append("not_reviewed")
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("observer_code", "notes", "usefulness_reason")):
            reasons.append("missing_manual_evidence")
        utility = row.get("usefulness")
        if utility == "useful" and row.get("new_information") is True and row.get("followup_required") is True:
            label = 1
        elif utility == "no_new_information" and row.get("new_information") is False and row.get("followup_required") is False:
            label = 0
        else:
            label = None
            reasons.append("uncertain_or_conflicting_assessment")
        if reasons:
            report["unknown_visits"].append({"visit_id": vid, "reasons": reasons})
        else:
            labels[vid] = label
    report["completion"] = len(labels) / len(visits) if visits else None
    control_labels = [labels.get(assigned_places.get(p)) for p in controls]
    report["control_completion"] = sum(v is not None for v in control_labels) / len(controls) if controls else None
    report["control_useful"] = sum(v for v in control_labels if v is not None) if any(v is not None for v in control_labels) else None
    # Invalid rounds never receive comparative scores, including invalid source cutoffs.
    if not errors:
        paired = [[labels.get(assigned_places.get(p)) for p in group] for group in (system, routine)]
        if all(v is not None for group in paired for v in group):
            report["system_precision_at_k"] = sum(paired[0]) / len(system)
            report["routine_precision_at_k"] = sum(paired[1]) / len(routine)
            report["precision_difference"] = report["system_precision_at_k"] - report["routine_precision_at_k"]
    else:
        report["provisional_labels"] = {}
    report["status"] = "invalid" if errors else "incomplete" if report["unknown_visits"] else "technically_checked_not_certified"
    return report
