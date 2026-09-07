#!/usr/bin/env python3
"""credit.chrislawrence.ca data updater and read-only status API.

One narrow question: are credit spreads blowing out, and when they did, what
followed? Credit is the family's confirmer; the blowout table computes whether
that reputation is deserved.

Everything live on the page comes from series.json, machine-owned and
rewritten wholesale each run. data/meta.json and the vendored recessions.json
are never touched by automation. There is no curated projection and no hand
ritual on this site.

Guardrails, inherited from jobs: stale or shrunken upstreams are kept rather
than written, a failed fetch carries the previous series forward and records
the error, and revisions to already-published observations land in
changelog.jsonl. Moody's yields barely revise; NFCI is re-estimated routinely
(the whole index is refit), so revision entries here mostly mean the Chicago
Fed did its normal thing.

Two quirks this file knows about so nobody rediscovers them:

  * the ICE BofA OAS series serve only a sliding ~3-year window on FRED now,
    keyed and keyless identically (probed 2026-09-07); the window rolling
    forward coexists fine with the guardrails, and the notes say so;
  * two Canadian vectors are terminated (corporate paper 2018-12 with the
    CDOR era, McLeod-Young-Weir industrials 1988-12) and are fetched anyway:
    WDS still serves closed history, and the page draws them ending where
    they ended.

HTTP here is read-only. Runs happen via host cron calling
`docker exec credit-updater python /app/server.py --refresh`.
"""

import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import econcore

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

SERIES_FILE = os.path.join(DATA_DIR, "series.json")
CHANGELOG = os.path.join(DATA_DIR, "changelog.jsonl")
STATE_FILE = os.path.join(DATA_DIR, "updater-state.json")
RECESSIONS_FILE = os.path.join(DATA_DIR, "recessions.json")

CURATED = ["meta", "recessions"]
SHRINK_TOLERANCE = 0.9
CHANGELOG_IN_PAYLOAD = 100

# The blowout rule. Ships in the payload so the page states the rule that
# produced the table. Thresholds tuned once against the canonical record at
# build time (see BUILD-PLAN.md) and then frozen; they are content, not knobs.
EPISODE_RULE = {
    "series": "us_quality_spread",
    "basis": "3-month average versus its trailing 24-month low",
    "threshold_widen_pp": 0.4,
    "sustain_months": 2,
    "merge_gap_months": 6,
    "trough_lookback_months": 24,
    "window_before_months": 18,
    "window_after_months": 12,
    "statement": ("A blowout episode is a stretch of months with the 3-month "
                  "average of the Baa minus Aaa spread at least 0.40 "
                  "percentage points above its low over the prior two years, "
                  "at least two such months in a row; stretches separated by "
                  "fewer than six clear months merge into one. Each episode "
                  "is dated two ways: the TROUGH, where the spread was "
                  "tightest in the two years before the alarm (peak "
                  "complacency, credit's early clock), and the ALARM, the "
                  "first month past the threshold. An NBER peak from 18 "
                  "months before the alarm to 12 months after the last "
                  "signal month is assigned to the nearest episode; a peak "
                  "before the alarm scores as coincident, because credit "
                  "usually confirms a recession already underway rather than "
                  "leading it. Lead time runs from the trough."),
}

_payload_cache = {"stamp": None, "body": None}
_state = {"last_run": None, "results": []}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# the series list
#
# Adding a series is a human decision with a verified source; the updater
# only refreshes what is declared. Depths in the notes were probed live on
# 2026-09-07, not assumed. StatCan vectors were resolved from cube metadata
# (10-10-0122) and carry expect_title so a renumbered vector fails loudly.
# --------------------------------------------------------------------------

def _fred(series_id):
    return lambda: econcore.fred_series(series_id, FRED_KEY)


def _wds(vector_id, expect_title):
    return lambda: econcore.wds_vector(vector_id, expect_title=expect_title)


FMS_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1010012201"
OAS_NOTE = ("ICE now licenses FRED to redistribute only a sliding window of "
            "roughly three years (verified keyed and keyless, 2026-09-07), "
            "although the underlying index history reaches 1996. Shown as "
            "the recent tape; the deep story rides the Moody's series.")

