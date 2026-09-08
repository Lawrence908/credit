# credit.chrislawrence.ca — build plan

One narrow question: **are credit spreads blowing out, and when they did, what followed?**
Credit is the family's confirmer: the yield curve hints a year or two ahead, housing rolls
over quarters ahead, and spreads blow out when the event is underway. The launch table in
this family's founding note said exactly that ("spread blowouts confirm what the curve
only hints at"), and this site computes whether it is true.

Site six of the family, fourth econ-core consumer. Written 2026-09-07; every series was
probed live that day through econcore's fetchers. Two probe findings
reshaped the plan, both below.

## Verified sources

### United States (FRED, keyless unless noted)

| Series | What | Depth (verified) | Freq | Latest |
|---|---|---|---|---|
| `BAA` / `AAA` | Moody's seasoned corporate yields | **1919-01 → live** | monthly | 6.32 / 5.88 |
| `DBAA` / `DAAA` | Same, daily | 1986 / 1983 → | daily | 6.34 / 5.92 |
| `BAA10Y` | Baa minus 10-year Treasury, as published | 1986-01-02 → | daily | 1.57 |
| `AAA10Y` | Aaa minus 10-year Treasury | 1983-01-03 → | daily | 1.15 |
| `BAMLH0A0HYM2` | ICE BofA US High Yield OAS | **2023-09 → only** | daily | 2.65 |
| `BAMLC0A0CM` | ICE BofA IG Corporate OAS | 2023-09 → only | daily | 0.81 |
| `BAMLH0A3HYC` | ICE BofA CCC & lower OAS | 2023-09 → only | daily | 10.51 |
| `NFCI` / `ANFCI` | Chicago Fed financial conditions | 1971-01 → | weekly | -0.56 / -0.58 |

### Canada (StatCan WDS cube 10-10-0122, vectors resolved and title-verified)

| Vector | What | Depth | Status |
|---|---|---|---|
| `v122487` | GoC marketable bonds avg yield, over 10 years | **1936-01 → live** | the long anchor |
| `v122490` | McLeod, Young & Weir bond averages: 10 industrials | 1948-01 → 1988-12 | **terminated** |
| `v122491` | Prime corporate paper rate, 3 month | 1956-01 → 2018-12 | **terminated** |
| `v122531` | Treasury bills, 3 month | 1962-01 → live | short anchor |

Findings that override assumptions:

1. **The ICE BofA OAS series now carry only a sliding ~3-year window on FRED**, keyless
   AND keyed identically (probed both), despite the underlying indexes reaching 1996.
   That is an upstream licensing change, not an endpoint quirk. Consequence: the modern
   deep-spread story rides Moody's Baa series (full history verified), and the OAS trio
   ships honestly as a "recent tape" card with the truncation stated, because HY OAS is
   the number the market actually quotes. The sliding window coexists fine with the
   updater guardrails (obs roll off the front, count stays flat, revision diff compares
   overlapping dates only).
2. **Canada's credit-spread record is real but closed.** The corporate paper rate died
   with CDOR-era publishing in 2018; the McLeod-Young-Weir industrials average died in
   1988; nothing keyless replaced them (live Canadian corporate spreads are FTSE/TMX,
   paywalled). The Canada card therefore shows two deep terminated series against live
   anchors, with the terminations and the post-2018 gap stated. An honest gap beats a
   plausible wrong number; that is family doctrine, and here the gap itself is content.
3. Launch posture, from the probe: Baa minus Aaa at 0.44pp and NFCI at -0.56 read as
   historically tight spreads and loose conditions, which is the complacency end of this
   site's own clock. The page states that from computed tokens, not hardcoded numbers.

## Series construction rules

- **`us_quality_spread` = BAA minus AAA, monthly, 1919 →**: the century spine, pure
  credit-quality pricing with no duration mix, both legs from Moody's on the same basis.
  Daily variant DBAA minus DAAA from 1986 draws on the same chart as its high-frequency
  continuation; same construction, same publisher, stated. Raw legs ship.
- **`BAA10Y` as published** is the modern headline (1986 →, daily): Baa over the 10-year
  Treasury. Not recomputed. AAA10Y is the toggle.
- **OAS trio as published**, window truncation in the note field and on the card.
- **Canada**: `ca_corp_spread_hist` = v122490 minus v122487 (monthly, 1948-1988, closed);
  `ca_paper_bill_spread` = v122491 minus v122531 (monthly, 1962-2018, closed). Both
  confidence `estimate` with construction stated; both drawn as closed series that
  visibly end, never extended, never spliced.
- No secret transformations. Derived spreads are subtraction with the formula stated.

## The feature: the blowout table, two clocks

Housing proved the two-clock design (crest vs alarm); credit inverts it. Per episode:

