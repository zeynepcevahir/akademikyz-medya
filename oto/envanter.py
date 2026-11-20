"""Osmanlıca hesapların Instagram gönderilerini YouTube kanallarıyla karşılaştırır (yalnızca okur).
Sonuç: oto/envanter.json + günlükte özet. Gizliler: IG_TOKEN, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_OSM_TOKEN, YT_HASBIHAL_TOKEN."""
import difflib, json, os, re, sys, tomllib
from pathlib import Path
import requests

EV = Path(__file__).resolve().parent
ayar = tomllib.loads((EV / "ayar.toml").read_text(encoding="utf-8"))
HESAP = {"osm": "YT_OSM_TOKEN", "hasbihal": "YT_HASBIHAL_TOKEN"}

def norm(t):
    t = re.sub(r"#\S+|https?://\S+", " ", (t or "").lower())
    return re.sub(r"\W+", " ", t).strip()

def ig_medya(ig_id):
    url = f"https://graph.facebook.com/v24.0/{ig_id}/media"
    p = {"fields": "id,media_type,media_product_type,timestamp,caption,permalink", "limit": 100,
         "access_token": os.environ["IG_TOKEN"]}
    out = []
    while url:
        r = requests.get(url, params=p, timeout=60).json()
        if "error" in r: raise SystemExit("IG hatası: " + r["error"].get("message", "")[:200])
        out += r.get("data", []); url = r.get("paging", {}).get("next"); p = None
    return out

def yt_videolar(yenileme):
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": yenileme, "grant_type": "refresh_token"}, timeout=30).json()["access_token"]
    h = {"Authorization": "Bearer " + tok}
    ch = requests.get("https://www.googleapis.com/youtube/v3/channels", headers=h,
                      params={"part": "snippet,contentDetails", "mine": "true"}, timeout=30).json()["items"][0]
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    out, sayfa = [], None
    while True:
        r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", headers=h, timeout=30, params={
            "part": "snippet,status", "playlistId": pl, "maxResults": 50, "pageToken": sayfa}).json()
        for it in r.get("items", []):
            s = it["snippet"]
            out.append({"video_id": s["resourceId"]["videoId"], "baslik": s["title"], "aciklama": s.get("description", ""),
                        "tarih": s["publishedAt"], "gizlilik": it.get("status", {}).get("privacyStatus")})
        sayfa = r.get("nextPageToken")
        if not sayfa: break
    return ch["snippet"]["title"], out

sonuc = {}
for hesap, gizli in HESAP.items():
    ig = ig_medya(ayar["hesaplar"][hesap]["ig_id"])
    kanal, yt = (None, [])
    if os.environ.get(gizli) and os.environ.get("YT_CLIENT_ID"):
        kanal, yt = yt_videolar(os.environ[gizli])
    satirlar = []
    for m in ig:
        n = norm(m.get("caption"))[:300]
        en, eslesen = 0, None
        for v in yt:
            o = max(difflib.SequenceMatcher(None, n, norm(v["baslik"] + " " + v["aciklama"])[:300]).ratio(),
                    difflib.SequenceMatcher(None, n[:80], norm(v["baslik"])[:80]).ratio())
            if o > en: en, eslesen = o, v
        satirlar.append({"ig_id": m["id"], "tur": m.get("media_product_type") or m["media_type"], "medya": m["media_type"],
                         "tarih": m["timestamp"], "permalink": m["permalink"], "metin": (m.get("caption") or "")[:120],
                         "youtube": eslesen["video_id"] if eslesen and en >= 0.45 else None,
                         "benzerlik": round(en, 2), "yt_baslik": eslesen["baslik"] if eslesen else None})
    sonuc[hesap] = {"kanal": kanal, "ig_sayisi": len(ig), "yt_sayisi": len(yt), "gonderiler": satirlar,
                    "yt_videolari": [{k: v[k] for k in ("video_id", "baslik", "tarih", "gizlilik")} for v in yt]}
    eksik = [s for s in satirlar if not s["youtube"]]
    turler = {}
    for s in eksik: turler[s["medya"]] = turler.get(s["medya"], 0) + 1
    print(f"== {hesap}: Instagram {len(ig)} gönderi | YouTube kanalı: {kanal} ({len(yt)} video) | "
          f"YouTube'da olmayan: {len(eksik)} {turler}")
    for s in satirlar:
        print(f"  {s['tarih'][:10]} {s['medya']:<14} {'YT✓ ' + s['youtube'] if s['youtube'] else 'YT✗':<16} "
              f"{s['benzerlik']:.2f} {norm(s['metin'])[:50]}")
(EV / "envanter.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
