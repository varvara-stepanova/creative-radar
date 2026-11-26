"""
Creative Radar – competitor ad intelligence for consumer fintech apps.

Pipeline:
  1. fetch   – pull public ads from the Meta Ad Library API (or use a saved CSV snapshot)
  2. tag     – AI tags every ad: angle, hook type, value proposition, CTA
               (Claude API if ANTHROPIC_API_KEY is set, otherwise a simple keyword fallback)
  3. report  – aggregates by brand and angle, finds white space for your brand,
               asks AI for test ideas and writes an HTML report

Usage:
  python radar.py fetch  --token $META_TOKEN --country DE      # optional, needs Ad Library API access
  python radar.py run    --input data/ads_sample_DE.csv
"""
import argparse, csv, datetime as dt, html, json, os, sys, time
from collections import Counter, defaultdict

def load_brands(path="competitors.csv"):
    """Brands to track. Add a competitor = add one line (brand, Facebook page ID, is_client)."""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    client = next((r["brand"] for r in rows if r.get("is_client", "").lower() == "yes"), rows[0]["brand"])
    return {r["brand"]: r["page_id"] for r in rows}, client


def pick_brands(all_brands, arg):
    """--brands "a,b" or an interactive menu in the terminal."""
    names = list(all_brands)
    if arg:
        chosen = [b.strip() for b in arg.split(",") if b.strip() in all_brands]
    else:
        print("Which brands do you want to analyse?")
        for i, b in enumerate(names, 1):
            print(f"  [{i}] {b}")
        raw = input("Numbers separated by commas (Enter = all): ").strip()
        chosen = [names[int(x) - 1] for x in raw.split(",") if x.strip().isdigit() and 0 < int(x) <= len(names)] or names
    print("Analysing:", ", ".join(chosen))
    return chosen
ANGLES = ["AI / smart investing", "Portfolio overview & tracking", "Passive income & dividends",
          "Fast & easy onboarding", "Saving for the future", "All-in-one broker & bank",
          "Direct sign-up CTA", "Brand / other"]
MODEL = os.getenv("RADAR_MODEL", "claude-sonnet-5-5")
CACHE = "output/tag_cache.json"
CLIENT, CHANGES = "Our brand", {}


# ---------- 1. fetch ----------
def fetch(token, country, out, brands):
    import urllib.parse, urllib.request
    fields = "id,page_name,ad_creative_bodies,ad_creative_link_titles,ad_delivery_start_time,ad_snapshot_url"
    rows = []
    for brand, page_id in brands.items():
        url = "https://graph.facebook.com/v21.0/ads_archive?" + urllib.parse.urlencode({
            "access_token": token, "search_page_ids": page_id, "ad_reached_countries": f'["{country}"]',
            "ad_active_status": "ACTIVE", "fields": fields, "limit": 100})
        while url and len([r for r in rows if r["brand"] == brand]) < 300:
            data = json.load(urllib.request.urlopen(url))
            for a in data.get("data", []):
                rows.append({"ad_id": a["id"], "brand": brand,
                             "headline": (a.get("ad_creative_link_titles") or [""])[0],
                             "body": (a.get("ad_creative_bodies") or [""])[0],
                             "delivery_start": a.get("ad_delivery_start_time", ""),
                             "snapshot_url": a.get("ad_snapshot_url", "")})
            url = data.get("paging", {}).get("next")
            time.sleep(1)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(f"Saved {len(rows)} ads to {out}")


# ---------- 2. tag ----------
def rule_tag(text):
    t = text.lower()
    rules = [("AI / smart investing", ["ki-", " ki ", "mit ki", " ai", "smart"]),
             ("Passive income & dividends", ["passiv", "dividend"]),
             ("All-in-one broker & bank", ["broker & bank", "investiere, spare, finanziere"]),
             ("Portfolio overview & tracking", ["dashboard", "tracker", "überblick", "portfolio"]),
             ("Fast & easy onboarding", ["5 minuten", "minutes", "einfach", "schnell"]),
             ("Saving for the future", ["spare", "später", "altersvorsorge", "sparplan"]),
             ("Direct sign-up CTA", ["werde kunde", "jetzt starten", "konto"])]
    for angle, keys in rules:
        if any(k in " " + t for k in keys):
            return {"angle": angle, "hook_type": "statement", "value_prop": text[:60], "cta": "", "tagger": "rules"}
    return {"angle": "Brand / other", "hook_type": "brand", "value_prop": text[:60], "cta": "", "tagger": "rules"}


