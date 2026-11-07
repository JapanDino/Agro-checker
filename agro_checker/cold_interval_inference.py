"""Apply the new envelope only through the unchanged parent domain guard."""

from .cold_interval import forecast_envelope
from .minimum_model import safe_prediction


def safe_cold_envelope(model, envelope, night, interval_protocol, parent_protocol, station_id):
    guarded = safe_prediction(model, model["calibration"], night, parent_protocol, station_id)
    if guarded["prediction"] is None: return guarded
    return {"status": "research_only", "prediction": forecast_envelope(model, envelope, night, interval_protocol)}