- **The trough clock**: the tightest 3-month average of the quality spread in the 24
  months before the alarm. Spread troughs are peak complacency and historically sit well
  before recessions (1997, 2007-02, 2021). This is credit's analogue of housing's crest.
- **The alarm clock**: sustained widening past the rule threshold. Candidate rule, to be
  tuned once against the canonical record at build and then frozen: the 3-month average
  of Baa minus Aaa at least 0.50pp above its trailing 24-month low, two consecutive
  months; episodes merging within six clear months; NBER peaks assigned to the nearest
  episode inside [alarm - 6 months, last signal + 18 months]; outcome vocabulary
  identical to housing (recession / coincident / none_in_window / pending).
- Expected canonical reading (verify computed, then believe the table): blowouts at
  1929-32, 1937, 1957, 1970, 1974, 1980-82, 1990, 2000-02, 2008, 2020, with the alarm
  mostly coincident or barely leading; and surviving false positives where the market
  blew out and no recession came (the 2015-16 energy bust should appear if the rule
  catches it; 1938-45 war-era noise may too). **If the stats show credit's alarms
  trailing where housing's crests led, the family's sequencing claim is computed, not
  asserted.** That cross-site sentence is the point of building this one.
- Depth per episode: trough level, peak level and month, widening in pp. 2008 should
  read roughly 0.9 to 3.4; 1932 roughly 1.5 to 5.6.

## Architecture

Clone housing wholesale; nothing new. nginx front `credit` (host port **8132**, verified
free in catalog and listeners; the family's next block) + stdlib sidecar
`credit-updater`. Vendor econ-core; jobs guardrails; analysis block `{status, episodes}`
with the rule shipped; payload from disk on mtime; host cron daily 07:10 PT (Moody's
dailies post next-morning, NFCI Wednesdays, StatCan monthly) plus log truncation.
`data/meta.json` near-empty: credit has no curated projection and no hand ritual.
Vintages reserved, low value here (yields barely revise; NFCI does get revised, noted).

## Page

Same bones as housing (TimeChart, tokens, tiles, chip, no framework, hub footer linked):

1. Header, the question, chip: blowout signal or not, current quality spread vs its
   trailing 24-month low, NFCI posture. Amber when the rule is signalling.
2. Tiles: Baa-Aaa, BAA10Y, HY OAS (with window caveat in the note), NFCI, and the
   computed "alarms led N of M recessions" stat next to housing's median-lead number.
3. Century chart: quality spread monthly 1919 → with daily 1986 → continuation, every
   recession shaded. The Depression peak makes the axis; that is the point.
4. The blowout table with rule printed, trough and alarm columns, computed footnotes
   (newest row from data, the confirmer-vs-hinter sentence from stats).
5. Modern chart: BAA10Y daily with AAA10Y toggle, range presets.
6. Recent tape: HY / IG / CCC OAS, three years, truncation stated in the caption.
7. NFCI weekly 1971 → with zero line (positive = tighter than average), bands.
8. Canada: the two closed spreads with terminations visible, C.D. Howe bands, the
   post-2018 gap stated in the caption with why (CDOR death, FTSE paywall).
9. Revisions card (NFCI revises routinely, Moody's yields barely; says so), sources
   card with every series, vector, verified depth, termination dates, the ICE licensing
   note, fetch policy, econ-core contract note, provenance line.

## Deploy checklist (identical to housing's, values changed)

Port 8132; `sites/credit.caddy`; services.yml entry with dashy + kuma blocks (compact
keyword); `cf-access.sh create credit.chrislawrence.ca --policy public` + retry; cron +
truncation; screenshots (mobile fullPage, desktop, blowout table) + layout audit +
console check; `ls -l data/`; commit; push private `Lawrence908/credit`.

## Anti-goals

- No hand-maintained derived data; the table computes or does not ship.
- No splicing the OAS window onto anything, and no pretending it is deep.
- No extending the terminated Canadian series by proxy or model; they end on the page
  the way they ended in the world.
- No house model of default risk, no "fair value" spread, no forecasts. Measured
  spreads, published conditions indexes, computed history with the rule printed.
- No emdashes in page copy.

## Acceptance

- All series land with zero errors on a cold start, contract-validated, including both
  terminated Canadian vectors served historically by WDS.
- The blowout table reproduces the canonical record or the discrepancy is investigated
  until the table is believed; the rule is then frozen and printed.
- The stats computably answer "does credit confirm what the curve hints": alarm leads
  vs housing's crest leads, stated on the page from tokens.
- Kill `FRED_API_KEY`: everything still refreshes (keyless CSV and WDS are primary;
  the OAS trio behaves identically either way).
- Both containers healthy, public 200, Kuma green, screenshots committed, zero console
  errors, no horizontal scroll, repo pushed, no machine-owned files in git.