def ai_tag(client, headline, body):
    prompt = f"""You are a performance marketer analysing a German fintech ad.
Headline: {headline!r}
Body: {body!r}
Return only JSON with keys:
 angle (one of {ANGLES}),
 hook_type (question | statement | number | social_proof | fear_of_missing_out | brand),
 value_prop (max 8 words, English),
 cta (max 4 words, English or empty)."""
    msg = client.messages.create(model=MODEL, max_tokens=600, messages=[{"role": "user", "content": prompt}])
    txt = "".join(b.text for b in msg.content if b.type == "text")
    data = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    data["tagger"] = "claude"
    return data


def tag_all(ads):
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    client = None
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
    for a in ads:
        key = (a["headline"] + "|" + a["body"]).strip()
        if key not in cache or (client and cache[key].get("tagger") != "claude"):
            try:
                cache[key] = ai_tag(client, a["headline"], a["body"]) if client else rule_tag(key)
            except Exception as e:  # never let one bad ad stop the run
                print("tagging failed, using rules:", e); cache[key] = rule_tag(key)
        a.update(cache[key])
    os.makedirs("output", exist_ok=True)
    json.dump(cache, open(CACHE, "w"), ensure_ascii=False, indent=1)
    return "claude" if client else "rules"


# ---------- 3. analyse + report ----------
def analyse(ads, today):
    by_brand = defaultdict(list)
    for a in ads:
        start = a["delivery_start"]
        start = dt.datetime.fromtimestamp(int(start)) if str(start).isdigit() else dt.datetime.fromisoformat(start[:10])
        a["days_live"] = (today - start).days
        a["week"] = start.strftime("%G-W%V")
        by_brand[a["brand"]].append(a)
    summary = {}
    for b, lst in by_brand.items():
        angles = Counter(a["angle"] for a in lst)
        weeks = Counter(a["week"] for a in lst)
        longest = sorted(lst, key=lambda a: -a["days_live"])[:3]
        summary[b] = {"ads": len(lst), "angles": dict(angles.most_common()),
                      "new_ads_per_week": round(len(lst) / max(len(weeks), 1), 1),
                      "headlines": dict(Counter(a["headline"] or "(no headline)" for a in lst).most_common(5)),
                      "longest_running": [(a["headline"] or "(no headline)", a["days_live"], a["snapshot_url"]) for a in longest]}
    used_by_client = set(summary.get(CLIENT, {}).get("angles", {}))
    white_space = sorted({ang for b, s in summary.items() if b != CLIENT for ang in s["angles"]} - used_by_client - {"Brand / other"})
    return summary, white_space


LAST_RUN = "output/last_run.json"


def changes(ads, today):
    """New ads in the last 7 days per brand, plus a diff against the previous run (if there is one)."""
    out = {}
    prev = json.load(open(LAST_RUN)) if os.path.exists(LAST_RUN) else None
    by_brand = defaultdict(list)
    for a in ads:
        by_brand[a["brand"]].append(a)
    for b, lst in by_brand.items():
        new_week = [a for a in lst if a["days_live"] <= 7]
        c = {"new_this_week": len(new_week),
             "new_angles_this_week": dict(Counter(a["angle"] for a in new_week).most_common())}
        if prev and b in prev:
            now_ids, old_ids = {a["ad_id"] for a in lst}, set(prev[b]["ad_ids"])
            c["new_since_last_run"] = len(now_ids - old_ids)
            c["stopped_since_last_run"] = len(old_ids - now_ids)
            c["angles_new_vs_last_run"] = sorted({a["angle"] for a in lst} - set(prev[b]["angles"]))
            c["last_run_date"] = prev[b]["date"]
        out[b] = c
    json.dump({b: {"ad_ids": [a["ad_id"] for a in lst], "angles": sorted({a["angle"] for a in lst}),
                   "date": today.date().isoformat()} for b, lst in by_brand.items()},
              open(LAST_RUN, "w"), ensure_ascii=False, indent=1)
    return out


