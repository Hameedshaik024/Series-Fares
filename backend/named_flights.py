"""
Named-flight/fare-class poster groups sourced from Alhind - specific
flights + fare classes chosen by name (not "cheapest available").

Each named-flight group (keyed the same as the WhatsApp group it posts
to) is a list of "posters". Each poster bundles several routes into ONE
stacked image (poster.build_multi - one compact mini-calendar per route,
all sharing the same date range), and is sent as its own image to the
group's WhatsApp group.

Every (origin_option/dest_option) value must be the exact text Alhind's
autocomplete shows for that city - confirm live, don't guess: some
cities (e.g. Hyderabad) have more than one airport and need the
"CITY - CODE" form to disambiguate.
"""

# NOTE: "DUBAI - DXB" (dest_option below) follows the same "CITY - CODE"
# autocomplete pattern confirmed live for every other city here, but
# hasn't itself been confirmed live yet (the saved Alhind session had
# expired when this was written, and no OTP was on hand to relogin). If
# it's wrong, city-select fails loudly with a clear error - it won't
# silently produce a wrong poster - so this is safe to try; just recheck
# the exact autocomplete text against the real site on the first run.
DUBAI_POSTERS = [
    {
        "title": "Fares to Dubai",
        "routes": [
            {
                "origin_search": "Chennai", "origin_option": "CHENNAI - MAA",
                "origin_code": "MAA", "origin_label": "Chennai",
                "dest_search": "Dubai", "dest_option": "DUBAI - DXB",
                "dest_code": "DXB", "dest_label": "Dubai",
                "flight_no": "6E 1471", "fare_type": None,  # None = lowest fare of any class
            },
            {
                "origin_search": "Bangalore", "origin_option": "BANGALORE - BLR",
                "origin_code": "BLR", "origin_label": "Bangalore",
                "dest_search": "Dubai", "dest_option": "DUBAI - DXB",
                "dest_code": "DXB", "dest_label": "Dubai",
                "flight_no": ["6E 1485", "IX 921"], "fare_type": None,
            },
            {
                "origin_search": "Hyderabad", "origin_option": "HYDERABAD - HYD",
                "origin_code": "HYD", "origin_label": "Hyderabad",
                "dest_search": "Dubai", "dest_option": "DUBAI - DXB",
                "dest_code": "DXB", "dest_label": "Dubai",
                "flight_no": ["6E 1465", "IX 951"], "fare_type": None,
            },
        ],
    },
    {
        "title": "Fares from Dubai",
        "routes": [
            {
                "origin_search": "Dubai", "origin_option": "DUBAI - DXB",
                "origin_code": "DXB", "origin_label": "Dubai",
                "dest_search": "Chennai", "dest_option": "CHENNAI - MAA",
                "dest_code": "MAA", "dest_label": "Chennai",
                "flight_no": "6E 1472", "fare_type": None,
            },
            {
                "origin_search": "Dubai", "origin_option": "DUBAI - DXB",
                "origin_code": "DXB", "origin_label": "Dubai",
                "dest_search": "Bangalore", "dest_option": "BANGALORE - BLR",
                "dest_code": "BLR", "dest_label": "Bangalore",
                "flight_no": ["6E 1486", "IX 920"], "fare_type": None,
            },
            {
                "origin_search": "Dubai", "origin_option": "DUBAI - DXB",
                "origin_code": "DXB", "origin_label": "Dubai",
                "dest_search": "Hyderabad", "dest_option": "HYDERABAD - HYD",
                "dest_code": "HYD", "dest_label": "Hyderabad",
                "flight_no": ["6E 1466", "IX 972"], "fare_type": None,
            },
        ],
    },
]

# Keyed by the same route-group name used for the WhatsApp group lookup.
NAMED_FLIGHT_GROUPS = {
    "dubai": DUBAI_POSTERS,
}