FETCHED = [
    {
        "id": "us_baa",
        "fetch": _fred("BAA"),
        "label": "Moody's Baa corporate yield",
        "source": "Moody's seasoned Baa corporate bond yield, via FRED BAA",
        "source_url": "https://fred.stlouisfed.org/series/BAA",
        "units": "percent", "freq": "monthly",
        "note": "Lowest investment grade, monthly since January 1919. One leg of the century spread.",
    },
    {
        "id": "us_aaa",
        "fetch": _fred("AAA"),
        "label": "Moody's Aaa corporate yield",
        "source": "Moody's seasoned Aaa corporate bond yield, via FRED AAA",
        "source_url": "https://fred.stlouisfed.org/series/AAA",
        "units": "percent", "freq": "monthly",
        "note": "Strongest investment grade, monthly since January 1919. The other leg.",
    },
    {
        "id": "us_baa_daily",
        "fetch": _fred("DBAA"),
        "label": "Moody's Baa yield, daily",
        "source": "Moody's via FRED DBAA",
        "source_url": "https://fred.stlouisfed.org/series/DBAA",
        "units": "percent", "freq": "daily",
        "note": "Daily since 1986-01-02; the high-frequency continuation of the monthly series.",
    },
    {
        "id": "us_aaa_daily",
        "fetch": _fred("DAAA"),
        "label": "Moody's Aaa yield, daily",
        "source": "Moody's via FRED DAAA",
        "source_url": "https://fred.stlouisfed.org/series/DAAA",
        "units": "percent", "freq": "daily",
        "note": "Daily since 1983-01-03.",
    },
    {
        "id": "us_baa10y",
        "fetch": _fred("BAA10Y"),
        "label": "Baa spread over 10-year Treasury",
        "source": "Moody's Baa minus 10-year Treasury constant maturity, as published by FRED BAA10Y",
        "source_url": "https://fred.stlouisfed.org/series/BAA10Y",
        "units": "percentage_points", "freq": "daily",
        "note": "Daily since 1986-01-02. The modern headline: what lowest-grade investment credit pays over the government curve. Published, not computed here.",
    },
    {
        "id": "us_aaa10y",
        "fetch": _fred("AAA10Y"),
        "label": "Aaa spread over 10-year Treasury",
        "source": "Moody's Aaa minus 10-year Treasury, as published by FRED AAA10Y",
        "source_url": "https://fred.stlouisfed.org/series/AAA10Y",
        "units": "percentage_points", "freq": "daily",
        "note": "Daily since 1983-01-03.",
    },
    {
        "id": "us_hy_oas",
        "fetch": _fred("BAMLH0A0HYM2"),
        "label": "US high yield OAS",
        "source": "ICE BofA US High Yield Index option-adjusted spread, via FRED BAMLH0A0HYM2",
        "source_url": "https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
        "units": "percentage_points", "freq": "daily",
        "note": OAS_NOTE,
    },
    {
        "id": "us_ig_oas",
        "fetch": _fred("BAMLC0A0CM"),
        "label": "US investment grade OAS",
        "source": "ICE BofA US Corporate Index option-adjusted spread, via FRED BAMLC0A0CM",
        "source_url": "https://fred.stlouisfed.org/series/BAMLC0A0CM",
        "units": "percentage_points", "freq": "daily",
        "note": OAS_NOTE,
    },
    {
        "id": "us_ccc_oas",
        "fetch": _fred("BAMLH0A3HYC"),
        "label": "US CCC and lower OAS",
        "source": "ICE BofA CCC & Lower US High Yield option-adjusted spread, via FRED BAMLH0A3HYC",
        "source_url": "https://fred.stlouisfed.org/series/BAMLH0A3HYC",
        "units": "percentage_points", "freq": "daily",
        "note": "The junkiest tier, which blows out first and hardest. " + OAS_NOTE,
    },
    {
        "id": "us_nfci",
        "fetch": _fred("NFCI"),
        "label": "Chicago Fed financial conditions",
        "source": "Chicago Fed National Financial Conditions Index, via FRED NFCI",
        "source_url": "https://fred.stlouisfed.org/series/NFCI",
        "units": "index_zero_mean", "freq": "weekly",
        "note": "Weekly since January 1971. Positive means tighter than the 1971-onward average, negative looser. The whole index is re-estimated each week, so history moves a little routinely; the revision log records it.",
    },
    {
        "id": "us_anfci",
        "fetch": _fred("ANFCI"),
        "label": "Adjusted financial conditions",
        "source": "Chicago Fed adjusted NFCI, via FRED ANFCI",
        "source_url": "https://fred.stlouisfed.org/series/ANFCI",
        "units": "index_zero_mean", "freq": "weekly",
        "note": "The NFCI with the business-cycle component removed: conditions tighter or looser than the economy alone would explain.",
    },
    {
        "id": "ca_goc_long",
        "fetch": _wds(122487, "over 10 years"),
        "label": "Canada government long bond yield",
        "source": "Government of Canada marketable bonds, average yield over 10 years, StatCan table 10-10-0122-01, vector v122487",
        "source_url": FMS_TABLE,
        "units": "percent", "freq": "monthly",
        "note": "Monthly (last Wednesday) since January 1936, still published. The long anchor both Canadian spreads price against.",
    },
    {
        "id": "ca_myw_industrials",
        "fetch": _wds(122490, "industrials"),
        "label": "Canada corporate bond yields (MYW industrials)",
        "source": "McLeod, Young and Weir bond yield averages, 10 industrials, StatCan table 10-10-0122-01, vector v122490",
        "source_url": FMS_TABLE,
        "units": "percent", "freq": "monthly",
        "note": "TERMINATED SERIES: monthly 1948 through December 1988, when the average stopped being compiled. Fetched anyway; WDS serves closed history, and the spread built on it is drawn ending where it ended.",
    },
    {
        "id": "ca_corp_paper_3m",
        "fetch": _wds(122491, "Prime corporate paper"),
        "label": "Canada prime corporate paper, 3 month",
        "source": "Prime corporate paper rate, 3 month, StatCan table 10-10-0122-01, vector v122491",
        "source_url": FMS_TABLE,
        "units": "percent", "freq": "monthly",
        "note": "TERMINATED SERIES: monthly 1956 through December 2018, when CDOR-era paper publishing ended. No keyless successor exists; the gap after 2018 is real and stated on the page.",
    },
    {
        "id": "ca_tbill_3m",
        "fetch": _wds(122531, "Treasury bills: 3 month"),
        "label": "Canada treasury bills, 3 month",
        "source": "Treasury bills, 3 month, StatCan table 10-10-0122-01, vector v122531",
        "source_url": FMS_TABLE,
        "units": "percent", "freq": "monthly",
        "note": "Monthly (last Wednesday) since 1962, live. The short anchor for the paper spread.",
    },
]