def ideas(summary, white_space):
    if not os.getenv("ANTHROPIC_API_KEY"):
        return ["(Set ANTHROPIC_API_KEY to generate AI test ideas.)"]
    import anthropic
    prompt = f"""You are a growth marketer for {CLIENT} (a consumer investing app, freemium, DE market).
Competitor ad data (public Meta Ad Library, last weeks): {json.dumps(summary, ensure_ascii=False)}
Angles competitors use that {CLIENT} does not: {white_space}\nWhat changed recently: {json.dumps(CHANGES, ensure_ascii=False)}
Give exactly 5 concrete Meta creative tests for {CLIENT}. Adapt competitor angles to {CLIENT}'s product instead of copying them.
Return ONLY a JSON array of 5 objects with keys: title, hypothesis, angle, hook, kpi. Short, specific, no markdown."""
    try:
        msg = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
        txt = "".join(b.text for b in msg.content if b.type == "text")
        items = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
        return [f"<b>{html.escape(i['title'])}</b><br>Hypothesis: {html.escape(i['hypothesis'])}<br>"
                f"Angle: {html.escape(i['angle'])} · Hook: {html.escape(i['hook'])} · KPI: {html.escape(i['kpi'])}" for i in items]
    except Exception as e:
        print("idea generation failed:", e)
        return ["(AI test ideas could not be generated this run.)"]


def bar(n, total):
    return f'<div class="bar"><span style="width:{100*n/max(total,1):.0f}%"></span></div>'


