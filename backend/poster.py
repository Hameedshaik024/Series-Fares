"""
Renders a themed fare-list poster PNG covering a rolling range of dates
(typically the next 30 days from today). Input is already-priced,
already-filtered rows from pricing.pick_fare() - this module only renders,
it makes no fare-selection decisions itself.
"""
import os
import base64
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

WEEKDAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MONTH_ABBR = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

THEMES = {
    "sky": dict(
        page_bg="#f9f9f7", surface="#fcfcfb", primary="#0b0b0b", secondary="#52514e",
        muted="#898781", accent="#256abf", border="#0b0b0b", grid="#e1e0d9",
        ramp=["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
        row_bg="#fcfcfb", row_border="#e1e0d9",
        badge_bg="#0ca30c", badge_text="#ffffff",
        logo="logo_navy.png",
    ),
    "ocean": dict(
        page_bg="#0a1628", surface="#0f2138", primary="#ffffff", secondary="#9fb4c9",
        muted="#6b8299", accent="#4dd4c0", border="#ffffff", grid="#1e3552",
        ramp=["#ffe8a3", "#ffd166", "#f5b942", "#e8a020", "#c67f0e", "#9c6108", "#6e4405"],
        row_bg="#16283e", row_border="#1e3552",
        badge_bg="#4dd4c0", badge_text="#0b0b0b",
        logo="logo_white.png",
    ),
    "sunset": dict(
        page_bg="#fdf3ea", surface="#fffaf3", primary="#2b1c12", secondary="#7a6455",
        muted="#a1897a", accent="#d85f31", border="#2b1c12", grid="#eddcc8",
        ramp=["#ffe4d6", "#ffc9ab", "#ffa679", "#f3804f", "#d85f31", "#b0431e", "#7a2c10"],
        row_bg="#fffaf3", row_border="#eddcc8",
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


def build_html(origin, dest, dates, priced_days, theme="sunset",
                origin_label=None, dest_label=None, show_logo=True):
    """
    dates: full requested list of datetime.date (used only for the header's
        period label - e.g. still shown even if some of those dates ended
        up with no fare and got filtered out of priced_days).
    priced_days: list of {"date": date, "source", "flight", "base_fare",
        "final_fare"} - already filtered (no unpriced dates) and sorted
        ascending by date.
    """
    t = THEMES[theme]
    logo_b64 = _logo_b64(theme) if show_logo else None

    has_fares = bool(priced_days)
    fares = [d["final_fare"] for d in priced_days]
    min_fare = min(fares) if has_fares else 0
    max_fare = max(fares) if has_fares else 0
    cheapest_date = min(priced_days, key=lambda d: d["final_fare"])["date"] if has_fares else None

    def color_for(fare):
        ramp = t["ramp"]
        if max_fare == min_fare:
            idx = 0
        else:
            idx = round((fare - min_fare) / (max_fare - min_fare) * (len(ramp) - 1))
        return ramp[idx]

    rows_html = []
    for d in priced_days:
        date = d["date"]
        flight = d["flight"]
        fare = d["final_fare"]

        bg = color_for(fare)
        fg = _text_for(bg, "#ffffff", t["primary"])
        is_best = date == cheapest_date

        stops = flight.get("stops")
        is_direct = bool(stops) and "non" in stops.lower() and "stop" in stops.lower()
        stops_badge = ""
        if stops:
            if is_direct:
                stops_badge = '<span class="stops-badge direct">Direct</span>'
            else:
                stops_badge = f'<span class="stops-badge">{stops}</span>'

        meta_items = [stops_badge] if stops_badge else []
        time_txt = flight.get("time")
        if time_txt:
            dep_arr = time_txt.split(" - ")
            if len(dep_arr) == 2:
                meta_items.append(f'<span>{dep_arr[0]} &rarr; {dep_arr[1]}</span>')
        if flight.get("duration"):
            meta_items.append(f'<span>{flight["duration"]}</span>')
        if flight.get("baggage"):
            meta_items.append(f'<span>{flight["baggage"]}</span>')
        meta_html = '<span class="sep">&bull;</span>'.join(meta_items)

        flight_no = flight.get("flight_no") or ""
        airline = flight.get("airline") or ""

        badge_html = ' <span class="badge">BEST</span>' if is_best else ""

        rows_html.append(f'''
        <div class="row">
          <div class="row-date" style="background:{bg};color:{fg}">
            <div class="date-wd">{WEEKDAY_ABBR[date.weekday()]}</div>
            <div class="date-num">{date.day}</div>
            <div class="date-mo">{MONTH_ABBR[date.month]}</div>
          </div>
          <div class="row-flight">
            <div class="row-airline">{airline} <span class="flightno">{flight_no}</span></div>
            <div class="row-meta">{meta_html}</div>
          </div>
          <div class="row-fare">&#8377;{fare:,.0f}{badge_html}</div>
        </div>''')

    rows_block = "\n".join(rows_html) if rows_html else '<div class="empty-msg">No fares found for this route in the selected range.</div>'

    origin_label = origin_label or origin
    dest_label = dest_label or dest

    if dates:
        first, last = dates[0], dates[-1]
        period_label = f"{first.day} {MONTH_ABBR[first.month]} &ndash; {last.day} {MONTH_ABBR[last.month]} {last.year}"
    else:
        period_label = ""

    summary = (
        f'Best fare: <b>&#8377;{min_fare:,.0f}</b> on {WEEKDAY_ABBR[cheapest_date.weekday()].title()} {cheapest_date.day} {MONTH_ABBR[cheapest_date.month]} '
        f'&nbsp;|&nbsp; Range: &#8377;{min_fare:,.0f} &ndash; &#8377;{max_fare:,.0f}'
        if has_fares else "No fares found for this route in the selected range"
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{origin}-{dest} Fares</title>
<style>
  * {{ box-sizing: border-box; margin:0; padding:0; }}
  body {{ width: 1100px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: {t["page_bg"]}; color: {t["primary"]}; }}
  .poster {{ width: 1100px; background: {t["surface"]}; padding: 56px 60px 48px; }}
  .brand-bar {{ display: flex; justify-content: flex-end; margin-bottom: 24px; }}
  .brand-bar img {{ height: 46px; width: auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 3px solid {t["border"]}; padding-bottom: 20px; margin-bottom: 28px; }}
  .route {{ font-size: 44px; font-weight: 800; letter-spacing: 0.5px; }}
  .route .arrow {{ color: {t["accent"]}; margin: 0 10px; }}
  .subtitle {{ font-size: 18px; color: {t["secondary"]}; margin-top: 6px; font-weight: 500; }}
  .period-label {{ font-size: 20px; font-weight: 700; color: {t["accent"]}; text-align: right; }}
  .stops-badge {{
    font-size: 11px; font-weight: 800; padding: 2px 9px; border-radius: 999px;
    background: {t["muted"]}; color: {t["surface"]};
  }}
  .stops-badge.direct {{ background: {t["badge_bg"]}; color: {t["badge_text"]}; }}
  .rows {{ display: flex; flex-direction: column; gap: 10px; }}
  .row {{
    display: flex; align-items: center; gap: 20px;
    background: {t["row_bg"]}; border: 1px solid {t["row_border"]}; border-radius: 12px;
    padding: 12px 20px;
  }}
  .row-date {{
    flex: 0 0 64px; height: 64px; border-radius: 10px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
  }}
  .date-wd {{ font-size: 10px; font-weight: 800; letter-spacing: 0.5px; opacity: 0.85; }}
  .date-num {{ font-size: 22px; font-weight: 800; line-height: 1.1; }}
  .date-mo {{ font-size: 10px; font-weight: 700; opacity: 0.85; }}
  .row-flight {{ flex: 1 1 auto; min-width: 0; }}
  .row-airline {{ font-size: 16px; font-weight: 800; color: {t["accent"]}; }}
  .row-airline .flightno {{ font-size: 13px; font-weight: 600; color: {t["secondary"]}; margin-left: 6px; }}
  .row-meta {{ font-size: 13px; color: {t["secondary"]}; margin-top: 4px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .row-meta .sep {{ color: {t["muted"]}; }}
  .row-fare {{ flex: 0 0 auto; font-size: 22px; font-weight: 800; color: {t["primary"]}; white-space: nowrap; }}
  .badge {{ font-size: 9px; background: {t["badge_bg"]}; color: {t["badge_text"]}; padding: 2px 7px; border-radius: 8px; font-weight: 800; vertical-align: middle; margin-left: 4px; }}
  .empty-msg {{ padding: 40px; text-align: center; color: {t["muted"]}; font-size: 16px; }}
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
      <div class="subtitle">{origin_label} &rarr; {dest_label} &middot; One-way Economy Fares</div>
    </div>
    <div class="period-label">{period_label}</div>
  </div>
  <div class="rows">{rows_block}</div>
  {f'''<div class="legend">
    <div class="legend-scale">
      <span>Cheaper</span>
      <span class="swatch" style="background:{t["ramp"][0]}"></span>
      <span class="swatch" style="background:{t["ramp"][2]}"></span>
      <span class="swatch" style="background:{t["ramp"][4]}"></span>
      <span class="swatch" style="background:{t["ramp"][6]}"></span>
      <span>Pricier</span>
    </div>
    <div class="summary">{summary}</div>
  </div>''' if has_fares else ''}
  <div class="footer">Fares in INR, per adult, subject to availability &amp; change.</div>
</div>
</body>
</html>
"""
    return html


def render_png(html, out_path=None):
    """Renders the poster HTML to PNG bytes (or writes to out_path if given)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 800}, device_scale_factor=2)
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