# --------------------------------------------------------------------------
# derived series: the constructions, stated
# --------------------------------------------------------------------------

def _aligned_spread(long_obs, short_obs, digits=3):
    short_map = dict(map(tuple, short_obs))
    out = []
    for date, long_v in long_obs:
        short_v = short_map.get(date)
        if short_v is None:
            continue
        out.append([date, round(long_v - short_v, digits)])
    return out


def build_derived(series):
    """Computed from shipped inputs on every run. Confidence is 'estimate'
    because the arithmetic happens here; every input is a reported series in
    the same payload."""
    out = {}

    baa, aaa = series.get("us_baa"), series.get("us_aaa")
    if baa and aaa:
        out["us_quality_spread"] = econcore.make_series(
            "us_quality_spread",
            "US credit quality spread (Baa minus Aaa)",
            "Derived: Moody's Baa minus Aaa seasoned yields",
            "https://fred.stlouisfed.org/series/BAA",
            "percentage_points", "monthly",
            _aligned_spread(baa["obs"], aaa["obs"]),
            confidence="estimate",
            note="Pure credit-quality pricing with no duration mix: both legs are Moody's long corporate averages on the same basis. Monthly since 1919; the century spine of this page, and the series the blowout table computes from. Both raw legs ship in this payload.")

    dbaa, daaa = series.get("us_baa_daily"), series.get("us_aaa_daily")
    if dbaa and daaa:
        out["us_quality_spread_daily"] = econcore.make_series(
            "us_quality_spread_daily",
            "US credit quality spread, daily",
            "Derived: Moody's DBAA minus DAAA",
            "https://fred.stlouisfed.org/series/DBAA",
            "percentage_points", "daily",
            _aligned_spread(dbaa["obs"], daaa["obs"]),
            confidence="estimate",
            note="Same construction and publisher as the monthly series, daily from 1986. Drawn as its high-frequency continuation, not a splice.")

    myw, goc = series.get("ca_myw_industrials"), series.get("ca_goc_long")
    if myw and goc:
        out["ca_corp_spread_hist"] = econcore.make_series(
            "ca_corp_spread_hist",
            "Canada corporate spread, 1948-1988 (closed)",
            "Derived: MYW 10-industrials average minus GoC over-10-year average yield",
            FMS_TABLE, "percentage_points", "monthly",
            _aligned_spread(myw["obs"], goc["obs"]),
            confidence="estimate",
            note="A real Canadian corporate credit spread with forty years of history, ending December 1988 because the corporate leg stopped being compiled. Drawn ending where it ended; never extended, never spliced.")

    paper, bill = series.get("ca_corp_paper_3m"), series.get("ca_tbill_3m")
    if paper and bill:
        out["ca_paper_bill_spread"] = econcore.make_series(
            "ca_paper_bill_spread",
            "Canada paper-bill funding spread, 1962-2018 (closed)",
            "Derived: prime corporate paper 3 month minus treasury bills 3 month",
            FMS_TABLE, "percentage_points", "monthly",
            _aligned_spread(paper["obs"], bill["obs"]),
            confidence="estimate",
            note="The classic funding-stress spread: what prime corporate borrowers paid over the government for three-month money. Ends December 2018 with CDOR-era publishing. Live Canadian corporate spread data is paywalled (FTSE/TMX); the gap after 2018 is shown, not filled.")

    return out


