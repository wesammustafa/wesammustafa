#!/usr/bin/env python3
"""Generate a cumulative star-history SVG for a GitHub repo. Stdlib only.

Usage: GITHUB_TOKEN=... python3 scripts/star_chart.py owner/repo out.svg
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
W, H = 800, 400
ML, MR, MT, MB = 64, 32, 56, 44  # margins
LINE = "#3987e5"  # passes 3:1 contrast on both light and dark surfaces
MUTED = "#898781"


# GraphQL, not REST: the Actions GITHUB_TOKEN gets 403 "Resource not accessible
# by integration" on other repos' REST /stargazers, but GraphQL reads them fine.
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


def render(repo, dates):
    points = downsample([(d.timestamp(), i + 1) for i, d in enumerate(dates)])
    t0, t1 = points[0][0], points[-1][0]
    total = points[-1][1]
    step, ymax = nice_ticks(total)
    tspan = max(t1 - t0, 1)

    def x(t):
        return ML + (t - t0) / tspan * (W - ML - MR)

    def y(v):
        return H - MB - v / ymax * (H - MT - MB)

    path = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in points)
    area = f"{ML},{y(0):.1f} {path} {x(t1):.1f},{y(0):.1f}"

    grid, yticks = [], []
    for i in range(1, round(ymax / step) + 1):
        v = step * i
        gy = y(v)
        grid.append(
            f'<line x1="{ML}" y1="{gy:.1f}" x2="{W - MR}" y2="{gy:.1f}" '
            f'stroke="{MUTED}" stroke-opacity=".22"/>'
        )
        yticks.append(
            f'<text x="{ML - 8}" y="{gy + 4:.1f}" text-anchor="end">{v:,.0f}</text>'
        )

    fmt = "%b %d" if tspan < 120 * 86400 else "%b %Y"
    xticks = []
    for i in range(5):
        t = t0 + tspan * i / 4
        label = datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)
        anchor = ("start", "middle", "middle", "middle", "end")[i]
        xticks.append(
            f'<text x="{x(t):.1f}" y="{H - MB + 20}" text-anchor="{anchor}">{label}</text>'
        )

    ex, ey = x(t1), y(total)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Star history of {repo}: {total:,} stars">
<style>
text{{font:12px system-ui,-apple-system,'Segoe UI',sans-serif;fill:{MUTED}}}
.title{{font-size:15px;font-weight:600;fill:#52514e}}
.count{{font-weight:600;fill:{LINE}}}
@media (prefers-color-scheme:dark){{.title{{fill:#c3c2b7}}}}
</style>
<text class="title" x="{ML}" y="26">{repo} — star history</text>
{"".join(grid)}
<line x1="{ML}" y1="{y(0):.1f}" x2="{W - MR}" y2="{y(0):.1f}" stroke="{MUTED}" stroke-opacity=".5"/>
{"".join(yticks)}{"".join(xticks)}
<polygon points="{area}" fill="{LINE}" fill-opacity=".08"/>
<polyline points="{path}" fill="none" stroke="{LINE}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{LINE}"/>
<text class="count" x="{ex - 10:.1f}" y="{ey - 10:.1f}" text-anchor="end">{total:,} ⭐</text>
</svg>
"""


def selftest():
    assert nice_ticks(2303) == (500, 2500) and nice_ticks(343) == (100, 400)
    assert nice_ticks(9) == (2, 10) and nice_ticks(1) == (1, 1)
    pts = downsample([(i, i) for i in range(1000)])
    assert len(pts) == 240 and pts[0] == (0, 0) and pts[-1] == (999, 999)
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    svg = render("o/r", [base.replace(day=d) for d in range(1, 20)])
    assert svg.startswith("<svg") and "19 ⭐" in svg
    print("selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit()
    repo, out = sys.argv[1], sys.argv[2]
    dates = fetch_star_dates(repo, os.environ["GITHUB_TOKEN"])
    with open(out, "w") as f:
        f.write(render(repo, dates))
    print(f"{out}: {len(dates):,} stars")
