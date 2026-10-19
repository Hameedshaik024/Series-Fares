"""
Renders a themed fare-calendar poster PNG covering a range of dates.
Input is already-priced, already-filtered rows (one already-chosen fare
per date) - this module only renders, it makes no fare-selection
decisions itself.

Layout: a calendar grid (like a real month-view fare calendar), sized to
span the whole requested date range even if that crosses a month
boundary. Most dates fly the same flight, so that flight's full details
(airline logo, timing, duration, baggage) are shown once in a header
strip; only dates flying something different get their own compact
flight info inline in the cell.

Two poster shapes:
- build()/build_html(): one route, one full-size calendar grid.
- build_multi()/build_html_multi(): several routes stacked as compact
  mini-calendars in a single poster image, sharing one date range - e.g.
  three routes all flying into (or out of) the same hub city.
"""
import os
import re
import base64
import datetime
from collections import Counter
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

MONTH_ABBR = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

THEMES = {
    "sky": dict(
        page_bg="#f9f9f7", surface="#fcfcfb", primary="#0b0b0b", secondary="#52514e",
        muted="#898781", accent="#256abf", border="#0b0b0b", grid="#e1e0d9",
        ramp=["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
        unavail_bg="#f0efec", strip_bg="#f0efec",
        badge_bg="#0ca30c", badge_text="#ffffff",
        logo="logo_navy.png",
    ),
    "ocean": dict(
        page_bg="#0a1628", surface="#0f2138", primary="#ffffff", secondary="#9fb4c9",
        muted="#6b8299", accent="#4dd4c0", border="#ffffff", grid="#1e3552",
        ramp=["#ffe8a3", "#ffd166", "#f5b942", "#e8a020", "#c67f0e", "#9c6108", "#6e4405"],
        unavail_bg="#16232f", strip_bg="#16283e",
        badge_bg="#4dd4c0", badge_text="#0b0b0b",
        logo="logo_white.png",
    ),
    "sunset": dict(
        page_bg="#fdf3ea", surface="#fffaf3", primary="#2b1c12", secondary="#7a6455",
        muted="#a1897a", accent="#d85f31", border="#2b1c12", grid="#eddcc8",
        ramp=["#ffe4d6", "#ffc9ab", "#ffa679", "#f3804f", "#d85f31", "#b0431e", "#7a2c10"],
        unavail_bg="#f5ebe0", strip_bg="#f5e8da",
        badge_bg="#2e8b57", badge_text="#ffffff",
        logo="logo_coral.png",
    ),
}


def _luminance(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _text_for(bg_hex, light_text, dark_text):
    return light_text if _luminance(bg_hex) < 0.42 else dark_text


def _logo_b64(theme_name):
    t = THEMES[theme_name]
    with open(os.path.join(ASSETS_DIR, t["logo"]), "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _normalize(text):
    """Collapse whitespace/case differences (e.g. "Salam Air" vs
    "SalamAir") so the same real-world flight isn't mistaken for two
    different ones."""
    return re.sub(r"\s+", "", (text or "")).lower()


def _flight_key(flight):
    return (_normalize(flight.get("airline")), _normalize(flight.get("flight_no")))


def _majority_flight(priced_days):
    """The (airline, flight_no) most dates fly, and a representative flight
    dict for it (details shown once in the header strip)."""
    if not priced_days:
        return None, None
    counts = Counter(_flight_key(d["flight"]) for d in priced_days)
    majority_key = counts.most_common(1)[0][0]
    for d in priced_days:
        if _flight_key(d["flight"]) == majority_key:
            return majority_key, d["flight"]
    return majority_key, None


def _week_grid(dates):
    """Sunday-start weeks fully covering the first..last date in `dates`."""
    first, last = dates[0], dates[-1]
    # date.weekday(): Mon=0..Sun=6. We want Sunday-start weeks.
    days_since_sunday = (first.weekday() + 1) % 7
    grid_start = first - datetime.timedelta(days=days_since_sunday)
    days_until_saturday = 6 - ((last.weekday() + 1) % 7)
    grid_end = last + datetime.timedelta(days=days_until_saturday)

    weeks = []
    cur = grid_start
    while cur <= grid_end:
        week = [cur + datetime.timedelta(days=i) for i in range(7)]
        weeks.append(week)
        cur += datetime.timedelta(days=7)
    return weeks


def _build_segment(origin, dest, dates, priced_days, theme, origin_label=None, dest_label=None):
    """Builds the markup pieces for one route's calendar (grid rows,
    flight-strip, period label, summary line) - shared by the single-route
    poster (build_html) and the multi-route stacked poster
    (build_html_multi), so both stay visually consistent."""
    t = THEMES[theme]

    by_date = {d["date"]: d for d in priced_days}
    has_fares = bool(priced_days)
    fares = [d["final_fare"] for d in priced_days]
    min_fare = min(fares) if has_fares else 0
    max_fare = max(fares) if has_fares else 0
    cheapest_date = min(priced_days, key=lambda d: d["final_fare"])["date"] if has_fares else None

    majority_key, majority_flight = _majority_flight(priced_days)

    def color_for(fare):
        ramp = t["ramp"]
        if max_fare == min_fare:
            idx = 0
        else:
            idx = round((fare - min_fare) / (max_fare - min_fare) * (len(ramp) - 1))
        return ramp[idx]

    weeks = _week_grid(dates) if dates else []
    range_start, range_end = (dates[0], dates[-1]) if dates else (None, None)

    cells_html = []
    for week in weeks:
        row = []
        for date in week:
            if date < range_start or date > range_end:
                row.append('<td class="cell empty"></td>')
                continue

            entry = by_date.get(date)
            if not entry:
                row.append(f'''<td class="cell unavailable">
                    <div class="daynum">{date.day}</div>
                    <div class="fare-na">&mdash;</div>
                </td>''')
                continue

            fare = entry["final_fare"]
            flight = entry["flight"]
            bg = color_for(fare)
            fg = _text_for(bg, "#ffffff", t["primary"])
            badge = ' <span class="badge">BEST</span>' if date == cheapest_date else ""

            is_majority = _flight_key(flight) == majority_key
            extra = ""
            if not is_majority:
                short_airline = (flight.get("airline") or "").split()[0] if flight.get("airline") else ""
                flight_no = flight.get("flight_no") or ""
                time_txt = flight.get("time") or ""
                dep_arr = time_txt.split(" - ")
                time_line = f'{dep_arr[0]} &rarr; {dep_arr[1]}' if len(dep_arr) == 2 else time_txt
                cell_logo_url = flight.get("logo_url")
                cell_logo_html = f'<img class="cell-logo" src="{cell_logo_url}">' if cell_logo_url else ""
                extra = f'''<div class="cell-flight">
                    {cell_logo_html}
                    <div class="cell-flight-text">
                        <div>{short_airline} {flight_no}</div>
                        <div>{time_line}</div>
                    </div>
                </div>'''

            row.append(f'''<td class="cell filled" style="background:{bg};color:{fg}">
                <div class="daynum">{date.day}{badge}</div>
                <div class="fare">&#8377;{fare:,.0f}</div>
                {extra}
            </td>''')
        cells_html.append("<tr>" + "".join(row) + "</tr>")

    table_rows = "\n".join(cells_html)

    # header flight strip: the majority flight's full details
    if majority_flight:
        stops = majority_flight.get("stops")
        is_direct = bool(stops) and "non" in stops.lower() and "stop" in stops.lower()
        items = []
        if stops:
            items.append(
                '<span class="stops-badge direct">Direct Flight</span>' if is_direct
                else f'<span class="stops-badge">{stops}</span>'
            )
        if majority_flight.get("flight_no"):
            items.append(f'<span>Flight <b>{majority_flight["flight_no"]}</b></span>')
        time_txt = majority_flight.get("time")
        if time_txt:
            dep_arr = time_txt.split(" - ")
            if len(dep_arr) == 2:
                items.append(f'<span>Dep <b>{dep_arr[0]}</b> &rarr; Arr <b>{dep_arr[1]}</b></span>')
        if majority_flight.get("duration"):
            items.append(f'<span>Duration <b>{majority_flight["duration"]}</b></span>')
        if majority_flight.get("baggage"):
            items.append(f'<span>Baggage <b>{majority_flight["baggage"]}</b></span>')
        flight_strip = '<span class="sep">&bull;</span>'.join(items)
        airline_tag = majority_flight.get("airline") or ""
        logo_url = majority_flight.get("logo_url")
        airline_block_html = f'''<div class="airline-block">
            {f'<img class="airline-logo" src="{logo_url}" alt="{airline_tag}">' if logo_url else ''}
            <span class="airline-name">{airline_tag}</span>
        </div>'''
    else:
        flight_strip = "<span>No live flight data for this route/range</span>"
        airline_block_html = ""

    origin_label = origin_label or origin
    dest_label = dest_label or dest

    if dates:
        first, last = dates[0], dates[-1]
        period_label = f"{first.day} {MONTH_ABBR[first.month]} &ndash; {last.day} {MONTH_ABBR[last.month]} {last.year}"
    else:
        period_label = ""

    summary = (
        f'Best fare: <b>&#8377;{min_fare:,.0f}</b> on {cheapest_date.day} {MONTH_ABBR[cheapest_date.month]} '
        f'&nbsp;|&nbsp; Range: &#8377;{min_fare:,.0f} &ndash; &#8377;{max_fare:,.0f}'
        if has_fares else "No fares found for this route in the selected range"
    )

    return {
        "origin": origin, "dest": dest, "origin_label": origin_label, "dest_label": dest_label,
        "table_rows": table_rows, "flight_strip": flight_strip, "airline_block_html": airline_block_html,
        "period_label": period_label, "summary": summary, "has_fares": has_fares,
    }


def build_html(origin, dest, dates, priced_days, theme="sunset",
                origin_label=None, dest_label=None, show_logo=True):
    """
    dates: full requested list of datetime.date (defines the grid span and
        the header's period label, even for dates that ended up with no
        fare and are shown as empty cells).
    priced_days: list of {"date", "source", "flight", "base_fare",
        "final_fare"} - already filtered (no unpriced dates).
    """
    t = THEMES[theme]
    logo_b64 = _logo_b64(theme) if show_logo else None
    seg = _build_segment(origin, dest, dates, priced_days, theme, origin_label, dest_label)

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{origin}-{dest} Fares</title>
<style>
  * {{ box-sizing: border-box; margin:0; padding:0; }}
  body {{ width: 1200px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: {t["page_bg"]}; color: {t["primary"]}; }}
  .poster {{ width: 1200px; background: {t["surface"]}; padding: 56px 64px 48px; }}
  .brand-bar {{ display: flex; justify-content: flex-end; margin-bottom: 24px; }}
  .brand-bar img {{ height: 46px; width: auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 3px solid {t["border"]}; padding-bottom: 20px; margin-bottom: 20px; }}
  .route {{ font-size: 44px; font-weight: 800; letter-spacing: 0.5px; }}
  .route .arrow {{ color: {t["accent"]}; margin: 0 10px; }}
  .subtitle {{ font-size: 18px; color: {t["secondary"]}; margin-top: 6px; font-weight: 500; }}
  .period-label {{ font-size: 20px; font-weight: 700; color: {t["accent"]}; text-align: right; }}
  .stops-badge {{
    font-size: 12px; font-weight: 800; padding: 3px 10px; border-radius: 999px;
    background: {t["muted"]}; color: {t["surface"]};
  }}
  .stops-badge.direct {{ background: {t["badge_bg"]}; color: {t["badge_text"]}; }}
  .flight-strip {{ display: flex; align-items: center; gap: 20px; background: {t["strip_bg"]}; border-radius: 10px; padding: 10px 22px; margin-bottom: 24px; font-size: 14px; color: {t["secondary"]}; }}
  .flight-strip b {{ color: {t["primary"]}; }}
  .flight-strip .sep {{ color: {t["muted"]}; margin: 0 8px; }}
  .airline-block {{ display: flex; align-items: center; gap: 10px; flex-shrink: 0; }}
  .airline-logo {{ height: 34px; width: 34px; object-fit: contain; border-radius: 6px; background: #fff; padding: 3px; flex-shrink: 0; }}
  .airline-name {{ font-size: 16px; font-weight: 800; color: {t["accent"]}; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  th {{ font-size: 14px; color: {t["muted"]}; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; padding-bottom: 10px; text-align: center; }}
  .cell {{ height: 118px; border: 2px solid {t["surface"]}; border-radius: 10px; text-align: center; vertical-align: middle; padding: 8px 4px; }}
  .cell.empty {{ background: transparent; border: none; }}
  .cell.unavailable {{ background: {t["unavail_bg"]}; color: {t["muted"]}; }}
  .daynum {{ font-size: 15px; font-weight: 700; opacity: 0.85; margin-bottom: 4px; }}
  .fare {{ font-size: 19px; font-weight: 800; }}
  .fare-na {{ font-size: 16px; color: {t["muted"]}; }}
  .cell-flight {{ display: flex; align-items: center; justify-content: center; gap: 5px; margin-top: 4px; }}
  .cell-logo {{ height: 16px; width: 16px; object-fit: contain; border-radius: 3px; background: #fff; padding: 1px; flex-shrink: 0; }}
  .cell-flight-text {{ font-size: 9px; font-weight: 700; opacity: 0.92; line-height: 1.3; text-align: left; }}
  .badge {{ font-size: 9px; background: {t["badge_bg"]}; color: {t["badge_text"]}; padding: 2px 6px; border-radius: 8px; font-weight: 800; vertical-align: middle; }}
  .legend {{ display: flex; align-items: center; gap: 18px; margin-top: 28px; padding-top: 20px; border-top: 1px solid {t["grid"]}; }}
  .legend-scale {{ display: flex; align-items: center; gap: 4px; font-size: 13px; color: {t["secondary"]}; }}
  .swatch {{ width: 22px; height: 14px; border-radius: 3px; }}
  .summary {{ margin-left: auto; text-align: right; font-size: 14px; color: {t["secondary"]}; }}
  .summary b {{ color: {t["primary"]}; font-size: 16px; }}
  .footer {{ margin-top: 20px; font-size: 12px; color: {t["muted"]}; text-align: center; }}
</style>
</head>
<body>
<div class="poster">
  {f'<div class="brand-bar"><img src="data:image/png;base64,{logo_b64}" alt="logo"></div>' if show_logo else ''}
  <div class="header">
    <div>
      <div class="route">{seg["origin"]} <span class="arrow">&#9992;</span> {seg["dest"]}</div>
      <div class="subtitle">{seg["origin_label"]} &rarr; {seg["dest_label"]} &middot; One-way Economy Fares</div>
    </div>
    <div>
      <div class="period-label">{seg["period_label"]}</div>
    </div>
  </div>
  <div class="flight-strip">{seg["airline_block_html"]}{seg["flight_strip"]}</div>
  <table>
    <thead><tr><th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th></tr></thead>
    <tbody>{seg["table_rows"]}</tbody>
  </table>
  {f'''<div class="legend">
    <div class="legend-scale">
      <span>Cheaper</span>
      <span class="swatch" style="background:{t["ramp"][0]}"></span>
      <span class="swatch" style="background:{t["ramp"][2]}"></span>
      <span class="swatch" style="background:{t["ramp"][4]}"></span>
      <span class="swatch" style="background:{t["ramp"][6]}"></span>
      <span>Pricier</span>
    </div>
    <div class="summary">{seg["summary"]}</div>
  </div>''' if seg["has_fares"] else ''}
  <div class="footer">Fares in INR, per adult, subject to availability &amp; change. Dates with no fares are left blank.</div>
</div>
</body>
</html>
"""
    return html


def build_html_multi(segments, dates, theme="sunset", show_logo=True, poster_title=None):
    """
    Several routes' fares stacked in ONE poster image, each as its own
    compact mini-calendar sharing the same date range - e.g. three routes
    all flying into (or out of) the same hub city.

    segments: list of {"origin", "dest", "origin_label", "dest_label",
        "priced_days"} dicts, one per route, rendered top to bottom in
        the order given.
    dates: the shared date range every segment's grid spans.
    """
    t = THEMES[theme]
    logo_b64 = _logo_b64(theme) if show_logo else None

    built = [
        _build_segment(s["origin"], s["dest"], dates, s["priced_days"], theme,
                        s.get("origin_label"), s.get("dest_label"))
        for s in segments
    ]

    if dates:
        first, last = dates[0], dates[-1]
        period_label = f"{first.day} {MONTH_ABBR[first.month]} &ndash; {last.day} {MONTH_ABBR[last.month]} {last.year}"
    else:
        period_label = ""

    segments_html = "\n".join(f'''
  <div class="segment">
    <div class="seg-header">
      <div class="seg-route">{seg["origin"]} <span class="arrow">&#9992;</span> {seg["dest"]}</div>
      <div class="seg-subtitle">{seg["origin_label"]} &rarr; {seg["dest_label"]}</div>
    </div>
    <div class="flight-strip">{seg["airline_block_html"]}{seg["flight_strip"]}</div>
    <table>
      <thead><tr><th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th></tr></thead>
      <tbody>{seg["table_rows"]}</tbody>
    </table>
    <div class="seg-summary{'' if seg["has_fares"] else ' na'}">{seg["summary"]}</div>
  </div>''' for seg in built)

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Fares</title>
<style>
  * {{ box-sizing: border-box; margin:0; padding:0; }}
  body {{ width: 1200px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: {t["page_bg"]}; color: {t["primary"]}; }}
  .poster {{ width: 1200px; background: {t["surface"]}; padding: 48px 64px 40px; }}
  .brand-bar {{ display: flex; justify-content: flex-end; margin-bottom: 16px; }}
  .brand-bar img {{ height: 42px; width: auto; }}
  .poster-header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 3px solid {t["border"]}; padding-bottom: 16px; margin-bottom: 28px; }}
  .poster-title {{ font-size: 32px; font-weight: 800; letter-spacing: 0.5px; }}
  .period-label {{ font-size: 18px; font-weight: 700; color: {t["accent"]}; text-align: right; }}
  .segment {{ margin-bottom: 32px; padding-bottom: 32px; border-bottom: 1px solid {t["grid"]}; }}
  .segment:last-child {{ margin-bottom: 0; padding-bottom: 0; border-bottom: none; }}
  .seg-header {{ display: flex; align-items: baseline; gap: 14px; margin-bottom: 12px; }}
  .seg-route {{ font-size: 26px; font-weight: 800; letter-spacing: 0.3px; }}
  .seg-route .arrow {{ color: {t["accent"]}; margin: 0 6px; }}
  .seg-subtitle {{ font-size: 14px; color: {t["secondary"]}; font-weight: 500; }}
  .stops-badge {{
    font-size: 11px; font-weight: 800; padding: 3px 9px; border-radius: 999px;
    background: {t["muted"]}; color: {t["surface"]};
  }}
  .stops-badge.direct {{ background: {t["badge_bg"]}; color: {t["badge_text"]}; }}
  .flight-strip {{ display: flex; align-items: center; gap: 16px; background: {t["strip_bg"]}; border-radius: 10px; padding: 8px 18px; margin-bottom: 14px; font-size: 12px; color: {t["secondary"]}; }}
  .flight-strip b {{ color: {t["primary"]}; }}
  .flight-strip .sep {{ color: {t["muted"]}; margin: 0 6px; }}
  .airline-block {{ display: flex; align-items: center; gap: 8px; flex-shrink: 0; }}
  .airline-logo {{ height: 26px; width: 26px; object-fit: contain; border-radius: 5px; background: #fff; padding: 2px; flex-shrink: 0; }}
  .airline-name {{ font-size: 13px; font-weight: 800; color: {t["accent"]}; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  th {{ font-size: 11px; color: {t["muted"]}; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; padding-bottom: 6px; text-align: center; }}
  .cell {{ height: 78px; border: 2px solid {t["surface"]}; border-radius: 8px; text-align: center; vertical-align: middle; padding: 5px 3px; }}
  .cell.empty {{ background: transparent; border: none; }}
  .cell.unavailable {{ background: {t["unavail_bg"]}; color: {t["muted"]}; }}
  .daynum {{ font-size: 12px; font-weight: 700; opacity: 0.85; margin-bottom: 2px; }}
  .fare {{ font-size: 15px; font-weight: 800; }}
  .fare-na {{ font-size: 13px; color: {t["muted"]}; }}
  .cell-flight {{ display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: 2px; }}
  .cell-logo {{ height: 12px; width: 12px; object-fit: contain; border-radius: 2px; background: #fff; padding: 1px; flex-shrink: 0; }}
  .cell-flight-text {{ font-size: 7px; font-weight: 700; opacity: 0.92; line-height: 1.2; text-align: left; }}
  .badge {{ font-size: 8px; background: {t["badge_bg"]}; color: {t["badge_text"]}; padding: 1px 5px; border-radius: 7px; font-weight: 800; vertical-align: middle; }}
  .seg-summary {{ margin-top: 10px; text-align: right; font-size: 12px; color: {t["secondary"]}; }}
  .seg-summary b {{ color: {t["primary"]}; font-size: 13px; }}
  .seg-summary.na {{ color: {t["muted"]}; }}
  .footer {{ margin-top: 8px; font-size: 12px; color: {t["muted"]}; text-align: center; }}
</style>
</head>
<body>
<div class="poster">
  {f'<div class="brand-bar"><img src="data:image/png;base64,{logo_b64}" alt="logo"></div>' if show_logo else ''}
  <div class="poster-header">
    <div class="poster-title">{poster_title or "Fares"}</div>
    <div class="period-label">{period_label}</div>
  </div>
  {segments_html}
  <div class="footer">Fares in INR, per adult, subject to availability &amp; change. Dates with no fares are left blank.</div>
</div>
</body>
</html>
"""
    return html


def render_png(html, out_path=None):
    """Renders the poster HTML to PNG bytes (or writes to out_path if given)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=2)
        page.set_content(html, wait_until="networkidle")
        if out_path:
            page.locator(".poster").screenshot(path=out_path)
            browser.close()
            return out_path
        else:
            data = page.locator(".poster").screenshot()
            browser.close()
            return data


def build(origin, dest, dates, priced_days, theme="sunset", origin_label=None, dest_label=None, show_logo=True):
    html = build_html(origin, dest, dates, priced_days, theme, origin_label, dest_label, show_logo)
    return render_png(html)


def build_multi(segments, dates, theme="sunset", show_logo=True, poster_title=None):
    html = build_html_multi(segments, dates, theme, show_logo, poster_title)
    return render_png(html)
