#!/usr/bin/env python3
"""Free APM / Product Associate job alerts for Bangalore. Stdlib only."""
import html as _html, hashlib, json, os, re, smtplib, ssl, sys, urllib.request, urllib.error
from urllib.parse import urlparse
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor

SEEN_FILE = "seen.json"

# ---- What you want -------------------------------------------------------
TITLE_INCLUDE = re.compile(
    r"associate product manager|\bapm\b|product associate|associate product|"
    r"junior product manager|product analyst|product management intern|"
    r"product intern|graduate product|product trainee|product management associate|"
    r"associate,? product|product operations|product ops|technical product associate|"
    r"product manager.{0,10}(fresher|entry|0-1|0-2)|"
    r"founder.{0,2}s? office.{0,40}product|product.{0,40}founder.{0,2}s? office",
    re.I)
TITLE_EXCLUDE = re.compile(
    r"business analyst|senior|\bsr\b|staff|principal|lead|director|head of|vp\b|group product|"
    r"\bii\b|\biii\b|manager,? (growth|marketing)|engineer|designer",
    re.I)
LOCATION_INCLUDE = re.compile(r"bangalore|bengaluru|blr", re.I)
ALLOW_REMOTE_INDIA = True   # also alert for "Remote - India" roles
# --------------------------------------------------------------------------

UA = {"User-Agent": "Mozilla/5.0 (job-alert-bot)", "Accept": "application/json, text/html;q=0.9"}

def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)

def post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)

def get_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")

def clean(t):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()

def scan_page(url, label):
    """Generic fallback: read a static careers page and pick out lines that look like our target titles."""
    html = get_text(url)
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = _html.unescape(re.sub(r"<[^>]+>", "\n", html))
    out = []
    for line in dict.fromkeys(l.strip() for l in text.splitlines()):
        if 3 < len(line) < 120 and TITLE_INCLUDE.search(line):
            h = hashlib.md5((url + line).encode()).hexdigest()[:12]
            out.append(dict(id=f"pg-{label}-{h}", title=line, location="", url=url, loc_unknown=True))
    return out

def from_url(u):
    """Turn a careers-page link into (ats, slug)."""
    p = urlparse(u)
    host = p.netloc.lower()
    parts = [x for x in p.path.split("/") if x]
    first = parts[0] if parts else ""
    if host.endswith("myworkdayjobs.com"):
        parts = [x for x in parts if not re.fullmatch(r"[a-z]{2}[-_][A-Za-z]{2}", x)]
        return ("workday", f"{host}|{parts[0]}")
    if host.endswith("greenhouse.io"):          return ("greenhouse", first)
    if host == "jobs.lever.co":                 return ("lever", first)
    if host == "jobs.ashbyhq.com":              return ("ashby", first)
    if host.endswith("smartrecruiters.com"):    return ("smartrecruiters", first)
    if host == "apply.workable.com":            return ("workable", first)
    if host.endswith(".recruitee.com"):         return ("recruitee", host.split(".")[0])
    if host.endswith(".bamboohr.com"):          return ("bamboohr", host.split(".")[0])
    if host.endswith(".keka.com"):              return ("keka", host.split(".")[0])
    if ".zohorecruit." in host:                 return ("zoho", host)
    if host.endswith(".freshteam.com"):         return ("freshteam", host.split(".")[0])
    return ("page", u)