def report(summary, white_space, idea_lines, tagger, src, out, chg):
    e = html.escape
    cards = ""
    for b, s in summary.items():
        rows = "".join(f"<tr><td>{e(k)}</td><td>{v}</td><td>{bar(v, s['ads'])}</td></tr>" for k, v in s["angles"].items())
        heads = "".join(f"<li>{e(h)} <small>×{n}</small></li>" for h, n in s["headlines"].items())
        longest = "".join(f'<li>{('<a href="'+e(u)+'">'+e(h)+'</a>') if u else e(h)} – {d} days live</li>' for h, d, u in s["longest_running"])
        cards += f"""<section><h2>{e(b)}</h2><p><b>{s['ads']}</b> ads in sample · <b>{s['new_ads_per_week']}</b> new ads / week</p>
        <table><tr><th>Angle</th><th>Ads</th><th></th></tr>{rows}</table>
        <h3>Top headlines</h3><ul>{heads}</ul><h3>Longest running (proxy for winners)</h3><ul>{longest}</ul></section>"""
    chg_rows = ""
    for b, c in chg.items():
        angles = ", ".join(f"{e(k)} ({v})" for k, v in c["new_angles_this_week"].items()) or "–"
        since = (f"{c['new_since_last_run']} new / {c['stopped_since_last_run']} stopped since {c['last_run_date']}"
                 + (f"; new angles: {e(', '.join(c['angles_new_vs_last_run']))}" if c["angles_new_vs_last_run"] else "")
                 if "last_run_date" in c else "first run")
        chg_rows += f"<tr><td>{e(b)}</td><td>{c['new_this_week']}</td><td>{angles}</td><td>{since}</td></tr>"
    chg_html = f"""<section><h2>What changed</h2><table><tr><th>Brand</th><th>New ads (7 days)</th><th>Angles of new ads</th><th>Vs last run</th></tr>{chg_rows}</table></section>"""
    ws = "".join(f"<li>{e(w)}</li>" for w in white_space) or "<li>None – {e(CLIENT)} covers all competitor angles</li>"
    ids = "".join(f'<li style="margin-bottom:10px">{i if i.startswith("<b>") else e(i)}</li>' for i in idea_lines)
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Creative Radar – {e(CLIENT)}</title>
<style>body{{font-family:system-ui;max-width:980px;margin:40px auto;padding:0 16px;color:#1b1b1b}}
section{{border:1px solid #e3e3e3;border-radius:10px;padding:16px 20px;margin:16px 0}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 8px;text-align:left;border-bottom:1px solid #f0f0f0}}
.bar{{background:#f1f1f1;border-radius:4px;height:10px;width:160px}}.bar span{{display:block;height:10px;background:#3b6cf6;border-radius:4px}}
small{{color:#888}}.note{{color:#666;font-size:13px}}</style></head><body>
<h1>Creative Radar – {e(CLIENT)} vs competitors</h1>
<p class="note">Source: {e(src)} · Tagger: {e(tagger)} · Generated {dt.date.today()}</p>
<section><h2>White space for {e(CLIENT)}</h2><p>Angles competitors run that {e(CLIENT)} doesn't:</p><ul>{ws}</ul>
<h3>Test ideas</h3><ol>{ids}</ol></section>{chg_html}{cards}
<p class="note">Note: the Ad Library shows no spend or results, so "days live" is used as a rough proxy for what works.</p></body></html>"""
    open(out, "w", encoding="utf-8").write(page)
    print("Report written to", out)


# ---------- 4. email ----------
def send_email(to, summary, white_space, idea_lines, chg, report_path):
    """Email a short summary + the full report via Gmail (needs an app password)."""
    import smtplib, ssl, re
    from email.message import EmailMessage
    sender, pwd = os.getenv("RADAR_EMAIL_FROM", ""), os.getenv("RADAR_EMAIL_APP_PASSWORD")
    e = html.escape
    changes = "".join(f"<li><b>{e(b)}</b>: {c['new_this_week']} new ads this week"
                      + (f" ({e(', '.join(c['new_angles_this_week']))})" if c["new_angles_this_week"] else "")
                      + (f"; new angles vs last run: {e(', '.join(c['angles_new_vs_last_run']))}"
                         if c.get("angles_new_vs_last_run") else "") + "</li>" for b, c in chg.items())
    brands = "".join(f"<li><b>{e(b)}</b>: {s['ads']} ads, top angle: {e(next(iter(s['angles']), '–'))}</li>"
                     for b, s in summary.items())
    ws = ", ".join(white_space) or "none"
    ideas_html = "".join(f"<li>{i if i.startswith('<b>') else e(i)}</li>" for i in idea_lines[:5])
    body = f"""<h2>Creative Radar – weekly summary for {e(CLIENT)}</h2>
<h3>What changed</h3><ul>{changes}</ul><h3>Brands</h3><ul>{brands}</ul>
<h3>White space</h3><p>{e(ws)}</p><h3>Test ideas</h3><ol>{ideas_html}</ol>
<p style="color:#888">Full report attached.</p>"""
    msg = EmailMessage()
    msg["Subject"] = f"Creative Radar – {CLIENT} vs competitors – {dt.date.today():%d %b %Y}"
    msg["From"], msg["To"] = sender, to
    msg.set_content(re.sub("<[^>]+>", "", body))
    msg.add_alternative(body, subtype="html")
    msg.add_attachment(open(report_path, "rb").read(), maintype="text", subtype="html", filename="creative_radar_report.html")
    if sender and pwd:  # send directly via Gmail
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, pwd); smtp.send_message(msg)
        print("Summary emailed to", to)
    else:  # no credentials: save a ready-to-send draft that opens in your mail app
        msg["X-Unsent"] = "1"
        path = "output/creative_radar_email.eml"
        open(path, "wb").write(bytes(msg))
        print(f"Email draft saved: {path}  (open it, check, click Send)")


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--token", required=True); f.add_argument("--country", default="DE")
    f.add_argument("--out", default="data/ads_latest.csv")
    r = sub.add_parser("run"); r.add_argument("--input", required=True); r.add_argument("--today", default=None)
    r.add_argument("--email-to", default=None, help="send the summary to this address")
    for x in (f, r):
        x.add_argument("--brands", default=None, help='e.g. "Our brand,Competitor A" (skip the menu)')
    a = p.parse_args()
    global CLIENT, CHANGES
    all_brands, CLIENT = load_brands()
    chosen = pick_brands(all_brands, a.brands)
    if a.cmd == "fetch":
        return fetch(a.token, a.country, a.out, {b: all_brands[b] for b in chosen})
    ads = [x for x in csv.DictReader(open(a.input, encoding="utf-8")) if x["brand"] in chosen]
    today = dt.datetime.fromisoformat(a.today) if a.today else dt.datetime.now()
    tagger = tag_all(ads)
    summary, white = analyse(ads, today)
    CHANGES = changes(ads, today)
    json.dump({"summary": summary, "white_space": white, "changes": CHANGES},
              open("output/summary.json", "w"), ensure_ascii=False, indent=1)
    idea_lines = ideas(summary, white)
    report(summary, white, idea_lines, tagger, a.input, "output/report.html", CHANGES)
    if a.email_to:
        send_email(a.email_to, summary, white, idea_lines, CHANGES, "output/report.html")


if __name__ == "__main__":
    sys.exit(main())
