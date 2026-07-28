#!/usr/bin/env python3
"""Generate cumulative star-history SVGs for a GitHub repo. Stdlib only.

Writes a light and a dark file from one API pass; the README selects between
them with a picture element.

Usage: python3 scripts/star_chart.py owner/repo light.svg dark.svg
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"

# Resolved off the script, not the CWD: a run from elsewhere would otherwise
# see no history, start a new one, and overwrite years of data with one point.
HISTORY = Path(__file__).resolve().parent.parent / "assets" / "star-history.json"

# Geometry. Type is sized for the README's width="60%" (~0.67 downscale), so
# what look like huge sizes here land at ~10-31px on the rendered page.
W, H = 800, 424
PAD = 12  # header gutter
ML, MR = 76, 26  # plot left/right
PT, PB = 144, 376  # plot top / baseline
HERO_Y = 94  # baseline of the headline star count
DELTA_DAYS = 30

# Baked per file rather than a prefers-color-scheme media query: that query
# reads the OS setting, which an SVG loaded as an image cannot reconcile with
# GitHub's own theme toggle, so a reader on GitHub-dark plus OS-light got white
# ink on a white card. The README picks the file via a picture element instead.
# Values are the reference data-viz palette, validated by the skill's
# validate_palette.js against GitHub's real surfaces (#ffffff and #0d1117).
THEMES = {
    "light": {
        "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "series": "#2a78d6", "good": "#006300",
    },
    "dark": {
        "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "series": "#3987e5", "good": "#0ca30c",
    },
}


# Reads stargazers_count off the public repo record instead of paginating
# /stargazers. That endpoint carries the starredAt history but needs auth, and
# the Actions github.token cannot read another repo's stargazers ("Resource not
# accessible by integration" over both REST and GraphQL), which forced a
# hand-rolled PAT secret. PATs expire, and when this one did the daily job 401'd
# every morning. The repo record is public, so the history is accumulated in
# HISTORY instead: one unauthenticated request a day, nothing to rotate.
def fetch_count(repo, token=None):
    headers = {"User-Agent": "star-chart", "Accept": "application/vnd.github+json"}
    if token:  # optional: only raises the 60/hr anonymous rate limit
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        req = urllib.request.Request(f"{API}/repos/{repo}", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)["stargazers_count"]
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403) and headers.pop("Authorization", None):
                # Token absent, expired or wrongly scoped. The data is public,
                # so this must not be fatal -- that was the original outage.
                print(f"HTTP {e.code} with token, retrying anonymously: {body}",
                      file=sys.stderr)
                continue
            if attempt == 3 or e.code not in (403, 429, 500, 502, 503):
                sys.exit(f"HTTP {e.code}\n{body}")
            wait = int(e.headers.get("Retry-After") or 15 * 2**attempt)
            print(f"HTTP {e.code}, retry in {wait}s: {body}", file=sys.stderr)
            time.sleep(wait)


def record(repo, count):
    """Store today's count, return the whole series as (timestamp, count).

    Keyed by UTC date so a re-run overwrites rather than appends: manual
    dispatches stay idempotent.
    """
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    series = history.setdefault(repo, {})
    series[datetime.now(timezone.utc).strftime("%Y-%m-%d")] = count
    # indent=0 keeps one day per line, so the daily commit is a one-line diff.
    HISTORY.write_text(json.dumps(history, indent=0, sort_keys=True) + "\n")
    return [
        (datetime.strptime(d, "%Y-%m-%d")
         .replace(tzinfo=timezone.utc).timestamp(), c)
        for d, c in sorted(series.items())
    ]


def nice_ticks(total):
    """Round tick step, largest nice number <= total/3; axis max = step * ceil."""
    target = max(total, 3) / 3
    mag = 10 ** math.floor(math.log10(target))
    step = next(m * mag for m in (5, 2, 1) if m * mag <= target)
    return step, step * math.ceil(total / step)


def downsample(points, cap=240):
    if len(points) <= cap:
        return points
    n = len(points)
    return [points[round(i * (n - 1) / (cap - 1))] for i in range(cap)]


def star_path(cx, cy, r):
    """Five-point star. A drawn path, not '⭐': SVG loaded via <img> has no
    guaranteed emoji font, and the glyph rendered at a different size per OS."""
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.382
        pts.append(f"{cx + rad * math.cos(a):.1f},{cy + rad * math.sin(a):.1f}")
    return "M" + "L".join(pts) + "Z"


def recent_gain(series, days=DELTA_DAYS):
    """Growth over the trailing window, or 0 if the history is shorter than it:
    a series that does not span the window cannot honestly label one."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    if series[0][0] > cutoff:
        return 0
    return series[-1][1] - [c for t, c in series if t <= cutoff][-1]