# --------------------------------------------------------------------------
# analysis: status and the blowout table
# --------------------------------------------------------------------------

def _mi(year_month):
    year, month = year_month.split("-")[:2]
    return int(year) * 12 + int(month) - 1


def _three_month_avg(obs):
    return [[obs[i][0], (obs[i][1] + obs[i - 1][1] + obs[i - 2][1]) / 3.0]
            for i in range(2, len(obs))]


def build_status(series):
    status = {}
    spread = series.get("us_quality_spread")
    if spread and len(spread["obs"]) > 27:
        avgs = _three_month_avg(spread["obs"])
        window = avgs[-25:-1]
        low = min(window, key=lambda a: a[1])
        latest = avgs[-1]
        widening = latest[1] - low[1]
        status["us_quality_spread"] = {
            "latest": [spread["obs"][-1][0], spread["obs"][-1][1]],
            "latest_3mma": [latest[0], round(latest[1], 2)],
            "low_3mma_24m": [low[0], round(low[1], 2)],
            "widening_pp": round(widening, 2),
        }
        status["signal_active"] = widening >= EPISODE_RULE["threshold_widen_pp"]
    for sid in ("us_baa10y", "us_hy_oas", "us_nfci"):
        entry = series.get(sid)
        if entry:
            status[sid] = {"latest": [entry["obs"][-1][0], entry["obs"][-1][1]],
                           "first": entry["obs"][0][0]}
    return status


