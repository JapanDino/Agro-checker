"""Local append-only field pilot: profile, screened measurements, frozen issues and decision journal."""

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid

from .hourly_study import timed_frost
from .pipeline import ROOT, UTC


def utc_now(): return datetime.now(UTC)


def stamp(value):
    if not isinstance(value, str): raise ValueError("Укажите дату и время с часовым поясом")
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None: raise ValueError("Время должно содержать Z или смещение часового пояса")
    return d.astimezone(UTC)


def encoded(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value): return hashlib.sha256(encoded(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value): raise ValueError("Код должен содержать 1–64 латинских букв, цифр, _ или -")
    return value


def numeric(value, low, high, label, optional=False):
    if value in [None, ""] and optional: return None
    if isinstance(value, bool): raise ValueError(f"Некорректное число: {label}")
    try: v = float(value)
    except (ValueError, TypeError): raise ValueError(f"Некорректное число: {label}") from None
    if not math.isfinite(v) or not low <= v <= high: raise ValueError(f"{label}: допустимо {low}…{high}")
    return v


def validate_profile(raw):
    p = json.loads(encoded(raw))
    if p.get("schema_version") != 1 or p.get("mode") not in ["field", "demo"] or p.get("crop") != "apple" or p.get("timezone") != "Europe/Moscow":
        raise ValueError("Пилот поддерживает яблоню, Europe/Moscow и режим field/demo")
    identifier(p["id"])
    for key in ["name", "region", "agronomist"]:
        if not isinstance(p.get(key), str) or len(p[key]) > 250: raise ValueError("Некорректное описание хозяйства")
    if p["equipment_status"] not in ["unknown", "available", "none"] or not isinstance(p["equipment"], list): raise ValueError("Укажите состояние оборудования")
    if any(not isinstance(s, str) or len(s) > 200 for s in p["equipment"]): raise ValueError("Некорректное оборудование")
    d = p["decision"]
    d["weather_threshold_c"] = numeric(d["weather_threshold_c"], -10, 5, "Погодный порог")
    d["required_lead_hours"] = numeric(d.get("required_lead_hours"), 0, 48, "Заблаговременность", True)
    d["false_alarm_budget_per_month"] = numeric(d.get("false_alarm_budget_per_month"), 0, 31, "Допустимые ложные тревоги", True)
    if d["review_status"] not in ["unreviewed", "reviewed"]: raise ValueError("Некорректный статус регламента")
    if d["review_status"] == "reviewed" and (not d.get("reviewed_by") or not d.get("agreed_protocol") or not d.get("reviewed_at_utc")):
        raise ValueError("Для отметки согласования нужны агроном, дата и текст регламента")
    if d.get("reviewed_at_utc"): stamp(d["reviewed_at_utc"])
    zones, sensors = p["zones"], p["sensors"]
    if not 1 <= len(zones) <= 20 or len(sensors) > 100: raise ValueError("Нужны 1–20 участков и не более 100 датчиков")
    zone_ids, sensor_ids = set(), set()
    for z in zones:
        identifier(z["id"])
        if z["id"] in zone_ids: raise ValueError("Коды участков должны быть уникальными")
        zone_ids.add(z["id"])
        z["latitude"] = numeric(z.get("latitude"), -90, 90, "Широта", True)
        z["longitude"] = numeric(z.get("longitude"), -180, 180, "Долгота", True)
        if (z["latitude"] is None) != (z["longitude"] is None): raise ValueError("Укажите обе координаты или оставьте обе пустыми")
        z["area_ha"] = numeric(z.get("area_ha"), .001, 100000, "Площадь", True)
        z["elevation_m"] = numeric(z.get("elevation_m"), -500, 9000, "Высота участка", True)
        if z.get("phase_recorded_at_utc"): stamp(z["phase_recorded_at_utc"])
        geometry = z.get("geometry")
        if geometry is not None:
            if geometry.get("type") != "Polygon" or not isinstance(geometry.get("coordinates"), list) or not 1 <= len(geometry["coordinates"]) <= 10:
                raise ValueError("Контур должен быть GeoJSON Polygon")
            for ring in geometry["coordinates"]:
                if not 4 <= len(ring) <= 500 or ring[0] != ring[-1]: raise ValueError("Кольцо контура должно быть замкнуто, 4–500 точек")
                for point in ring:
                    if len(point) != 2: raise ValueError("Контур задаётся парами longitude,latitude")
                    numeric(point[0], -180, 180, "Долгота контура"); numeric(point[1], -90, 90, "Широта контура")
    for s in sensors:
        identifier(s["id"])
        if s["id"] in sensor_ids or s["zone_id"] not in zone_ids: raise ValueError("Проверьте код датчика и его участок")
        sensor_ids.add(s["id"])
        if s["measurement_type"] not in ["air_2m", "canopy", "soil"]: raise ValueError("Тип датчика: air_2m, canopy или soil")
        s["height_m"] = numeric(s.get("height_m"), -5, 20, "Высота датчика", True)
        s["interval_minutes"] = numeric(s.get("interval_minutes"), 1, 120, "Шаг датчика")
        if s["calibration_status"] not in ["unknown", "checked"]: raise ValueError("Некорректный статус проверки датчика")
    for z in zones:
        reference = z.get("reference_sensor_id", "")
        if reference and not any(s["id"] == reference and s["zone_id"] == z["id"] for s in sensors): raise ValueError("Опорный датчик должен находиться на этом участке")
    return p


def readiness(p):
    missing = []
    if not p["name"].strip(): missing.append("Название хозяйства")
    if not p["agronomist"].strip(): missing.append("Ответственный агроном")
    if p["equipment_status"] == "unknown" or (p["equipment_status"] == "available" and not p["equipment"]): missing.append("Доступное оборудование")
    if p["decision"]["required_lead_hours"] is None: missing.append("Необходимая заблаговременность")
    if p["decision"]["false_alarm_budget_per_month"] is None: missing.append("Допустимая частота ложных тревог")
    if p["decision"]["review_status"] != "reviewed": missing.append("Согласованный с агрономом регламент")
    for z in p["zones"]:
        prefix = z["name"] or z["id"]
        if z["latitude"] is None: missing.append(prefix+": координаты")
        if not z.get("geometry"): missing.append(prefix+": контур участка")
        if z.get("area_ha") is None: missing.append(prefix+": площадь")
        if not z.get("cultivar"): missing.append(prefix+": сорт")
        if not z.get("phase") or not z.get("phase_recorded_at_utc"): missing.append(prefix+": актуальная фаза и дата записи")
        s = next((s for s in p["sensors"] if s["id"] == z.get("reference_sensor_id")), None)
        if not s: missing.append(prefix+": опорный датчик")
        elif s["measurement_type"] != "air_2m" or s["height_m"] is None or abs(s["height_m"]-2) > .2 or s["calibration_status"] != "checked":
            missing.append(prefix+": проверенный датчик воздуха около 2 м")
    return {"ready_for_field_decision_review": p["mode"] == "field" and not missing, "missing": missing,
            "mode": p["mode"], "shadow_only": True, "crop_damage_model": False}


class FieldStore:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory/"field.sqlite3"
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,
                    profile_id TEXT NOT NULL,recorded_at_utc TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_kind_profile ON events(kind,profile_id);
                CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only'); END;
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        try:
            with c: yield c
        finally: c.close()

    def append(self, kind, profile_id, payload, now=None, connection=None):
        now = now or utc_now(); body = encoded(payload); key = uuid.uuid4().hex
        row = (key, kind, profile_id, now.isoformat(), body, hashlib.sha256(body.encode()).hexdigest())
        if connection is None:
            with self.connect() as c: c.execute("INSERT INTO events(id,kind,profile_id,recorded_at_utc,payload,sha256) VALUES(?,?,?,?,?,?)", row)
        else: connection.execute("INSERT INTO events(id,kind,profile_id,recorded_at_utc,payload,sha256) VALUES(?,?,?,?,?,?)", row)
        return {"id": key, "kind": kind, "profile_id": profile_id, "recorded_at_utc": now.isoformat(), "payload": payload, "sha256": row[-1]}

    def records(self, kind=None, profile_id=None):
        query = "SELECT id,kind,profile_id,recorded_at_utc,payload,sha256 FROM events"
        terms, values = [], []
        if kind: terms.append("kind=?"); values.append(kind)
        if profile_id: terms.append("profile_id=?"); values.append(profile_id)
        if terms: query += " WHERE "+" AND ".join(terms)
        query += " ORDER BY seq"
        with self.connect() as c: rows = c.execute(query, values).fetchall()
        result = []
        for key, k, p, when, body, h in rows:
            if hashlib.sha256(body.encode()).hexdigest() != h: raise ValueError("Нарушена контрольная сумма журнала")
            result.append({"id": key, "kind": k, "profile_id": p, "recorded_at_utc": when, "payload": json.loads(body), "sha256": h})
        return result

    def profile_record(self, profile_id):
        records = self.records("profile", identifier(profile_id))
        if not records: raise ValueError("Паспорт не найден")
        return records[-1]

    def save_profile(self, raw, expected_revision=None, now=None):
        p = validate_profile(raw); now = now or utc_now()
        dates = [p["decision"].get("reviewed_at_utc")]+[z.get("phase_recorded_at_utc") for z in p["zones"]]
        if any(stamp(t) > now+timedelta(minutes=2) for t in dates if t): raise ValueError("Дата фазы или согласования не может быть в будущем")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT id,payload FROM events WHERE kind='profile' AND profile_id=? ORDER BY seq DESC LIMIT 1", (p["id"],)).fetchone()
            if row and expected_revision != row[0]: raise ValueError("Паспорт изменился: обновите страницу перед сохранением")
            if row and json.loads(row[1])["mode"] != p["mode"]: raise ValueError("Режим demo/field нельзя менять для существующего паспорта")
            return self.append("profile", p["id"], p, now, c)

    def init(self):
        if not self.records("profile"):
            self.save_profile(json.loads((ROOT/"config/field_profile.json").read_text(encoding="utf-8")))
        return self

    def import_measurements(self, profile_id, content, filename="measurements.csv", now=None):
        now = now or utc_now(); p = self.profile_record(profile_id)["payload"]
        if not isinstance(content, str) or len(content.encode()) > 2_000_000: raise ValueError("CSV должен быть не больше 2 МБ")
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if not {"sensor_id", "observed_at", "temperature_c", "rh_pct"}.issubset(reader.fieldnames or []): raise ValueError("Нужны столбцы sensor_id,observed_at,temperature_c,rh_pct")
        sensors = {s["id"]: s for s in p["sensors"]}
        existing = { (r["payload"]["sensor_id"], r["payload"].get("observed_at_utc")): r["payload"] for r in self.records("measurement", profile_id) if r["payload"].get("status") == "accepted"}
        prepared, counts = [], Counter()
        raw_rows = list(reader)
        if len(raw_rows) > 10000: raise ValueError("Один импорт ограничен 10000 строками")
        parsed = []
        for line, row in enumerate(raw_rows, 2):
            item = {"line": line, "raw": row, "sensor_id": row.get("sensor_id", ""), "observed_at_utc": None,
                    "temperature_c": None, "rh_pct": None, "status": "accepted", "reasons": [], "mode": p["mode"]}
            try:
                if item["sensor_id"] not in sensors: raise ValueError("Неизвестный датчик")
                item["zone_id"] = sensors[item["sensor_id"]]["zone_id"]
                t = stamp(row["observed_at"])
                if t > now+timedelta(minutes=2): raise ValueError("Время измерения находится в будущем")
                item["observed_at_utc"] = t.isoformat()
                item["temperature_c"] = numeric(row["temperature_c"], -60, 60, "Температура", True)
                item["rh_pct"] = numeric(row["rh_pct"], 0, 100, "Влажность", True)
                if item["temperature_c"] is None and item["rh_pct"] is None: raise ValueError("Нет измеренных значений")
            except (ValueError, TypeError) as exc:
                item["status"] = "rejected"; item["reasons"].append(str(exc))
            parsed.append(item)
        last = {}
        for item in sorted(parsed, key=lambda r: r["observed_at_utc"] or ""):
            if item["status"] == "accepted":
                key = (item["sensor_id"], item["observed_at_utc"]); prior = existing.get(key)
                if prior:
                    if all(prior[k] == item[k] for k in ["temperature_c", "rh_pct"]): item["status"] = "duplicate"
                    else: item["status"] = "suspect"; item["reasons"].append("Конфликт значений в одно время; требуется проверка")
                previous = last.get(item["sensor_id"])
                if previous is None:
                    earlier = [v for (sid, when), v in existing.items() if sid == item["sensor_id"] and when < item["observed_at_utc"]]
                    previous = max(earlier, key=lambda v: v["observed_at_utc"]) if earlier else None
                if previous and item["temperature_c"] is not None and previous["temperature_c"] is not None:
                    minutes = (stamp(item["observed_at_utc"])-stamp(previous["observed_at_utc"])).total_seconds()/60
                    if 0 < minutes <= 30 and abs(item["temperature_c"]-previous["temperature_c"])/minutes > .5:
                        item["status"] = "suspect"; item["reasons"].append("Скачок более 0,5 °C/мин: эвристика, требуется проверка")
                if item["status"] == "accepted": existing[key] = item; last[item["sensor_id"]] = item
            counts[item["status"]] += 1; prepared.append(item)
        batch = {"filename": Path(filename).name, "csv_sha256": hashlib.sha256(content.encode()).hexdigest(), "raw_csv": content,
                 "counts": dict(counts), "mode": p["mode"], "qc_policy": "range/time/identity/duplicate/spike screening v1; not independent sensor calibration"}
        with self.connect() as c:
            batch_record = self.append("import_batch", profile_id, batch, now, c)
            for item in prepared:
                self.append("measurement", profile_id, {**item, "batch_id": batch_record["id"]}, now, c)
        return {"batch_id": batch_record["id"], "counts": dict(counts), "rows": len(prepared)}

    def review_measurement(self, measurement_id, status, note, now=None):
        records = self.records("measurement")
        record = next((r for r in records if r["id"] == measurement_id), None)
        if not record or status not in ["accepted", "rejected"] or not str(note).strip(): raise ValueError("Для проверки нужны запись, статус и объяснение")
        if status == "accepted" and record["payload"]["status"] in ["rejected", "duplicate"]: raise ValueError("Ошибку формата/диапазона нельзя принять без исправленного импорта")
        return self.append("qc_review", record["profile_id"], {"measurement_id": measurement_id, "status": status, "note": str(note)}, now)

    def measurements(self, profile_id):
        reviews = {r["payload"]["measurement_id"]: r for r in self.records("qc_review", profile_id)}
        records = self.records("measurement", profile_id); result = []
        for r in records:
            review = reviews.get(r["id"])
            result.append({**r, "effective_status": review["payload"]["status"] if review else r["payload"]["status"],
                           "reviewed_at_utc": review["recorded_at_utc"] if review else None,
                           "review_note": review["payload"]["note"] if review else None})
        return result

    def issue(self, profile_id, zone_id, forecast, now=None):
        now = now or utc_now(); profile_record = self.profile_record(profile_id); p = profile_record["payload"]
        zone = next((z for z in p["zones"] if z["id"] == zone_id), None)
        if not zone: raise ValueError("Участок не найден")
        if forecast["origin"] not in ["live_gfs", "demo"]: raise ValueError("Неизвестное происхождение прогноза")
        if forecast["origin"] == "demo" and p["mode"] != "demo": raise ValueError("Синтетический прогноз запрещён в полевом паспорте")
        if stamp(forecast["issued_at_utc"]) > now: raise ValueError("Будущая выдача недопустима")
        night = date.fromisoformat(forecast["night_date_local"])
        times = [datetime(night.year, night.month, night.day, 16, tzinfo=UTC)+timedelta(hours=i) for i in range(14)]
        hours = forecast["hours"]
        if len(hours) != 14 or [stamp(r["valid_at_utc"]) for r in hours] != times: raise ValueError("Прогноз должен содержать 14 часов 19–08 МСК")
        for r in hours:
            numeric(r["temperature_c"], -90, 65, "Прогноз температуры")
            numeric(r["rh_pct"], 0, 100, "Прогноз влажности")
        issued, run = stamp(forecast["issued_at_utc"]), stamp(forecast["run_at_utc"])
        if run > issued: raise ValueError("Выпуск модели позже выдачи")
        if forecast["origin"] == "live_gfs":
            if forecast.get("capture_profile_revision") != profile_record["id"]: raise ValueError("Паспорт изменился во время получения GFS; повторите загрузку")
            if issued >= times[0] or now >= times[0]: raise ValueError("Оперативная выдача должна быть до начала ночи")
            if forecast.get("availability_evidence") != "observed_retrieval" or stamp(forecast["retrieved_by_utc"]) > issued:
                raise ValueError("Нет доказательства получения всех данных до выдачи")
            if not forecast.get("manifest"): raise ValueError("Нет происхождения исходных данных")
        policy = p["decision"]
        cold = timed_frost([r["temperature_c"]-policy["weather_threshold_c"] for r in hours], [r["valid_at_utc"] for r in hours])
        return self.append("forecast", profile_id, {**forecast, "profile_snapshot": p, "profile_revision": profile_record["id"], "zone_id": zone_id,
                                                    "mode": p["mode"], "weather_frost": cold, "shadow_only": True,
                                                    "model": "GFS uncorrected; local transfer of research models not validated", "crop_damage_probability": None}, now)

    def acknowledge_forecast(self, profile_id, forecast_id, now=None):
        now = now or utc_now()
        fore = next((f for f in self.records("forecast", profile_id) if f["id"] == forecast_id), None)
        if not fore: raise ValueError("Выберите сохранённый прогноз этого паспорта")
        if stamp(fore["payload"]["issued_at_utc"]) > now: raise ValueError("Прогноз ещё не выдан")
        return self.append("forecast_read", profile_id, {"forecast_id": fore["id"], "seen_at_utc": now.isoformat(),
                                                       "mode": fore["payload"]["mode"]}, now)

    def journal(self, profile_id, raw, now=None):
        now = now or utc_now(); fore = next((f for f in self.records("forecast", profile_id) if f["id"] == raw.get("forecast_id")), None)
        if not fore: raise ValueError("Выберите сохранённый прогноз")
        if raw.get("action") not in ["prepare", "observe", "protection", "none"]: raise ValueError("Выберите действие")
        actor = str(raw.get("actor", "")).strip()
        if not actor: raise ValueError("Укажите ответственного")
        record_now = raw.get("record_now", False)
        if not isinstance(record_now, bool): raise ValueError("Укажите способ записи времени решения")
        when = now if record_now else stamp(raw["decision_at_utc"])
        if not stamp(fore["payload"]["issued_at_utc"]) <= when <= now: raise ValueError("Решение должно быть между выдачей и текущим временем")
        seen = stamp(raw["seen_at_utc"]) if raw.get("seen_at_utc") else None
        read_id = raw.get("read_id")
        review_read_id = raw.get("review_read_id")
        if read_id and review_read_id: raise ValueError("Укажите одно основание прочтения")
        if read_id or review_read_id:
            read = next((r for r in self.records("forecast_read", profile_id) if r["id"] == (read_id or review_read_id)), None)
            if not read or read["payload"]["forecast_id"] != fore["id"]: raise ValueError("Подтверждение прочтения относится к другой выдаче")
            recorded_seen = stamp(read["payload"]["seen_at_utc"])
            if read_id:
                if seen is not None and seen != recorded_seen: raise ValueError("Время подтверждённого прочтения нельзя менять")
                seen = recorded_seen
            elif seen is not None:
                raise ValueError("Текущий просмотр не подтверждает прочтение в прошлом")
        if seen and not stamp(fore["payload"]["issued_at_utc"]) <= seen <= when: raise ValueError("Прочтение должно быть после выдачи и до решения")
        if raw.get("helpful") not in [True, False, None]: raise ValueError("Оценка полезности: да/нет/неизвестно")
        if raw.get("protection_status", "unknown") not in ["unknown", "not_used", "used"]: raise ValueError("Укажите, применялась ли защита в этой зоне ночью")
        return self.append("journal", profile_id, {"forecast_id": fore["id"], "actor": actor, "action": raw["action"],
                                                 "seen_at_utc": seen.isoformat() if seen else None, "decision_at_utc": when.isoformat(),
                                                 "cost_rub": numeric(raw.get("cost_rub"), 0, 1e9, "Фактические затраты", True),
                                                 "helpful": raw.get("helpful"), "protection_status": raw.get("protection_status", "unknown"),
                                                 "notes": str(raw.get("notes", "")), "mode": fore["payload"]["mode"],
                                                 "read_id": read_id or None, "review_read_id": review_read_id or None,
                                                 "decision_time_source": "server_now" if record_now else "operator_reported"}, now)

    def assess(self, forecast_record, now=None):
        now = now or utc_now(); f = forecast_record["payload"]; p = f["profile_snapshot"]
        z = next(z for z in p["zones"] if z["id"] == f["zone_id"])
        sensor = next((s for s in p["sensors"] if s["id"] == z.get("reference_sensor_id")), None)
        target_ok = sensor and sensor["measurement_type"] == "air_2m" and sensor["height_m"] is not None and abs(sensor["height_m"]-2) <= .2 and sensor["calibration_status"] == "checked"
        observations = [r for r in self.measurements(forecast_record["profile_id"]) if target_ok and r["effective_status"] == "accepted" and
                        r["payload"]["sensor_id"] == sensor["id"] and r["payload"].get("zone_id") == z["id"]]
        # A documented accepted QC revision can replace a conflicting point; forecasts remain immutable.
        by_time = {}
        for r in observations:
            for quantity in ["temperature_c", "rh_pct"]:
                if r["payload"][quantity] is None: continue
                key = (r["payload"]["observed_at_utc"], quantity); prior = by_time.get(key)
                order = r["reviewed_at_utc"] or r["recorded_at_utc"]
                if prior is None or order >= (prior["reviewed_at_utc"] or prior["recorded_at_utc"]): by_time[key] = r
        matched = []
        for h in f["hours"]:
            item = {"valid_at_utc": h["valid_at_utc"]}
            for quantity, id_key, time_key in [("temperature_c", "measurement_id", "matched_observed_at_utc"), ("rh_pct", "rh_measurement_id", "matched_rh_observed_at_utc")]:
                candidates = [r for (_, q), r in by_time.items() if q == quantity and abs((stamp(r["payload"]["observed_at_utc"])-stamp(h["valid_at_utc"])).total_seconds()) <= 300]
                best = min(candidates, key=lambda r: (abs((stamp(r["payload"]["observed_at_utc"])-stamp(h["valid_at_utc"])).total_seconds()), r["payload"]["observed_at_utc"])) if candidates else None
                item.update({quantity: best["payload"][quantity] if best else None, id_key: best["id"] if best else None, time_key: best["payload"]["observed_at_utc"] if best else None})
            matched.append(item)
        journals = [j for j in self.records("journal", forecast_record["profile_id"]) if j["payload"]["forecast_id"] == forecast_record["id"]]
        same_night = {r["id"] for r in self.records("forecast", forecast_record["profile_id"]) if r["payload"]["zone_id"] == z["id"] and r["payload"]["night_date_local"] == f["night_date_local"]}
        night_journals = [j for j in self.records("journal", forecast_record["profile_id"]) if j["payload"]["forecast_id"] in same_night]
        affected = any(j["payload"]["action"] == "protection" or j["payload"].get("protection_status") == "used" for j in night_journals)
        protection_known = any(j["payload"].get("protection_status") == "not_used" for j in night_journals)
        complete = all(h["temperature_c"] is not None for h in matched)
        ended = now >= stamp(f["hours"][-1]["valid_at_utc"])+timedelta(hours=1)
        truth = timed_frost([h["temperature_c"]-p["decision"]["weather_threshold_c"] if h["temperature_c"] is not None else None for h in matched], [h["valid_at_utc"] for h in matched])
        outcome = None
        if complete and ended and not affected and protection_known:
            a, b = truth["event"], f["weather_frost"]["event"]
            outcome = "hit" if a and b else "miss" if a else "false_alarm" if b else "correct_negative"
        # Follow-up diary entries must not replace the first recorded pre-night decision.
        night_start = stamp(f["hours"][0]["valid_at_utc"])
        prospective = [j for j in journals if stamp(j["payload"]["decision_at_utc"]) < night_start and stamp(j["recorded_at_utc"]) < night_start]
        first = min(prospective, key=lambda j: (j["payload"]["decision_at_utc"], j["recorded_at_utc"])) if prospective else None
        onset = truth["first_cold_sample_at_utc"] if outcome == "hit" else None
        lead = (stamp(onset)-stamp(first["payload"]["decision_at_utc"])).total_seconds()/3600 if onset and first else None
        first_index = truth["first_cold_sample_index"] if outcome == "hit" else None
        onset_lower = matched[first_index-1]["matched_observed_at_utc"] if first_index is not None and first_index > 0 else None
        lower_lead = (stamp(onset_lower)-stamp(first["payload"]["decision_at_utc"])).total_seconds()/3600 if onset_lower and first else None
        eligible = f["mode"] == "field" and f["origin"] == "live_gfs" and outcome is not None
        exclusions = []
        if f["mode"] == "demo": exclusions.append("demo_profile")
        if f["origin"] != "live_gfs": exclusions.append("synthetic_source")
        if not ended: exclusions.append("night_pending")
        if not target_ok: exclusions.append("reference_sensor_unverified")
        if not complete: exclusions.append("temperature_incomplete")
        if affected: exclusions.append("protection_used")
        elif not protection_known: exclusions.append("protection_unknown")
        return {"forecast_id": forecast_record["id"], "night_date_local": f["night_date_local"], "zone_id": f["zone_id"], "mode": f["mode"],
                "matched_hours": sum(h["temperature_c"] is not None for h in matched), "expected_hours": 14, "sensor_target_verified": bool(target_ok),
                "matched_rh_hours": sum(h["rh_pct"] is not None for h in matched), "exclusion_reasons": exclusions,
                "missing_temperature_at_utc": [h["valid_at_utc"] for h in matched if h["temperature_c"] is None],
                "status": "intervention_confounded" if affected else "awaiting_night" if not ended else "intervention_unknown" if not protection_known else "scored" if outcome else "insufficient_measurements",
                "outcome": outcome, "prospective_field_score_eligible": eligible, "observations": matched, "hindsight_frost": truth,
                "duration_absolute_error_h": abs(f["weather_frost"]["cold_bins_hours"]-truth["cold_bins_hours"]) if outcome else None,
                "first_sample_absolute_error_h": abs(f["weather_frost"]["first_cold_sample_index"]-truth["first_cold_sample_index"]) if outcome == "hit" else None,
                "decision_lead_to_observed_cold_h": lead, "lead_decision_id": first["id"] if first else None,
                "lead_decision_recorded_before_night": stamp(first["recorded_at_utc"]) < stamp(f["hours"][0]["valid_at_utc"]) if first else None,
                "required_lead_hours": p["decision"]["required_lead_hours"],
                "decision_lead_to_onset_lower_bound_h": lower_lead,
                "retrospective_decision_entries": sum(stamp(j["recorded_at_utc"]) >= night_start for j in journals),
                "readiness_lead_met": lower_lead >= p["decision"]["required_lead_hours"] if lower_lead is not None and p["decision"]["required_lead_hours"] is not None else None,
                "journal_count": len(journals), "reported_cost_rub": sum(j["payload"]["cost_rub"] for j in journals if j["payload"]["cost_rub"] is not None) if any(j["payload"]["cost_rub"] is not None for j in journals) else None,
                "cost_entries": sum(j["payload"]["cost_rub"] is not None for j in journals), "prevented_loss_rub": None,
                "measurement_matching": "nearest accepted reference air-2m point within 5min; no interpolation; calibration declared by operator"}

    def inspection(self, profile_id, raw, now=None):
        now = now or utc_now()
        fore = next((f for f in self.records("forecast", profile_id) if f["id"] == raw.get("forecast_id")), None)
        if not fore: raise ValueError("Выберите сохранённый прогноз")
        when = stamp(raw["observed_at_utc"]); f = fore["payload"]
        if not stamp(f["hours"][-1]["valid_at_utc"])+timedelta(hours=1) <= when <= now: raise ValueError("Осмотр записывается после окончания ночи и до текущего времени")
        actor = str(raw.get("actor", "")).strip(); method = str(raw.get("sampling_method", "")).strip()
        if not actor or not method: raise ValueError("Укажите ответственного и способ отбора образцов")
        total = numeric(raw.get("examined_units"), 1, 1e6, "Осмотрено образцов")
        damaged = numeric(raw.get("damaged_units"), 0, total, "Повреждено образцов")
        if int(total) != total or int(damaged) != damaged: raise ValueError("Количество образцов должно быть целым")
        return self.append("inspection", profile_id, {"forecast_id": fore["id"], "zone_id": f["zone_id"], "night_date_local": f["night_date_local"],
                           "actor": actor, "observed_at_utc": when.isoformat(), "examined_units": int(total), "damaged_units": int(damaged),
                           "sample_damage_pct": 100*damaged/total, "sampling_method": method, "notes": str(raw.get("notes", "")),
                           "mode": f["mode"], "causal_attribution": None}, now)

    def state(self, now=None):
        now = now or utc_now(); latest = {}
        for r in self.records("profile"): latest[r["profile_id"]] = r
        forecasts = self.records("forecast")
        assessments = [self.assess(f, now) for f in forecasts]
        # Select latest field issue before looking at its truth completeness or score.
        # An unscorable latest issue must never silently fall back to an older, scorable one.
        unique = {}
        for f, a in zip(forecasts, assessments):
            if f["payload"]["mode"] == "field" and f["payload"]["origin"] == "live_gfs":
                key = (f["profile_id"], a["zone_id"], a["night_date_local"])
                if key not in unique or stamp(f["payload"]["issued_at_utc"]) >= stamp(unique[key][0]["payload"]["issued_at_utc"]): unique[key] = (f, a)
        selected_ids = {f["id"] for f, _ in unique.values()}
        for f, a in zip(forecasts, assessments):
            a["selected_for_field_metrics"] = f["id"] in selected_ids and a["prospective_field_score_eligible"]
            if f["payload"]["mode"] == "field" and f["payload"]["origin"] == "live_gfs" and f["id"] not in selected_ids:
                a["exclusion_reasons"].append("superseded_issue")
        coverage = [{"profile_id": f["profile_id"], "forecast_id": f["id"], "zone_id": a["zone_id"], "night_date_local": a["night_date_local"],
                     "included": a["selected_for_field_metrics"], "status": a["status"], "reasons": a["exclusion_reasons"]} for f, a in unique.values()]
        scored_pairs = [(f, a) for f, a in unique.values() if a["selected_for_field_metrics"]]
        scored = [a for _, a in scored_pairs]
        totals = dict(Counter(a["outcome"] for a in scored))
        journals = self.records("journal")
        errors = [abs(h["temperature_c"]-a["observations"][i]["temperature_c"]) for f, a in scored_pairs for i, h in enumerate(f["payload"]["hours"])]
        rh_errors = [abs(h["rh_pct"]-a["observations"][i]["rh_pct"]) for f, a in scored_pairs for i, h in enumerate(f["payload"]["hours"]) if a["observations"][i]["rh_pct"] is not None]
        cold = [a for a in scored if a["outcome"] in ["hit", "miss"]]
        starts = [a["first_sample_absolute_error_h"] for a in scored if a["outcome"] == "hit"]
        field_journals = [j for j in journals if j["payload"]["mode"] == "field"]
        mean = lambda v: sum(v)/len(v) if v else None
        qc = [r for pid in latest for r in self.measurements(pid) if r["effective_status"] in ["suspect", "rejected"]][-100:]
        from .field_decision import forecast_brief
        return {"profiles": [{**r, "readiness": readiness(r["payload"])} for r in latest.values()], "forecasts": forecasts,
                "forecast_briefs": [{**forecast_brief(f["payload"], now), "forecast_id": f["id"]} for f in forecasts], "field_coverage": coverage,
                "assessments": assessments, "reads": self.records("forecast_read"), "prototype_feedback": self.records("prototype_feedback"), "batches": [{**r, "payload": {k: v for k, v in r["payload"].items() if k != "raw_csv"}} for r in self.records("import_batch")],
                "qc_queue": qc, "journal": journals, "inspections": self.records("inspection"),
                "field_metrics": {"unique_site_nights": len(scored), "candidate_site_nights": len(unique),
                                  "pending_site_nights": sum("night_pending" in c["reasons"] for c in coverage),
                                  "excluded_completed_site_nights": sum(not c["included"] and "night_pending" not in c["reasons"] for c in coverage),
                                  "outcomes": totals, "cold_nights": sum(a["outcome"] in ["hit", "miss"] for a in scored),
                                  "hourly_temperature_mae_c": mean(errors), "cold_duration_mae_h": mean([a["duration_absolute_error_h"] for a in cold]),
                                  "hourly_rh_mae_pp": mean(rh_errors), "paired_rh_hours": len(rh_errors),
                                  "first_sample_mae_hits_h": mean(starts), "first_sample_pairs": len(starts),
                                  "lead_confirmation_pairs": sum(a["readiness_lead_met"] is not None for a in scored),
                                  "lead_requirement_met_count": sum(a["readiness_lead_met"] is True for a in scored),
                                  "known_cost_entries": sum(j["payload"]["cost_rub"] is not None for j in field_journals),
                                  "reported_cost_rub": sum(j["payload"]["cost_rub"] for j in field_journals if j["payload"]["cost_rub"] is not None) if any(j["payload"]["cost_rub"] is not None for j in field_journals) else None,
                                  "helpful_responses": sum(j["payload"]["helpful"] is True for j in field_journals),
                                  "helpfulness_responses": sum(j["payload"]["helpful"] is not None for j in field_journals),
                                  "prevented_loss_rub": None, "note": "Select latest pre-night field issue before checking truth; no fallback to older score; demo/protection/unknown excluded; RH scored on available pairs only; no causal savings estimate"},
                "generated_at_utc": now.isoformat(), "shadow_only": True}


def seed_demo(store, now=None):
    now = now or utc_now(); profile_id = "demo-station"
    if store.records("profile", profile_id): return profile_id
    p = json.loads((ROOT/"config/field_profile.json").read_text(encoding="utf-8"))
    p.update(id=profile_id, mode="demo", name="Учебная площадка: станция Краснодар, не сад", agronomist="Демонстрационный оператор", equipment_status="none")
    p["decision"]["required_lead_hours"] = 2.; p["decision"]["false_alarm_budget_per_month"] = 2.
    p["zones"][0].update(latitude=45.0333, longitude=39.15, cultivar="Не задан", phase="Не задана", reference_sensor_id="demo-t1")
    p["sensors"] = [{"id": "demo-t1", "zone_id": "z1", "measurement_type": "air_2m", "height_m": 2., "interval_minutes": 60., "calibration_status": "checked", "note": "Синтетический датчик, не полевое оборудование"}]
    store.save_profile(p, now=now)
    for days, kind in [(5, "hit"), (4, "false_alarm"), (3, "miss"), (2, "correct_negative")]:
        night = (now+timedelta(hours=3)).date()-timedelta(days=days)
        times = [datetime(night.year, night.month, night.day, 16, tzinfo=UTC)+timedelta(hours=i) for i in range(14)]
        curve = [3.-i*.45 if kind in ["hit", "false_alarm"] else 3.+i*.03 for i in range(14)]
        actual = [2.-i*.4 if kind in ["hit", "miss"] else 3.+i*.02 for i in range(14)]
        issued = times[0]-timedelta(hours=3)
        forecast = store.issue(profile_id, "z1", {"origin": "demo", "night_date_local": night.isoformat(), "issued_at_utc": issued.isoformat(),
                                                 "run_at_utc": (issued-timedelta(hours=7)).isoformat(), "availability_evidence": "synthetic_demo",
                                                 "hours": [{"valid_at_utc": t.isoformat(), "temperature_c": v, "rh_pct": 75.} for t, v in zip(times, curve)],
                                                 "source_note": "Все значения синтетические; не прогноз хозяйства"}, now)
        output = io.StringIO(); writer = csv.writer(output); writer.writerow(["sensor_id", "observed_at", "temperature_c", "rh_pct"])
        for t, value in zip(times, actual): writer.writerow(["demo-t1", t.isoformat(), value, 80])
        store.import_measurements(profile_id, output.getvalue(), "synthetic-demo.csv", now)
        store.journal(profile_id, {"forecast_id": forecast["id"], "actor": "Демо-оператор", "action": "observe", "decision_at_utc": (issued+timedelta(minutes=10)).isoformat(),
                                  "seen_at_utc": (issued+timedelta(minutes=5)).isoformat(), "cost_rub": None, "helpful": kind == "hit", "protection_status": "not_used",
                                  "notes": "Сценарная запись, не факт полевого решения"}, now)
    return profile_id
