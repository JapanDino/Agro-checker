"""Self-reported usability evidence; separate from operational decisions and field metrics."""

import hashlib

from .field_store import digest, identifier, numeric, stamp, utc_now
from .pipeline import ROOT

SOURCES = {"user_test", "engineering_check"}
ROLES = {"agronomist", "farm_manager", "operator", "other"}
COMPLETION = {"without_help", "with_help", "not_completed"}
DATA_MEANINGS = {"demo_or_station", "my_orchard", "unsure"}


def save_feedback(store, profile_id, raw, now=None):
    now = now or utc_now()
    fore = next((f for f in store.records("forecast", profile_id) if f["id"] == raw.get("forecast_id")), None)
    if not fore or fore["payload"]["mode"] != "demo":
        raise ValueError("Проверку прототипа проводите по выдаче отдельного демонстрационного паспорта")
    if now < stamp(fore["payload"]["issued_at_utc"]): raise ValueError("Выдача ещё не доступна")
    source, role, completion = [raw.get(k) for k in ["source", "role", "completion"]]
    if source not in SOURCES or role not in ROLES or completion not in COMPLETION:
        raise ValueError("Укажите тип проверки, роль участника и результат прохождения")
    code = identifier(str(raw.get("participant_code", "")).strip())
    meaning = raw.get("data_meaning")
    if meaning not in DATA_MEANINGS: raise ValueError("Запишите, как участник понял происхождение данных")
    notes = str(raw.get("notes", "")).strip()
    if not 5 <= len(notes) <= 3000: raise ValueError("Опишите затруднение или наблюдение: 5–3000 символов")
    duration = numeric(raw.get("duration_seconds"), 0, 86400, "Время прохождения, секунд", True)
    if duration is not None and (duration <= 0 or int(duration) != duration):
        raise ValueError("Укажите положительное целое число секунд либо оставьте время неизвестным")
    iteration = raw.get("iteration")
    if iteration not in {"first", "repeat"}: raise ValueError("Укажите первый или повторный тест")
    previous_id = raw.get("previous_feedback_id") or None
    previous = next((r for r in store.records("prototype_feedback", profile_id) if r["id"] == previous_id), None)
    if iteration == "first" and previous_id: raise ValueError("Первый тест не ссылается на предыдущий")
    if iteration == "repeat" and (not previous or any(previous["payload"][k] != v for k, v in
        [("participant_code", code), ("forecast_id", fore["id"]), ("source", source)])):
        raise ValueError("Повторный тест должен ссылаться на проверку того же участника и той же выдачи")
    if previous and now < stamp(previous["recorded_at_utc"]): raise ValueError("Повторный тест не может предшествовать исходному")
    build = {f: hashlib.sha256((ROOT/"web/field"/f).read_bytes()).hexdigest()
             for f in ["index.html", "app.js", "journey.js", "styles.css"]}
    return store.append("prototype_feedback", profile_id, {
        "forecast_id": fore["id"], "participant_code": code, "source": source, "role": role,
        "completion": completion, "data_meaning": meaning, "duration_seconds": duration,
        "duration_source": "observer_reported" if duration is not None else "unknown",
        "notes": notes, "iteration": iteration, "previous_feedback_id": previous_id,
        "protocol_version": "journey_review_v1", "server_ui_sha256": build,
        "forecast_sha256": digest(fore["payload"]),
        "version_note": "Server UI files at save time; reload app before each test. Not client version attestation.",
        "mode": "demo", "evidence": "self-reported prototype feedback; not verified interviews or field effectiveness"}, now)


def feedback_export(store):
    records = store.records("prototype_feedback")
    users = [r for r in records if r["payload"]["source"] == "user_test"]
    return {"records": records, "summary": {
        "user_test_records": len(users),
        "reported_participant_codes": len({r["payload"]["participant_code"] for r in users}),
        "engineering_records": sum(r["payload"]["source"] == "engineering_check" for r in records),
        "repeat_records": sum(r["payload"]["iteration"] == "repeat" for r in users)},
        "note": "Counts describe entered records and participant codes, not independently verified people, interviews, demand or crop savings. Repeats and engineering checks are separate; failures are retained."}