def build_episodes(spread_entry, recessions):
    """The blowout table: housing's two clocks, inverted. The trough (the
    tightest spread in the lookback, peak complacency) is credit's early
    clock; the alarm (sustained widening past the threshold) is the late
    confirmation. The rule ships alongside the rows."""
    avgs = _three_month_avg(spread_entry["obs"])
    level = {d[:7]: v for d, v in avgs}
    months = [[d[:7], v] for d, v in avgs]
    look = EPISODE_RULE["trough_lookback_months"]
    threshold = EPISODE_RULE["threshold_widen_pp"]

    widen = []
    for i in range(look, len(months)):
        low = min(v for _, v in months[i - look:i])
        widen.append([months[i][0], months[i][1] - low])

    qualifying = [i for i, (_, w) in enumerate(widen) if w >= threshold]
    if not qualifying:
        return {"rule": EPISODE_RULE, "episodes": [], "stats": {}}

    runs = [[qualifying[0], qualifying[0]]]
    for i in qualifying[1:]:
        if i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    groups = [runs[0][:]]
    for start_i, end_i in runs[1:]:
        gap = _mi(widen[start_i][0]) - _mi(widen[groups[-1][1]][0]) - 1
        if gap < EPISODE_RULE["merge_gap_months"]:
            groups[-1][1] = end_i
        else:
            groups.append([start_i, end_i])

    def longest_run(lo, hi):
        best = run = 0
        for i in range(lo, hi + 1):
            run = run + 1 if widen[i][1] >= threshold else 0
            best = max(best, run)
        return best

    groups = [g for g in groups
              if longest_run(g[0], g[1]) >= EPISODE_RULE["sustain_months"]]

    def month_at(mi_value):
        return "%04d-%02d" % (mi_value // 12, mi_value % 12 + 1)

    shells = []
    for lo, hi in groups:
        span = widen[lo:hi + 1]
        start, end = span[0][0], span[-1][0]
        before = [month_at(m) for m in range(_mi(start) - look, _mi(start))]
        trough = min((m for m in before if m in level), key=lambda m: level[m])
        after = [month_at(m) for m in range(_mi(start), _mi(end) + 7)]
        peak_m = max((m for m in after if m in level), key=lambda m: level[m])
        shells.append({
            "start": start, "end": end, "span": span,
            "trough": trough, "peak_m": peak_m,
            "window_lo": _mi(start) - EPISODE_RULE["window_before_months"],
            "window_hi": _mi(end) + EPISODE_RULE["window_after_months"],
            "peaks": [],
        })

    bands = recessions["us"]["bands"]
    data_through = _mi(recessions["us"]["as_of"][:7])
    assigned = set()
    for band in bands:
        peak = band["peak"]
        candidates = [s for s in shells
                      if s["window_lo"] <= _mi(peak) <= s["window_hi"]]
        if not candidates:
            continue
        best = max(candidates, key=lambda s: _mi(s["start"]))
        best["peaks"].append(peak)
        assigned.add(peak)

    episodes = []
    for s in shells:
        led = [p for p in s["peaks"] if _mi(p) >= _mi(s["start"])]
        if led:
            outcome = "recession"
        elif s["peaks"]:
            outcome = "coincident"
        elif s["window_hi"] > data_through:
            outcome = "pending"
        else:
            outcome = "none_in_window"
        first_peak = s["peaks"][0] if s["peaks"] else None
        episodes.append({
            "start": s["start"],
            "end": s["end"],
            "trough": {"month": s["trough"],
                       "value": round(level[s["trough"]], 2)},
            "peak": {"month": s["peak_m"],
                     "value": round(level[s["peak_m"]], 2)},
            "widening_pp": round(level[s["peak_m"]] - level[s["trough"]], 2),
            "months_signalling": len([1 for _, w in s["span"]
                                      if w >= threshold]),
            "recessions": s["peaks"],
            "lead_from_trough_months": (_mi(first_peak) - _mi(s["trough"])
                                        if first_peak else None),
            "lead_from_alarm_months": (_mi(first_peak) - _mi(s["start"])
                                       if first_peak else None),
            "outcome": outcome,
        })

    leads = sorted(e["lead_from_trough_months"] for e in episodes
                   if e["recessions"])
    stats = {}
    if leads:
        mid = len(leads) // 2
        median = (leads[mid] if len(leads) % 2
                  else (leads[mid - 1] + leads[mid]) / 2.0)
        stats = {"credited_episodes": len(leads),
                 "median_lead_from_trough_months": median,
                 "min_lead_from_trough_months": leads[0],
                 "max_lead_from_trough_months": leads[-1],
                 "alarm_led": len([e for e in episodes
                                   if e["outcome"] == "recession"]),
                 "coincident": len([e for e in episodes
                                    if e["outcome"] == "coincident"]),
                 "false_positives": len([e for e in episodes
                                         if e["outcome"] == "none_in_window"]),
                 "pending": len([e for e in episodes
                                 if e["outcome"] == "pending"]),
                 "uncredited_recessions": [
                     b["peak"] for b in bands
                     if _mi(b["peak"]) >= _mi(months[0][0])
                     and b["peak"] not in assigned]}
    return {"rule": EPISODE_RULE, "episodes": episodes, "stats": stats}


def build_analysis(series):
    analysis = {"status": build_status(series)}
    spread = series.get("us_quality_spread")
    if spread:
        try:
            recessions = econcore.load_recessions(RECESSIONS_FILE)
            analysis["episodes"] = build_episodes(spread, recessions)
        except Exception as exc:  # noqa: BLE001 - the table degrades, the page renders
            analysis["episodes_error"] = "%s: %s" % (type(exc).__name__, exc)
    return analysis


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

def load_old_series():
    try:
        with open(SERIES_FILE) as fh:
            return json.load(fh).get("series", {})
    except Exception:  # noqa: BLE001 - first run, or corrupt file: start clean
        return {}


def _diff_revisions(series_id, old_obs, new_obs):
    """Changed values at already-published dates, raw fetched series only."""
    old_map = dict(map(tuple, old_obs))
    changed = [(d, old_map[d], v) for d, v in new_obs
               if d in old_map and abs(old_map[d] - v) > 1e-9]
    if not changed:
        return None
    deltas = [abs(after - before) for _, before, after in changed]
    return {
        "series": series_id, "action": "revised",
        "changed": len(changed),
        "span": [changed[0][0], changed[-1][0]],
        "max_delta": round(max(deltas), 4),
        "sample": [{"date": d, "before": b, "after": a}
                   for d, b, a in changed[:3]],
    }


def refresh_series(dry=False):
    old = load_old_series()
    series, errors, results = {}, {}, []

    for spec in FETCHED:
        sid = spec["id"]
        prev = old.get(sid)
        rec = {"series": sid, "action": "fetched"}
        try:
            obs = spec["fetch"]()
            doc = econcore.make_series(
                sid, spec["label"], spec["source"], spec["source_url"],
                spec["units"], spec["freq"], obs, note=spec.get("note"))
            if prev and prev.get("obs"):
                if doc["as_of"] < prev["as_of"]:
                    rec.update(action="stale-upstream",
                               reason="upstream at %s, behind stored %s; kept"
                                      % (doc["as_of"], prev["as_of"]))
                    doc = prev
                elif len(obs) < len(prev["obs"]) * SHRINK_TOLERANCE:
                    rec.update(action="shrunk",
                               reason="%d obs against %d stored; kept"
                                      % (len(obs), len(prev["obs"])))
                    doc = prev
                else:
                    revision = _diff_revisions(sid, prev["obs"], obs)
                    if revision and prev.get("source") == doc.get("source"):
                        if not dry:
                            econcore.log_revision(CHANGELOG, revision)
                        rec.update(action="revised",
                                   changed=revision["changed"])
                    added = len(obs) - len(prev["obs"])
                    if added > 0:
                        rec["added"] = added
            series[sid] = doc
        except Exception as exc:  # noqa: BLE001 - one dead endpoint, one chart
            errors[sid] = "%s: %s" % (type(exc).__name__, exc)
            rec.update(action="error", reason=errors[sid])
            if prev:
                series[sid] = prev
                rec["carried_forward"] = True
        results.append(rec)
        print("%-24s %-14s %s" % (sid, rec["action"], rec.get("reason", "")),
              flush=True)

    if not series:
        raise ValueError("nothing fetched and nothing stored; refusing to write")

    series.update(build_derived(series))
    analysis = build_analysis(series)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Machine-fetched. Never hand-edited; the updater rewrites this file wholesale.",
        "econcore": econcore.VERSION,
        "fred_key_used": bool(FRED_KEY),
        "errors": errors,
        "series": series,
        "analysis": analysis,
    }

    if dry:
        total = sum(len(s["obs"]) for s in series.values())
        print("dry run: %d series, %d observations, %d errors -- not written"
              % (len(series), total, len(errors)), flush=True)
        return payload

    tmp = SERIES_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, SERIES_FILE)

    with _lock:
        _state["last_run"] = datetime.now(timezone.utc).isoformat()
        _state["results"] = results
    _save_state()

    total = sum(len(s["obs"]) for s in series.values())
    print("series refreshed: %d series, %d observations, %d errors"
          % (len(series), total, len(errors)), flush=True)
    return payload