def fetch(ats, slug):
    """Return list of dicts: id, title, location, url, company."""
    out = []
    if ats == "greenhouse":
        d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        for j in d.get("jobs", []):
            out.append(dict(id=f"gh-{slug}-{j['id']}", title=j["title"],
                            location=(j.get("location") or {}).get("name", ""),
                            url=j["absolute_url"], gh_id=j["id"]))
    elif ats == "lever":
        d = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in d:
            out.append(dict(id=f"lv-{slug}-{j['id']}", title=j["text"],
                            location=(j.get("categories") or {}).get("location", "") or "",
                            url=j["hostedUrl"], desc=j.get("descriptionPlain", "") or ""))
    elif ats == "ashby":
        d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        for j in d.get("jobs", []):
            if j.get("isListed", True):
                out.append(dict(id=f"ab-{slug}-{j['id']}", title=j["title"],
                                location=j.get("location", "") or "",
                                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}",
                                desc=j.get("descriptionPlain", "") or ""))
    elif ats == "smartrecruiters":
        d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
        for j in d.get("content", []):
            loc = j.get("location") or {}
            out.append(dict(id=f"sr-{slug}-{j['id']}", title=j["name"],
                            location=f"{loc.get('city','')}, {loc.get('country','')}",
                            url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"))
    elif ats == "workday":
        host, site = slug.split("|")
        tenant = host.split(".")[0]
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        offset, total, label = 0, 1, tenant
        while offset < min(total, 100):
            d = post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "product"})
            total = d.get("total", 0)
            posts = d.get("jobPostings", [])
            if not posts:
                break
            for p in posts:
                path = p.get("externalPath", "")
                out.append(dict(id=f"wd-{tenant}-{path}", title=p.get("title", ""),
                                location=p.get("locationsText", "") or "",
                                url=f"https://{host}/{site}{path}", detail=f"{base}{path}"))
            offset += 20
    elif ats == "workable":
        d = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        for j in d.get("jobs", []):
            loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
            out.append(dict(id=f"wk-{slug}-{j.get('shortcode')}", title=j.get("title", ""), location=loc,
                            url=j.get("url") or f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
                            desc=j.get("description", "") or ""))
    elif ats == "recruitee":
        d = get_json(f"https://{slug}.recruitee.com/api/offers/")
        for j in d.get("offers", []):
            loc = j.get("location") or ", ".join(x for x in [j.get("city"), j.get("country")] if x)
            out.append(dict(id=f"rc-{slug}-{j['id']}", title=j.get("title", ""), location=loc or "",
                            url=j.get("careers_url", ""), desc=j.get("description", "") or ""))
    elif ats == "bamboohr":
        d = get_json(f"https://{slug}.bamboohr.com/careers/list")
        for j in d.get("result", []):
            l = j.get("location") or {}
            loc = ", ".join(x for x in [l.get("city"), l.get("state")] if x)
            out.append(dict(id=f"bb-{slug}-{j['id']}", title=j.get("jobOpeningName", ""), location=loc,
                            url=f"https://{slug}.bamboohr.com/careers/{j['id']}"))
    elif ats == "zoho":
        html = get_text(f"https://{slug}/jobs/Careers")
        m = re.search(r'id="jobs"[^>]*value="([^"]*)"', html) or re.search(r'value="([^"]*)"[^>]*id="jobs"', html)
        if not m:
            raise ValueError("Zoho jobs data not found on page")
        for p in json.loads(_html.unescape(m.group(1))):
            loc = ", ".join(x for x in [p.get("City"), p.get("State"), p.get("Country")] if x)
            out.append(dict(id=f"zo-{slug}-{p.get('id')}", title=p.get("Posting_Title") or p.get("Job_Opening_Name") or "",
                            location=loc, url=f"https://{slug}/jobs/Careers/{p.get('id')}"))
    elif ats == "freshteam":
        host = f"{slug}.freshteam.com"
        html = get_text(f"https://{host}/jobs")
        for href, inner in re.findall(r'<a[^>]+href="(/jobs/[^"#?]+)"[^>]*>(.*?)</a>', html, re.S):
            title = clean(inner)
            if title:
                out.append(dict(id=f"ft-{slug}-{href}", title=title, location="",
                                url=f"https://{host}{href}", loc_unknown=True))
    elif ats == "keka":
        out = scan_page(f"https://{slug}.keka.com/careers/", slug)
    elif ats == "page":
        out = scan_page(slug, urlparse(slug).netloc)
    label = slug
    if ats == "workday":  label = slug.split("|")[0].split(".")[0]
    if ats == "page":     label = urlparse(slug).netloc
    for j in out:
        j.setdefault("company", label)
    return out


import html as _html
EXP_RE = re.compile(r"(\d{1,2})\s*(?:\+|-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years|yrs|year)", re.I)
MAX_MIN_YEARS = 1   # skip jobs whose stated minimum experience is above this

def min_years(text):
    """Smallest 'N years' figure stated near the word experience; None if not stated."""
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    found = []
    for m in EXP_RE.finditer(text):
        ctx = text[max(0, m.start() - 80): m.end() + 80].lower()
        if "experience" in ctx or "exp" in ctx:
            found.append(int(m.group(1)))
    return min(found) if found else None

def too_senior(j):
    if j.get("gh_id") and not j.get("desc"):
        try:
            d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{j['company']}/jobs/{j['gh_id']}")
            j["desc"] = d.get("content", "")
        except Exception:
            return False
    if j.get("detail") and not j.get("desc"):
        try:
            j["desc"] = get_json(j["detail"]).get("jobPostingInfo", {}).get("jobDescription", "")
        except Exception:
            return False
    y = min_years(j.get("desc", ""))
    j["exp"] = "not stated" if y is None else f"{y}+ yrs stated"
    return y is not None and y > MAX_MIN_YEARS

