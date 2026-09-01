"""
Named-flight/fare-class poster groups sourced from Alhind - unlike the
automatic AirIQ pipeline (pricing.py always picks the cheapest fare
available), these are specific flights + fare classes chosen by name.
One poster per route, all of a group's posters bundled into a single PDF
and sent to that group's WhatsApp group.

Every (origin_option/dest_option) value here is the exact text Alhind's
autocomplete shows for that city - confirmed live, not guessed:
Hyderabad and Muscat both need the "CITY - CODE" form to disambiguate
(Hyderabad has two airports; "Muscat" alone can also match "Muscatine"),
Bangalore/Chennai are unambiguous either way.
"""

MUSCAT_ROUTES = [
    {
        "origin_search": "Hyderabad", "origin_option": "HYDERABAD - HYD",
        "origin_code": "HYD", "origin_label": "Hyderabad",
        "dest_search": "Muscat", "dest_option": "MUSCAT - MCT",
        "dest_code": "MCT", "dest_label": "Muscat",
        "flight_no": "6E 1273", "fare_type": "Tactical",
    },
    {
        "origin_search": "Muscat", "origin_option": "MUSCAT - MCT",
        "origin_code": "MCT", "origin_label": "Muscat",
        "dest_search": "Hyderabad", "dest_option": "HYDERABAD - HYD",
        "dest_code": "HYD", "dest_label": "Hyderabad",
        "flight_no": "6E 1274", "fare_type": "Tactical",
    },
    {
        "origin_search": "Bangalore", "origin_option": "BANGALORE - BLR",
        "origin_code": "BLR", "origin_label": "Bangalore",
        "dest_search": "Muscat", "dest_option": "MUSCAT - MCT",
        "dest_code": "MCT", "dest_label": "Muscat",
        "flight_no": "OV 784", "fare_type": "Value",
    },
    {
        "origin_search": "Muscat", "origin_option": "MUSCAT - MCT",
        "origin_code": "MCT", "origin_label": "Muscat",
        "dest_search": "Bangalore", "dest_option": "BANGALORE - BLR",
        "dest_code": "BLR", "dest_label": "Bangalore",
        "flight_no": "OV 783", "fare_type": "Value",
    },
    {
        "origin_search": "Chennai", "origin_option": "CHENNAI - MAA",
        "origin_code": "MAA", "origin_label": "Chennai",
        "dest_search": "Muscat", "dest_option": "MUSCAT - MCT",
        "dest_code": "MCT", "dest_label": "Muscat",
        "flight_no": "WY 252", "fare_type": "Super Saver",
    },
    {
        "origin_search": "Muscat", "origin_option": "MUSCAT - MCT",
        "origin_code": "MCT", "origin_label": "Muscat",
        "dest_search": "Chennai", "dest_option": "CHENNAI - MAA",
        "dest_code": "MAA", "dest_label": "Chennai",
        "flight_no": "WY 251", "fare_type": "Super Saver",
    },
]

# Keyed by the same route-group name used for the WhatsApp group lookup.
NAMED_FLIGHT_GROUPS = {
    "muscat": MUSCAT_ROUTES,
}
