#!/usr/bin/env python3
"""سيرفر محلي لموقع صوتي: بيجيب الصوت بس من يوتيوب بـ yt-dlp ويبعته ستريمنج.
التشغيل:  pip install -U yt-dlp   ثم   python server.py   ثم افتح  http://localhost:8000
"""
import json, os, sys, time, threading, urllib.request, urllib.parse, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

try:
    import yt_dlp
except ImportError:
    sys.exit("ثبّت yt-dlp الأول:  pip install -U yt-dlp")

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))
CACHE_TTL = 3 * 3600
cache = {}
lock = threading.Lock()


def opts(**kw):
    o = {"quiet": True, "no_warnings": True, "noplaylist": True}
    o.update(kw)
    return o


def get_info(vid):
    with lock:
        c = cache.get(vid)
    if c and time.time() - c[0] < CACHE_TTL:
        return c[1]
    with yt_dlp.YoutubeDL(opts()) as y:
        info = y.extract_info("https://www.youtube.com/watch?v=" + vid, download=False)
    fmts = [
        f for f in info.get("formats", [])
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
        and f.get("url") and f.get("protocol") in ("https", "http")
    ]
    m4a = [f for f in fmts if f.get("ext") == "m4a"]
    if m4a:
        fmts = m4a  # أوسع توافق (موبايل/آيفون)
    fmts.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0)
    if not fmts:
        raise RuntimeError("مفيش صيغة صوت متاحة للفيديو ده")
    data = {
        "title": info.get("title"),
        "by": info.get("uploader") or info.get("channel"),
        "thumb": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid,
        "fmts": [{"url": f["url"], "abr": f.get("abr") or f.get("tbr") or 0,
                  "headers": f.get("http_headers") or {}} for f in fmts],
    }
    with lock:
        cache[vid] = (time.time(), data)
    return data


def pick(data, q):
    f = data["fmts"]
    return f[0] if q == "low" else f[-1] if q == "high" else f[(len(f) - 1) // 2]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.parse_qs(u.query)
        g = lambda k, d="": (p.get(k) or [d])[0]
        try:
            if u.path in ("/", "/index.html"):
                b = open(os.path.join(HERE, "audio-tube.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path == "/api/ping":
                self.json({"ok": True})
            elif u.path == "/api/search":
                with yt_dlp.YoutubeDL(opts(extract_flat=True)) as y:
                    r = y.extract_info("ytsearch20:" + g("q"), download=False)
                items = []
                for e in r.get("entries") or []:
                    i = e.get("id") or ""
                    if len(i) == 11:
                        items.append({"id": i, "title": e.get("title"),
                                      "by": e.get("uploader") or e.get("channel"),
                                      "dur": e.get("duration") or 0,
                                      "thumb": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % i})
                self.json({"items": items})
            elif u.path == "/api/info":
                d = get_info(g("id"))
                f = pick(d, g("q", "low"))
                self.json({"title": d["title"], "by": d["by"], "thumb": d["thumb"], "abr": f["abr"]})
            elif u.path == "/api/audio":
                self.audio(g("id"), g("q", "low"))
            else:
                self.json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.json({"error": str(e)[:300]}, 500)
            except Exception:
                pass

    def audio(self, vid, q):
        d = get_info(vid)
        f = pick(d, q)
        req = urllib.request.Request(f["url"], headers=f["headers"])
        rng = self.headers.get("Range")
        if rng:
            req.add_header("Range", rng)
        try:
            r = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            with lock:
                cache.pop(vid, None)  # الرابط انتهى، هيتجدد في الطلب الجاي
            self.json({"error": "HTTP %s من يوتيوب" % e.code}, 502)
            return
        self.send_response(r.status)
        for h in ("Content-Type", "Content-Length", "Content-Range"):
            v = r.headers.get(h)
            if v:
                self.send_header(h, v)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            r.close()


if __name__ == "__main__":
    print("شغال على  http://localhost:%d  (اقفل بـ Ctrl+C)" % PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
