#!/usr/bin/env python3
"""Curate The Lumen's demo image pack from our own SearXNG (run ON the box).

For each catalog item: query SearXNG images -> download the first image that fetches
-> normalize to media/<id>.jpg with ffmpeg (also resizes). Pre-curated + verified, so
NO live web calls happen during the demo. Then rsync server/media/ -> Mac app/static/media/.

    python3 curate_media.py            # only fetch missing
    python3 curate_media.py --force    # refetch everything
"""
import json
import os
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "catalog.json")
OUT = os.path.join(HERE, "media")
SEARXNG = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_NOVERIFY = ssl._create_unverified_context()  # demo image fetch only


def search_images(query, n=12):
    url = f"{SEARXNG}/search?" + urllib.parse.urlencode({"q": query, "categories": "images", "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    out = []
    for res in data.get("results", [])[:n]:
        src = res.get("img_src") or ""
        if src.startswith("http"):
            out.append(src)
    return out


def download(url, dst_tmp):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ""})
    with urllib.request.urlopen(req, timeout=15, context=_NOVERIFY) as r:
        data = r.read()
    if len(data) < 4000:           # too small / placeholder
        return False
    with open(dst_tmp, "wb") as f:
        f.write(data)
    return True


def to_jpg(src_tmp, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-i", src_tmp, "-vf", "scale='min(900,iw)':-2", "-q:v", "3", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return os.path.exists(dst) and os.path.getsize(dst) > 5000


def fetch_one(query):
    """Fetch + normalize ONE image for a query -> jpg bytes (or None). Used live by
    the server to self-expand the image DB when the LLM asks for something new."""
    import tempfile
    try:
        candidates = search_images(query)
    except Exception:
        return None
    tmp = tempfile.NamedTemporaryFile(suffix="_dl", delete=False).name
    dst = tmp + ".jpg"
    try:
        for url in candidates:
            try:
                if download(url, tmp) and to_jpg(tmp, dst):
                    with open(dst, "rb") as f:
                        return f.read()
            except Exception:
                continue
        return None
    finally:
        for p in (tmp, dst):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def main():
    force = "--force" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    items = json.load(open(CATALOG))["items"]
    ok = 0
    for it in items:
        dst = os.path.join(OUT, it["id"] + ".jpg")
        if os.path.exists(dst) and not force:
            print(f"  = {it['id']:16s} (exists)")
            ok += 1
            continue
        try:
            candidates = search_images(it["query"])
        except Exception as e:
            print(f"  ! {it['id']:16s} search failed: {repr(e)[:80]}")
            continue
        got = False
        tmp = os.path.join(OUT, "_tmp_" + it["id"])
        for url in candidates:
            try:
                if not download(url, tmp):
                    continue
                if to_jpg(tmp, dst):
                    print(f"  + {it['id']:16s} {os.path.getsize(dst)//1024}KB  <- {url[:60]}")
                    got = True
                    break
            except Exception:
                continue
        if os.path.exists(tmp):
            os.remove(tmp)
        if got:
            ok += 1
        else:
            print(f"  x {it['id']:16s} NO IMAGE (try --force or tweak query)")
    print(f"\n{ok}/{len(items)} images ready in {OUT}")


if __name__ == "__main__":
    main()
