"""
Shared fare-selection rule, used identically by the manual poster
generator and the WhatsApp auto-post job so the two never drift apart.

Rule:
  - If AIR IQ has any fare for the date, use the cheapest AIR IQ fare,
    plus a flat +500 markup (AirIQ fares are always marked up).
  - Else if Market Place has any fare, use its cheapest fare, unmarked up.
  - Else there's no fare for that date at all - the caller excludes it
    from the poster rather than showing an empty/sold-out row.
"""

AIRIQ_MARKUP = 500


def _cheapest(flights):
    return min(flights, key=lambda f: f["fare_inr"] if f["fare_inr"] is not None else float("inf"))


def pick_fare(day_data, manual_markup=0):
    """day_data: {"airiq": [flight...], "marketplace": [flight...]}.
    Returns None, or {"source", "flight", "base_fare", "final_fare"}."""
    airiq = [f for f in day_data.get("airiq", []) if f.get("fare_inr") is not None]
    marketplace = [f for f in day_data.get("marketplace", []) if f.get("fare_inr") is not None]

    if airiq:
        flight = _cheapest(airiq)
        base_fare = flight["fare_inr"]
        return {
            "source": "airiq",
            "flight": flight,
            "base_fare": base_fare,
            "final_fare": base_fare + AIRIQ_MARKUP + manual_markup,
        }

    if marketplace:
        flight = _cheapest(marketplace)
        base_fare = flight["fare_inr"]
        return {
            "source": "marketplace",
            "flight": flight,
            "base_fare": base_fare,
            "final_fare": base_fare + manual_markup,
        }

    return None
