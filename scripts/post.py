#!/usr/bin/env python3
"""Publish the next queued Osho Vigyan carousel to Instagram.

Runs inside GitHub Actions. Uses the Instagram API with Instagram Login
(graph.instagram.com). Images are fetched by Instagram from this public repo.

Env:
  IG_TOKEN     long-lived Instagram user token (repo secret, used the first time)
  TOKEN_KEY    any long password (repo secret), encrypts the rotated token in state/token.enc
  GITHUB_REPOSITORY, GITHUB_SHA  set by Actions
  DRY_RUN=1    check everything and build containers, but do not publish
  FORCE=1      post even if config.json has enabled=false (manual test run)
"""
import json, os, subprocess, sys, time, urllib.parse, urllib.request, urllib.error, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json")))
VER = CFG.get("api_version", "v23.0")
API = "https://graph.instagram.com/" + VER          # Instagram-login tokens (IGAA...)
FB_API = "https://graph.facebook.com/" + VER        # system-user / Facebook tokens (EAA...)
STATE = os.path.join(ROOT, "state", "posted.json")
TOKEN_ENC = os.path.join(ROOT, "state", "token.enc")
TOKEN_META = os.path.join(ROOT, "state", "token_meta.json")
DRY = os.environ.get("DRY_RUN") == "1"
FORCE = os.environ.get("FORCE") == "1"


def log(*a):
    print(*a, flush=True)


def call(method, url, params):
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {body}")


def ssl_crypt(mode, text):
    key = os.environ.get("TOKEN_KEY", "")
    if not key:
        return None
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A", "-pass", "env:TOKEN_KEY"]
    if mode == "d":
        args.insert(2, "-d")
    r = subprocess.run(args, input=text.encode(), capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def get_token():
    cands = []
    if os.path.exists(TOKEN_ENC):
        t = ssl_crypt("d", open(TOKEN_ENC).read().strip())
        if t:
            cands.append(("rotated", t))
    if os.environ.get("IG_TOKEN"):
        cands.append(("secret", os.environ["IG_TOKEN"].strip()))
    global API
    for name, t in cands:
        try:
            if t.startswith("EAA"):
                uid = str(CFG["ig_user_id"])
                me = call("GET", f"{FB_API}/{uid}", {"fields": "username", "access_token": t})
                me["user_id"] = uid
                API = FB_API
            else:
                me = call("GET", API + "/me", {"fields": "user_id,username", "access_token": t})
            log(f"Token OK ({name}) for @{me.get('username')}")
            return t, me
        except Exception as e:
            log(f"Token '{name}' rejected: {e}")
    sys.exit("No working Instagram token. Generate a new one and update the IG_TOKEN secret.")


def maybe_refresh(token):
    """Instagram long-lived tokens last 60 days. Refresh weekly, keep it encrypted in the repo."""
    if token.startswith("EAA"):
        return  # system-user tokens never expire
    if not os.environ.get("TOKEN_KEY"):
        log("TOKEN_KEY not set, skipping token refresh")
        return
    meta = json.load(open(TOKEN_META)) if os.path.exists(TOKEN_META) else {}
    last = meta.get("refreshed_at")
    if last and (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.datetime.fromisoformat(last)).days < 7:
        return
    try:
        r = call("GET", "https://graph.instagram.com/refresh_access_token",
                 {"grant_type": "ig_refresh_token", "access_token": token})
    except Exception as e:
        log(f"Token refresh failed (will retry next run): {e}")
        return
    enc = ssl_crypt("e", r["access_token"])
    if not enc:
        log("Could not encrypt refreshed token")
        return
    open(TOKEN_ENC, "w").write(enc + "\n")
    json.dump({"refreshed_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
               "expires_in_days": round(r.get("expires_in", 0) / 86400)}, open(TOKEN_META, "w"), indent=1)
    log("Token refreshed, valid for another", round(r.get("expires_in", 0) / 86400), "days")


def next_post(state):
    done = {p["no"] for p in state["posted"]}
    qdir = os.path.join(ROOT, "queue")
    nums = sorted(int(d) for d in os.listdir(qdir) if d.isdigit())
    todo = [n for n in nums if n not in done]
    return (todo[0] if todo else None), len(todo)


def wait_ready(cid, what):
    for _ in range(30):
        s = call("GET", f"{API}/{cid}", {"fields": "status_code,status", "access_token": TOKEN})
        code = s.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{what} container {cid} failed: {s}")
        time.sleep(5)
    raise RuntimeError(f"{what} container {cid} not ready after 150s")


def main():
    global TOKEN
    if not CFG.get("enabled") and not FORCE and not DRY:
        log("Autopost is paused (config.json enabled=false). Nothing to do.")
        return
    start = CFG.get("start_date")
    if start and not FORCE and not DRY and dt.date.today().isoformat() < start:
        log(f"Daily posting starts on {start}. Nothing to do today.")
        return
    state = json.load(open(STATE))
    n, left = next_post(state)
    if n is None:
        log("Queue is empty. Nothing posted.")
        open(os.path.join(ROOT, "state", "QUEUE_EMPTY"), "w").write("1")
        return
    TOKEN, me = get_token()
    maybe_refresh(TOKEN)
    uid = me["user_id"]
    d = f"queue/{n:03d}"
    post = json.load(open(os.path.join(ROOT, d, "post.json")))
    base = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/{os.environ.get('GITHUB_SHA','main')}/{d}"
    imgs = sorted(f for f in os.listdir(os.path.join(ROOT, d)) if f.endswith(".jpg"))
    assert 2 <= len(imgs) <= 10, f"carousel needs 2-10 images, found {len(imgs)}"
    log(f"Post {n:03d}: {post['title']} ({len(imgs)} slides), {left} left in queue")

    children = []
    for i, f in enumerate(imgs):
        p = {"image_url": f"{base}/{f}", "is_carousel_item": "true", "access_token": TOKEN}
        alts = post.get("alt_text") or []
        if i < len(alts):
            p["alt_text"] = alts[i]
        try:
            c = call("POST", f"{API}/{uid}/media", p)
        except RuntimeError as e:
            if "alt_text" in p and "alt_text" in str(e):
                log("alt_text not accepted, retrying slide without it")
                p.pop("alt_text")
                c = call("POST", f"{API}/{uid}/media", p)
            else:
                raise
        children.append(c["id"])
        log(f"  slide {i+1} container {c['id']}")
    for c in children:
        wait_ready(c, "slide")
    car = call("POST", f"{API}/{uid}/media", {"media_type": "CAROUSEL", "children": ",".join(children),
                                               "caption": post["caption"], "access_token": TOKEN})
    wait_ready(car["id"], "carousel")
    if DRY:
        log("DRY RUN: carousel container built and ready, not published.")
        return
    pub = call("POST", f"{API}/{uid}/media_publish", {"creation_id": car["id"], "access_token": TOKEN})
    mid = pub["id"]
    link = ""
    try:
        link = call("GET", f"{API}/{mid}", {"fields": "permalink", "access_token": TOKEN}).get("permalink", "")
    except Exception:
        pass
    state["posted"].append({"no": n, "media_id": mid, "permalink": link,
                            "posted_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"})
    json.dump(state, open(STATE, "w"), indent=1)
    log(f"PUBLISHED post {n:03d}: {link or mid}")
    if left - 1 < 3:
        open(os.path.join(ROOT, "state", "QUEUE_LOW"), "w").write(str(left - 1))


if __name__ == "__main__":
    main()
