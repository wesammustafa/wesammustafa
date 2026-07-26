#!/usr/bin/env python3
"""Generate cumulative star-history SVGs for a GitHub repo. Stdlib only.

Writes a light and a dark file from one API pass; the README selects between
them with a picture element.

Usage: GITHUB_TOKEN=... python3 scripts/star_chart.py owner/repo light.svg dark.svg
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"

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


# Needs a real PAT, not the Actions github.token: that token is scoped to this
# repo, so reading another repo's stargazers 403s "Resource not accessible by
# integration" over both REST and GraphQL. CI passes the STAR_CHART_TOKEN secret
# (zero-scope classic PAT; public read is enough). Anonymous is not an option:
# /stargazers now returns 401. GraphQL over REST only because orderBy STARRED_AT
# returns the history already sorted.
QUERY = """query($owner:String!,$name:String!,$cursor:String){
  repository(owner:$owner,name:$name){
    stargazers(first:100,after:$cursor,orderBy:{field:STARRED_AT,direction:ASC}){
      pageInfo{hasNextPage endCursor}
      edges{starredAt}}}}"""


def graphql(variables, token):
    req = urllib.request.Request(
        f"{API}/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {token}", "User-Agent": "star-chart"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                body = json.load(r)
            if "errors" in body:
                sys.exit(f"GraphQL errors: {body['errors']}")
            return body["data"]
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            if attempt == 3 or e.code not in (403, 429, 500, 502, 503):
                sys.exit(f"HTTP {e.code}\n{dict(e.headers)}\n{body}")
            wait = int(e.headers.get("Retry-After") or 15 * 2**attempt)
            print(f"HTTP {e.code}, retry in {wait}s: {body}", file=sys.stderr)
            time.sleep(wait)


def fetch_star_dates(repo, token):
    owner, name = repo.split("/")
    dates, cursor = [], None
    while True:
        sg = graphql({"owner": owner, "name": name, "cursor": cursor}, token)[
            "repository"
        ]["stargazers"]
        dates += [
            datetime.fromisoformat(e["starredAt"].replace("Z", "+00:00"))
            for e in sg["edges"]
        ]
        if not sg["pageInfo"]["hasNextPage"]:
            return sorted(dates)
        cursor = sg["pageInfo"]["endCursor"]


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


def recent_gain(dates, days=DELTA_DAYS):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return sum(1 for d in dates if d >= cutoff)


def render(repo, dates, theme="light"):
    c = THEMES[theme]
    points = downsample([(d.timestamp(), i + 1) for i, d in enumerate(dates)])
    t0, t1 = points[0][0], points[-1][0]
    total = points[-1][1]
    gain = recent_gain(dates)
    step, ymax = nice_ticks(total)
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
        if gain
        else ""
    )
    ex, ey = x(t1), y(total)
    label = f"Star history of {repo}: {total:,} stars"
    if gain:
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

    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    old = [base.replace(day=d) for d in range(1, 20)]
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

    fresh = render("o/r", old + [datetime.now(timezone.utc) - timedelta(days=1)])
    assert f"1 in the last {DELTA_DAYS} days" in fresh
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit()
    if len(sys.argv) != 4:
        sys.exit(f"usage: {sys.argv[0]} owner/repo out-light.svg out-dark.svg")
    repo, out_light, out_dark = sys.argv[1:4]
    # One fetch, both themes: paginating a few thousand stargazers twice would
    # double the API cost for identical data.
    dates = fetch_star_dates(repo, os.environ["GITHUB_TOKEN"])
    for out, theme in ((out_light, "light"), (out_dark, "dark")):
        with open(out, "w") as f:
            f.write(render(repo, dates, theme))
    print(f"{out_light} + {out_dark}: {len(dates):,} stars")
