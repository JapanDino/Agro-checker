"""Explain a frozen weather issue and its preparation window without later observations."""

from datetime import timedelta

from .field_store import stamp, utc_now


def forecast_brief(forecast, now=None):
    from .forecast_quality import model_quality
    now = now or utc_now(); p = forecast["profile_snapshot"]; policy = p["decision"]
    zone = next(z for z in p["zones"] if z["id"] == forecast["zone_id"])
    cold = forecast["weather_frost"]; start = stamp(forecast["hours"][0]["valid_at_utc"])
    end = stamp(forecast["hours"][-1]["valid_at_utc"])+timedelta(hours=1)
    bracket = cold["onset_bracket_at_utc"]
    lead = policy["required_lead_hours"]
    # Use the earlier edge of the forecast's crossing bracket, not its cold sample's timestamp.
    # A left-censored episode has no known earlier edge; inventing a deadline would imply precision.
    deadline = stamp(bracket[0])-timedelta(hours=lead) if bracket and lead is not None else None
    reasons = []
    if policy["review_status"] != "reviewed": reasons.append("protocol_unreviewed")
    if lead is None: reasons.append("lead_unknown")
    if not zone.get("phase_recorded_at_utc") or not zone.get("phase"): reasons.append("phase_unknown")
    if cold["left_censored"]: reasons.append("onset_left_censored")
    if deadline and stamp(forecast["issued_at_utc"]) > deadline: reasons.append("issue_after_prepare_deadline")
    return {"weather_event": cold["event"], "threshold_c": policy["weather_threshold_c"],
            "window_start_at_utc": start.isoformat(), "window_end_at_utc": end.isoformat(),
            "window_state": "upcoming" if now < start else "in_progress" if now < end else "past",
            "episodes": cold["episodes"], "cold_bins_hours": cold["cold_bins_hours"],
            "onset_bracket_at_utc": bracket, "first_cold_sample_at_utc": cold["first_cold_sample_at_utc"],
            "prepare_by_utc": deadline.isoformat() if deadline else None,
            "prepare_deadline_basis": "earlier edge of hourly onset bracket" if deadline else None,
            "required_lead_hours": lead,
            "lead_sufficient_at_issue": stamp(forecast["issued_at_utc"]) <= deadline if deadline else None,
            "prepare_window_state": "open" if deadline and now <= deadline else "elapsed" if deadline else "unknown",
            "phase": zone.get("phase"), "phase_recorded_at_utc": zone.get("phase_recorded_at_utc"),
            "agreed_protocol": policy.get("agreed_protocol") if policy["review_status"] == "reviewed" else None,
            "limitations": reasons, "crop_damage_probability": None, "shadow_only": True,
            "model_quality": model_quality(forecast),
            "evidence": "frozen issue only; no later measurements, journal decisions or model refit"}
