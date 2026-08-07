"""
Renders a themed monthly fare-calendar poster PNG from a fares dict
(as returned by airiq_client.scrape_month), with an optional flat
markup applied to every fare before rendering.
"""
import os
import calendar
import base64
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

MONTH_NAMES = list(calendar.month_name)

THEMES = {
    "sky": dict(
        page_bg="#f9f9f7", surface="#fcfcfb", primary="#0b0b0b", secondary="#52514e",
        muted="#898781", accent="#256abf", border="#0b0b0b", grid="#e1e0d9",
        ramp=["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
        unavail_bg="#f0efec", soldout_bg="#ffffff", soldout_border="#e1e0d9",
        soldout_text="#d03b3b", badge_bg="#0ca30c", badge_text="#ffffff", strip_bg="#f0efec",
        logo="logo_navy.png",
    ),
    "ocean": dict(
        page_bg="#0a1628", surface="#0f2138", primary="#ffffff", secondary="#9fb4c9",
        muted="#6b8299", accent="#4dd4c0", border="#ffffff", grid="#1e3552",
        ramp=["#ffe8a3", "#ffd166", "#f5b942", "#e8a020", "#c67f0e", "#9c6108", "#6e4405"],
        unavail_bg="#16232f", soldout_bg="#1a1220", soldout_border="#3a1414",
        soldout_text="#ff8a7a", badge_bg="#4dd4c0", badge_text="#0b0b0b", strip_bg="#16283e",
        logo="logo_white.png",
    ),
    "sunset": dict(
        page_bg="#fdf3ea", surface="#fffaf3", primary="#2b1c12", secondary="#7a6455",
        muted="#a1897a", accent="#d85f31", border="#2b1c12", grid="#eddcc8",
        ramp=["#ffe4d6", "#ffc9ab", "#ffa679", "#f3804f", "#d85f31", "#b0431e", "#7a2c10"],
        unavail_bg="#f5ebe0", soldout_bg="#fffaf3", soldout_border="#eddcc8",
        soldout_text="#c0392b", badge_bg="#2e8b57", badge_text="#ffffff", strip_bg="#f5e8da",
        logo="logo_coral.png",
    ),
}

CITY_LABELS = {}  # populated lazily by app.py from airiq_client.list_origins()


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


def _flight_summary(fares):
    """Pull representative flight_no/time/baggage/airline from the first day with data."""
    for info in fares.values():
        if info.get("status") == "ok":
            c = info["cheapest"]
            return {
                "airline": c.get("airline", "").strip(),
                "flight_no": c.get("flight_no", "").strip() if c.get("flight_no") else None,
                "time": c.get("time"),
                "baggage": c.get("baggage"),
            }
    return None


def build_html(origin, dest, year, month, fares, markup=0, theme="sunset",
                origin_label=None, dest_label=None, show_logo=True):
    t = THEMES[theme]
    logo_b64 = _logo_b64(theme) if show_logo else None

    # apply markup
    fares = {
        int(day): (
            {**info, "cheapest": {**info["cheapest"], "fare_inr": info["cheapest"]["fare_inr"] + markup}}
            if info.get("status") == "ok" else info
        )
        for day, info in fares.items()
    }

    ok_fares = [v["cheapest"]["fare_inr"] for v in fares.values() if v.get("status") == "ok"]
    has_fares = bool(ok_fares)
    min_fare = min(ok_fares) if has_fares else 0
    max_fare = max(ok_fares) if has_fares else 0
    cheapest_day = (
        min(fares, key=lambda d: fares[d]["cheapest"]["fare_inr"] if fares[d].get("status") == "ok" else float("inf"))
        if has_fares else None
    )

    def color_for(fare):
        ramp = t["ramp"]
        if max_fare == min_fare:
            idx = 0
        else:
            idx = round((fare - min_fare) / (max_fare - min_fare) * (len(ramp) - 1))
        return ramp[idx]

    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)

    cells_html = []
    for week in weeks:
        row = []
        for date in week:
            if date.month != month:
                row.append('<td class="cell empty"></td>')
                continue
            day = date.day
            info = fares.get(day, {"status": "past_or_unavailable"})
            status = info.get("status")
            if status == "ok":
                fare = info["cheapest"]["fare_inr"]
                bg = color_for(fare)
                fg = _text_for(bg, "#ffffff", t["primary"])
                badge = ' <span class="badge">BEST</span>' if day == cheapest_day else ""
                row.append(f'''<td class="cell filled" style="background:{bg};color:{fg}">
                    <div class="daynum">{day}{badge}</div>
                    <div class="fare">&#8377;{fare:,.0f}</div>
                </td>''')
            elif status == "sold_out":
                row.append(f'''<td class="cell soldout">
                    <div class="daynum">{day}</div>
                    <div class="fare-soldout">Sold Out</div>
                </td>''')
            else:
                row.append(f'''<td class="cell unavailable">
                    <div class="daynum">{day}</div>
                    <div class="fare-na">&mdash;</div>
                </td>''')
        cells_html.append("<tr>" + "".join(row) + "</tr>")

    table_rows = "\n".join(cells_html)

    flight = _flight_summary(fares)
    if flight:
        parts = []
        if flight["flight_no"]:
            parts.append(f'Flight <b>{flight["flight_no"]}</b>')
        if flight["time"]:
            dep_arr = flight["time"].split(" - ")
            if len(dep_arr) == 2:
                parts.append(f'Dep <b>{dep_arr[0]}</b> &rarr; Arr <b>{dep_arr[1]}</b>')
        if flight["baggage"]:
            parts.append(f'Baggage <b>{flight["baggage"]}</b>')
        flight_strip = '<span class="sep">&bull;</span>'.join(f'<span>{p}</span>' for p in parts)
        airline_tag = flight["airline"] or ""
    else:
        flight_strip = "<span>No live flight data for this route/month</span>"
        airline_tag = ""

    origin_label = origin_label or origin
    dest_label = dest_label or dest
    month_label = f"{MONTH_NAMES[month].upper()} {year}"

    summary = (
        f'Best fare: <b>&#8377;{min_fare:,.0f}</b> on {MONTH_NAMES[month][:3]} {cheapest_day} '
        f'&nbsp;|&nbsp; Range: &#8377;{min_fare:,.0f} &ndash; &#8377;{max_fare:,.0f}'
        if has_fares else "No fares found for this route/month"
    )
    markup_note = f' (incl. +&#8377;{markup:,.0f} markup)' if markup else ""

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{origin}-{dest} {month_label} Fares</title>
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
  .month-label {{ font-size: 22px; font-weight: 700; color: {t["accent"]}; text-align: right; }}
  .airline-tag {{ font-size: 15px; color: {t["muted"]}; text-align: right; margin-top: 4px; }}
  .flight-strip {{ display: flex; align-items: center; gap: 28px; background: {t["strip_bg"]}; border-radius: 10px; padding: 14px 22px; margin-bottom: 24px; font-size: 14px; color: {t["secondary"]}; }}
  .flight-strip b {{ color: {t["primary"]}; }}
  .flight-strip .sep {{ color: {t["muted"]}; margin: 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  th {{ font-size: 14px; color: {t["muted"]}; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; padding-bottom: 10px; text-align: center; }}
  .cell {{ height: 108px; border: 2px solid {t["surface"]}; border-radius: 10px; text-align: center; vertical-align: middle; padding: 8px 4px; }}
  .cell.empty {{ background: transparent; border: none; }}
  .cell.unavailable {{ background: {t["unavail_bg"]}; color: {t["muted"]}; }}
  .cell.soldout {{ background: {t["soldout_bg"]}; border: 2px solid {t["soldout_border"]}; }}
  .daynum {{ font-size: 15px; font-weight: 700; opacity: 0.85; margin-bottom: 6px; }}
  .fare {{ font-size: 21px; font-weight: 800; }}
  .fare-soldout {{ font-size: 13px; font-weight: 700; color: {t["soldout_text"]}; }}
  .fare-na {{ font-size: 16px; color: {t["muted"]}; }}
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
      <div class="route">{origin} <span class="arrow">&#9992;</span> {dest}</div>
      <div class="subtitle">{origin_label} &rarr; {dest_label} &middot; One-way Economy Fares{markup_note}</div>
    </div>
    <div>
      <div class="month-label">{month_label}</div>
      <div class="airline-tag">{airline_tag}</div>
    </div>
  </div>
  <div class="flight-strip">{flight_strip}</div>
  <table>
    <thead><tr><th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
  <div class="legend">
    <div class="legend-scale">
      <span>Cheaper</span>
      <span class="swatch" style="background:{t["ramp"][0]}"></span>
      <span class="swatch" style="background:{t["ramp"][2]}"></span>
      <span class="swatch" style="background:{t["ramp"][4]}"></span>
      <span class="swatch" style="background:{t["ramp"][6]}"></span>
      <span>Pricier</span>
    </div>
    <div class="summary">{summary}</div>
  </div>
  <div class="footer">Fares in INR, per adult, subject to availability &amp; change. Sold out / unavailable dates shown as marked.</div>
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


def build(origin, dest, year, month, fares, markup=0, theme="sunset", origin_label=None, dest_label=None, show_logo=True):
    html = build_html(origin, dest, year, month, fares, markup, theme, origin_label, dest_label, show_logo)
    return render_png(html)