def loc_ok(loc):
    if LOCATION_INCLUDE.search(loc):
        return True
    if re.search(r"\d+\s+locations?|multiple locations", loc, re.I):   # Workday hides the list; check the listing
        return True
    return ALLOW_REMOTE_INDIA and re.search(r"remote", loc, re.I) and re.search(r"india|\bin\b", loc, re.I)

def matches(j):
    return TITLE_INCLUDE.search(j["title"]) and not TITLE_EXCLUDE.search(j["title"]) and (loc_ok(j["location"]) or j.get("loc_unknown"))

RESOLVED_FILE = "resolved.json"   # remembers which ATS each company uses
RETRY_DAYS = 7                    # re-try unresolved companies weekly
ATS_ORDER = ["greenhouse", "lever", "ashby", "smartrecruiters"]

def slug_variants(name):
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    vs = [base.replace(" ", ""), base.replace(" ", "-")]
    return list(dict.fromkeys(v for v in vs if v))

def load_companies():
    """Lines are a Company Name (auto-detect), ats:slug, or a careers-page URL."""
    cos = []
    for line in open("companies.txt"):
        line = line.split("#")[0].strip()
        if line:
            cos.append(line)
    return cos

def resolve(name, resolved, now):
    """Return (ats, slug) or None. Auto-detects by trying each ATS."""
    if name.lower().startswith("http"):
        return from_url(name)
    if ":" in name:
        a, s_ = [x.strip() for x in name.split(":", 1)]
        return (a.lower(), s_)
    r = resolved.get(name)
    if r and r.get("ats"):
        return (r["ats"], r["slug"])
    if r and now - r.get("tried", 0) < RETRY_DAYS * 86400:
        return None
    for slug in slug_variants(name):
        for ats in ATS_ORDER:
            try:
                jobs = fetch(ats, slug)
                if not jobs:      # some ATS APIs return 200 + empty list for ANY name
                    continue
                resolved[name] = {"ats": ats, "slug": slug}
                print(f"  detected {name} -> {ats}:{slug} ({len(jobs)} jobs)")
                return (ats, slug)
            except Exception:
                continue
    resolved[name] = {"ats": None, "tried": now}
    return None

def run_one(c):
    try:
        return c, fetch(*c), None
    except urllib.error.HTTPError as e:
        return c, [], f"HTTP {e.code}"
    except Exception as e:
        return c, [], str(e)[:60]

def send_email(jobs):
    user, pw, to = os.environ["SMTP_USER"], os.environ["SMTP_PASS"], os.environ.get("EMAIL_TO") or os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"] = f"🚀 {len(jobs)} new APM/Product Associate job(s) in Bangalore"
    msg["From"], msg["To"] = user, to
    lines = [f"{j['title']}\n{j['company']} | {j['location'] or 'location not shown - check listing'} | Experience: {j.get('exp','not stated')}\nAPPLY NOW: {j['url']}\n" for j in jobs]
    msg.set_content("\n".join(lines))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)

def main():
    seen = set(json.load(open(SEEN_FILE))) if os.path.exists(SEEN_FILE) else set()
    import time
    now = int(time.time())
    resolved = json.load(open(RESOLVED_FILE)) if os.path.exists(RESOLVED_FILE) else {}
    names = load_companies()
    companies, no_ats = [], []
    for n in names:
        r = resolve(n, resolved, now)
        (companies if r else no_ats).append(r or n)
    json.dump(resolved, open(RESOLVED_FILE, "w"), indent=1)
    new, dead = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for c, jobs, err in ex.map(run_one, companies):
            if err:
                dead.append(f"{c[0]}:{c[1]} ({err})")
                continue
            for j in jobs:
                if matches(j) and j["id"] not in seen:
                    seen.add(j["id"])
                    if too_senior(j):
                        print("  skipped (experience):", j["title"], "|", j["company"], "|", j["exp"])
                        continue
                    new.append(j)
    print(f"Checked {len(companies)} boards, {len(dead)} unreachable, {len(new)} new matches.")
    if no_ats:
        print("Not found automatically - paste their careers-page URL into companies.txt:", *no_ats, sep="\n  ")
    if dead:
        print("Unreachable (wrong slug or not on this ATS):", *dead, sep="\n  ")
    if new:
        if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"):
            send_email(new)
            print("Email sent.")
        else:
            print("No SMTP secrets set; would have emailed:", *[f"{j['title']} - {j['url']}" for j in new], sep="\n  ")
    json.dump(sorted(seen), open(SEEN_FILE, "w"), indent=0)

if __name__ == "__main__":
    sys.exit(main())