def render(repo, series, theme="light"):
    c = THEMES[theme]
    total = series[-1][1]
    gain = recent_gain(series)  # off the full series; downsampling loses days
    points = downsample(series)
    t0, t1 = points[0][0], points[-1][0]
    # Scaled to the peak, not the last value: unstars can dip the tail, and a
    # ymax below an earlier peak would draw that peak outside the plot box.
    step, ymax = nice_ticks(max(v for _, v in series))
    tspan = max(t1 - t0, 1)

    def x(t):
        return ML + (t - t0) / tspan * (W - ML - MR)

    def y(v):
        return PB - v / ymax * (PB - PT)

    path = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in points)
    area = f"{ML},{PB} {path} {x(t1):.1f},{PB}"

    grid, yticks = [], []
    for i in range(round(ymax / step) + 1):
        v, gy = step * i, y(step * i)
        if i:  # 0 gets the baseline rule below instead of a gridline
            grid.append(
                f'<line class="grid" x1="{ML}" y1="{gy:.1f}" x2="{W - MR}" y2="{gy:.1f}"/>'
            )
        yticks.append(
            f'<text class="tick" x="{ML - 12}" y="{gy + 5:.1f}" text-anchor="end">{v:,.0f}</text>'
        )

    fmt = "%b %d" if tspan < 120 * 86400 else "%b %Y"
    xticks = []
    for i in range(5):
        t = t0 + tspan * i / 4
        label = datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)
        anchor = ("start", "middle", "middle", "middle", "end")[i]
        xticks.append(
            f'<text class="tick" x="{x(t):.1f}" y="{PB + 32}" text-anchor="{anchor}">{label}</text>'
        )

    delta = (
        f'<text class="delta" x="{W - PAD}" y="{HERO_Y}" text-anchor="end">'
        f"&#8593; {gain:,} in the last {DELTA_DAYS} days</text>"
        if gain > 0  # net-negative months exist; "up -4" would be nonsense
        else ""
    )
    ex, ey = x(t1), y(total)
    label = f"Star history of {repo}: {total:,} stars"
    if gain > 0:
        label += f", up {gain:,} in the last {DELTA_DAYS} days"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{label}">
<style>
text{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;fill:{c["muted"]}}}
.repo{{font-size:18px;fill:{c["ink2"]}}}
.hero{{font-size:46px;font-weight:600;fill:{c["ink"]};letter-spacing:-.02em}}
.unit{{font-size:18px;fill:{c["muted"]}}}
.delta{{font-size:16px;font-weight:500;fill:{c["good"]}}}
.tick{{font-size:16px;font-variant-numeric:tabular-nums}}
.grid{{stroke:{c["grid"]};stroke-width:1}}
.axis{{stroke:{c["axis"]};stroke-width:1}}
.mark{{fill:{c["series"]}}}
.line{{fill:none;stroke:{c["series"]};stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}}
.g0{{stop-color:{c["series"]};stop-opacity:.22}}
.g1{{stop-color:{c["series"]};stop-opacity:0}}
</style>
<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" class="g0"/><stop offset="1" class="g1"/>
</linearGradient></defs>
<text class="repo" x="{PAD}" y="30">{repo}</text>
<path class="mark" d="{star_path(PAD + 15, HERO_Y - 17, 15)}"/>
<text x="{PAD + 42}" y="{HERO_Y}"><tspan class="hero">{total:,}</tspan><tspan class="unit" dx="12">stars</tspan></text>
{delta}
{"".join(grid)}
<line class="axis" x1="{ML}" y1="{PB}" x2="{W - MR}" y2="{PB}"/>
{"".join(yticks)}{"".join(xticks)}
<polygon points="{area}" fill="url(#fade)"/>
<polyline class="line" points="{path}"/>
<circle class="mark" cx="{ex:.1f}" cy="{ey:.1f}" r="5"/>
</svg>
"""


def selftest():
    import re
    import xml.dom.minidom

    assert nice_ticks(2303) == (500, 2500) and nice_ticks(343) == (100, 400)
    assert nice_ticks(9) == (2, 10) and nice_ticks(1) == (1, 1)
    pts = downsample([(i, i) for i in range(1000)])
    assert len(pts) == 240 and pts[0] == (0, 0) and pts[-1] == (999, 999)

    day = 86400
    base = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
    old = [(base + i * day, i + 1) for i in range(19)]
    for theme, ink in (("light", "#0b0b0b"), ("dark", "#ffffff")):
        svg = render("o/r", old, theme)
        xml.dom.minidom.parseString(svg)  # well-formed, and entities escaped
        assert f"fill:{ink}" in svg, f"{theme} did not bake its own ink"
        assert "prefers-color-scheme" not in svg, "theme must be baked, not queried"
        assert '<tspan class="hero">19</tspan>' in svg
        assert "nan" not in svg.lower()
        assert "in the last" not in svg, "2025 data must not claim a recent gain"

        # Every plotted coordinate stays inside the plot box — catches layout
        # math that renders off-canvas, which parsing alone will not.
        for attr in re.findall(r'points="([^"]+)"', svg):
            for pt in attr.split():
                px, py = (float(n) for n in pt.split(","))
                assert 0 <= px <= W and PT <= py <= PB, f"{pt} outside plot"

    now = datetime.now(timezone.utc).timestamp()
    span = [(now - i * day, 100 - i) for i in range(90, -1, -1)]
    assert recent_gain(span) == DELTA_DAYS
    assert f"{DELTA_DAYS} in the last {DELTA_DAYS} days" in render("o/r", span)
    # A window the history does not cover must not be labelled, and a net
    # decline must not be rendered as growth.
    assert recent_gain(span[-5:]) == 0
    assert "in the last" not in render("o/r", [(now - 60 * day, 50), (now, 40)])

    # An earlier peak above the final value still has to fit the plot box.
    dip = [(now - 60 * day, 900), (now - 30 * day, 1000), (now, 700)]
    for pt in re.findall(r'points="([^"]+)"', render("o/r", dip))[0].split():
        assert PT <= float(pt.split(",")[1]) <= PB, f"{pt} outside plot"
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit()
    if len(sys.argv) != 4:
        sys.exit(f"usage: {sys.argv[0]} owner/repo out-light.svg out-dark.svg")
    repo, out_light, out_dark = sys.argv[1:4]
    series = record(repo, fetch_count(repo, os.environ.get("GITHUB_TOKEN")))
    for out, theme in ((out_light, "light"), (out_dark, "dark")):
        with open(out, "w") as f:
            f.write(render(repo, series, theme))
    print(f"{out_light} + {out_dark}: {series[-1][1]:,} stars, {len(series)} days")