def _save_state():
    try:
        with _lock:
            snapshot = dict(_state)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# read-only HTTP
# --------------------------------------------------------------------------

def _load(name):
    with open(os.path.join(DATA_DIR, name)) as fh:
        return json.load(fh)


def data_stamp():
    newest = 0.0
    names = [n + ".json" for n in CURATED] + ["series.json", "changelog.jsonl"]
    for name in names:
        try:
            newest = max(newest, os.path.getmtime(os.path.join(DATA_DIR, name)))
        except OSError:
            continue
    return newest


def build_data_payload():
    stamp = data_stamp()
    if _payload_cache["stamp"] == stamp and _payload_cache["body"] is not None:
        return _payload_cache["body"]

    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    for name in CURATED:
        try:
            payload[name] = _load(name + ".json")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            payload[name] = None
            payload.setdefault("errors", {})[name] = str(exc)
    try:
        doc = _load("series.json")
        payload["series"] = doc.get("series", {})
        payload["analysis"] = doc.get("analysis", {})
        payload["series_fetched_at"] = doc.get("fetched_at")
        payload["series_errors"] = doc.get("errors", {})
    except Exception as exc:  # noqa: BLE001 - charts degrade, page renders
        payload["series"] = {}
        payload["analysis"] = {}
        payload.setdefault("errors", {})["series"] = str(exc)

    recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
    payload["changelog"] = {"total": total, "recent": recent}

    _payload_cache["stamp"] = stamp
    _payload_cache["body"] = payload
    return payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, cache="no-cache"):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            # Probe the dependency, not the process: no data, not healthy.
            try:
                doc = _load("series.json")
                spread = doc.get("series", {}).get("us_quality_spread", {})
                st = doc.get("analysis", {}).get("status", {})
                self._send(200, {
                    "status": "ok",
                    "series": len(doc.get("series", {})),
                    "latest": spread.get("as_of"),
                    "signal_active": st.get("signal_active"),
                    "errors": len(doc.get("errors", {})),
                    "fetched_at": doc.get("fetched_at"),
                })
            except Exception as exc:  # noqa: BLE001 - absent data IS the unhealthy case
                self._send(503, {"status": "no data", "error": str(exc)})
        elif path == "/api/data":
            self._send(200, build_data_payload(),
                       cache="public, max-age=300, must-revalidate")
        elif path == "/api/status":
            with _lock:
                snapshot = dict(_state)
            snapshot["fred_key"] = bool(FRED_KEY)
            snapshot["econcore"] = econcore.VERSION
            self._send(200, snapshot)
        elif path == "/api/changelog":
            recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
            self._send(200, {"total": total, "recent": recent})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    if "--refresh" in sys.argv:
        refresh_series()
        return
    if "--once" in sys.argv:
        refresh_series(dry=True)
        return

    print("updater starting: fred_key=%s (schedule: host cron)"
          % bool(FRED_KEY), flush=True)

    def warm():
        try:
            refresh_series()
        except Exception as exc:  # noqa: BLE001 - server must come up regardless
            print("initial fetch failed: %s" % exc, flush=True)

    threading.Thread(target=warm, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
