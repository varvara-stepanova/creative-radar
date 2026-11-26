# Creative Radar – competitor ad intelligence for consumer fintech apps

A small end-to-end tool that shows what a brand and its competitors are running on Meta right now, and turns it into concrete creative test ideas.

**How it works**
1. **Fetch:** pulls active ads per brand from the public Meta Ad Library API (or uses a saved CSV sample).
2. **Tag:** an AI model (Claude) labels every ad with angle, hook type, value proposition and CTA. Without an API key, a simple keyword tagger is used, so it always runs.
3. **Analyse:** ads per brand, new ads per week (creative velocity), angle mix, top headlines, longest-running ads (a proxy for winners, because the Ad Library shows no spend), and **white space**: angles competitors use that your brand doesn't.
4. **What changed:** new ads in the last 7 days by angle, plus a comparison with the previous run (new ads, stopped ads, new angles a competitor just started testing).
5. **Report:** AI writes 5 concrete Meta test ideas (hypothesis, angle, hook, KPI), and everything goes into one HTML report (`output/report.html`).
6. **Email summary (optional):** a short weekly summary with the report attached, sent via Gmail, or saved as a ready-to-send draft.

## Run it
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...          # optional: enables AI tagging + test ideas
python radar.py run --input data/ads_sample_DE.csv
open output/report.html
```
The tool asks which brands to analyse (or pass `--brands "Our brand,Competitor A"`).
To track your own brands, put their Facebook page IDs in `competitors.csv` and fetch fresh data (needs a Meta Ad Library API token):
```bash
python radar.py fetch --token $META_TOKEN --country DE
python radar.py run --input data/ads_latest.csv --email-to you@example.com
```

**Cost control:** AI tags are cached per unique headline, so re-runs only tag new ads (about 1–3 cents per run).

## Sample data
`data/ads_sample_DE.csv` is an anonymised sample of 109 public ads from three German investing apps (Meta Ad Library, November 2026). Brand names are replaced with "Our brand", "Competitor A" and "Competitor B".

## Limitations and next steps
The Ad Library shows no spend or performance, so this is a direction finder, not a performance report. Next steps: analyse the images and videos themselves (hooks, formats, UGC vs studio), run it weekly on a schedule, and join it with the brand's own Meta data to compare competitor angles with your own CPA by angle.
