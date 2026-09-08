# Are Credit Spreads Blowing Out?

Credit is the cycle's confirmer; this page keeps a century of score.
Live at [credit.chrislawrence.ca](https://credit.chrislawrence.ca).

No framework, no build step, no package manager. Plain HTML, CSS and vanilla JS on an
nginx front, with a stdlib-Python updater sidecar. Part of the economic tracker
collection (diesel, debt, jobs, yield, housing) on the shared
[`econ-core`](https://github.com/Lawrence908/econ-core/blob/main/CONTRACT.md) series contract.

## Layout

```
src/index.html    markup, styling, the TimeChart canvas engine, every render function
data/series.json  machine-fetched, rewritten wholesale each run, never hand-edited
data/meta.json    curated; deliberately near-empty (no hand-entered figure exists here)
data/recessions.json  vendored from econ-core; never edited here
api/server.py     updater, blowout engine, and read-only status API
api/econcore.py   vendored, stamped copy of the shared fetchers
```

## The series

Nineteen series on the econ-core contract. The century spine is the Moody's quality
spread, Baa minus Aaa, monthly since January 1919, computed here with both raw legs
shipped and a daily continuation from 1986. Against Treasuries, BAA10Y and AAA10Y as
published. Financial conditions are the Chicago Fed NFCI and ANFCI, weekly since 1971.

Two upstream facts this repo records so nobody rediscovers them:

- **The ICE BofA OAS series (high yield, IG, CCC) serve only a sliding ~3-year window
  on FRED**, identical keyed and keyless (probed 2026-09-07), though the indexes reach
  1996. They ship as the recent tape with the truncation stated everywhere they appear.
- **Canada's spread record is real but closed**: the MYW 10-industrials corporate
  average (v122490) ends December 1988 and prime corporate paper (v122491) ends
  December 2018 with the CDOR era. Both are fetched from WDS as closed history, drawn
  against the live GoC long average (v122487, 1936 onward) and 3-month bills, ending
  where they ended. Live Canadian corporate spreads are paywalled; the gap is shown.

## The blowout table, two clocks

Housing's two-clock design, inverted. Each episode is dated at its TROUGH (the tightest
3-month average in the prior two years, peak complacency, credit's early clock) and its
ALARM (the first month at least 0.40pp above that trailing low, two months sustained,
six-month merge). An NBER peak from 18 months before the alarm to 12 months after the
last signal month is assigned to the nearest episode; a peak before the alarm scores
coincident.

On current data that yields 21 episodes since 1919 and the family's thesis, computed:
the complacency troughs led the 12 attached recessions by a median of 11 months, while
**the alarm led exactly one of them** (the 1981 second dip, by three months). Everything
else it confirmed after the fact, including the Depression, where the spread did not
cross the threshold until November 1930. The nine false alarms all have names (1966
crunch, Continental Illinois 1984, the LTCM aftermath, 2011 euro stress, the 2015-16
energy bust, the 2018-19 selloff, the 2022 rate shock), and the five recessions credit
never flagged (1926, 1945, 1948, 1953, 1960) were the mild ones. The rule was tuned
once against the canonical record (0.50 missed 1990; a 6-month coincident window
misread the Depression as a false positive), then frozen; it ships in the payload and
prints beside the table.

## The updater

```bash
docker exec credit-updater python /app/server.py --once      # dry run
docker exec credit-updater python /app/server.py --refresh   # what cron runs
```

Host crontab, daily at 07:10 Pacific, log bounded monthly. Guardrails as everywhere in
the family: stale or shrunken upstreams kept, failures carry forward with the error
recorded, revisions logged to `changelog.jsonl`. Moody's yields barely revise; the
NFCI is re-estimated in full weekly, so most log entries are the Chicago Fed's normal
behaviour, and the OAS window sliding forward is not logged because dropping old
observations is not a revision.

Fetch policy is econ-core's: keyless first (FRED CSV, StatCan WDS), keyed FRED as
fallback (`FRED_API_KEY` in `.env`, gitignored).

## Provenance

Assembled with Claude, made by Anthropic. Measured spreads, published indexes, and
computed history with the rule printed. No forecasts, no fair-value calls.

## Data and attribution

The MIT licence covers this repository's code. It does not cover the data, which is not
mine: every series belongs to the body that publishes it and carries that body's own terms.
Each series names its `source` and `source_url` so the original is always one click away.

**Restricted series.** The ICE BofA option-adjusted spreads (`BAMLC0A0CM`,
`BAMLH0A0HYM2`, `BAMLH0A3HYC`) are copyrighted by ICE Data Indices, LLC, whose notice
reads: *Reproduction of this data in any form is prohibited except with the prior written
permission of ICE Data Indices.* FRED tags them **Copyrighted: Pre-Approval Required**.
The Moody's seasoned corporate bond yields are likewise copyrighted and carry a citation
requirement. Both are used here for non-commercial educational purposes; any other use
needs clearing with the copyright holder directly, and neither FRED nor this repository
can grant that permission.

The Chicago Fed's financial conditions indexes are works of the Federal Reserve and are
not subject to copyright.

Statistics Canada data is used under the [Open Licence](https://www.statcan.gc.ca/en/reference/licence),
which requires this acknowledgement: *Adapted from Statistics Canada, the tables and vectors
named per series above. This does not constitute an endorsement by Statistics Canada of this
product.*

Recession bands come from econ-core: the US from the NBER chronology via FRED `USREC`,
Canada from the C.D. Howe Institute Business Cycle Council chronology.

Series reached through FRED are redistributed by the Federal Reserve Bank of St. Louis
under [its terms of use](https://fred.stlouisfed.org/legal/), which ask that you cite the
original source and note that it was accessed via FRED.
