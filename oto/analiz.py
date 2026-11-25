"""Paylaşım performansı: Instagram Graph API ve YouTube Data API'den YALNIZCA OKUR, analiz.json yazar."""
import json, os, sys, time, tomllib, datetime as dt
import requests
sys.path.insert(0, os.path.dirname(__file__))
import yt_ortak

AYAR = tomllib.load(open(os.path.join(os.path.dirname(__file__), "ayar.toml"), "rb"))
V = AYAR.get("api_surumu", "v24.0")
G = f"https://graph.facebook.com/{V}/"
MEDYA_METRIK = ["reach", "saved", "shares", "views", "total_interactions", "likes", "comments"]


def get(yol, token, **p):
    p["access_token"] = token
    for _ in range(3):
        r = requests.get(G + yol, params=p, timeout=60)
        if r.status_code < 500:
            return r.json()
        time.sleep(3)
    return r.json()


def hata(d):
    return (d.get("error") or {}).get("message", "")[:200] if isinstance(d, dict) else ""


def ig(h, token):
    out = {"hesap": h["kullanici_adi"], "medya": [], "hatalar": []}
    p = get(h["ig_id"], token, fields="username,followers_count,follows_count,media_count")
    out["profil"] = p if not hata(p) else {"hata": hata(p)}
    bugun = int(time.time())
    for m in ("follower_count", "reach"):
        d = get(f"{h['ig_id']}/insights", token, metric=m, period="day", since=bugun - 29 * 86400, until=bugun)
        out[f"gunluk_{m}"] = ([(v["end_time"][:10], v["value"]) for v in (d.get("data") or [{}])[0].get("values", [])]
                              if not hata(d) else hata(d))
    url = f"{h['ig_id']}/media"
    prm = {"fields": "id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink", "limit": 50}
    while url:
        d = get(url, token, **prm) if not url.startswith("http") else requests.get(url, timeout=60).json()
        if hata(d):
            out["hatalar"].append(hata(d))
            break
        for x in d.get("data", []):
            x["caption"] = (x.get("caption") or "")[:90]
            ins = {}
            r = get(f"{x['id']}/insights", token, metric=",".join(MEDYA_METRIK))
            if hata(r):
                for mt in MEDYA_METRIK:
                    r1 = get(f"{x['id']}/insights", token, metric=mt)
                    if not hata(r1):
                        ins[mt] = r1["data"][0]["values"][0]["value"]
            else:
                ins = {i["name"]: i["values"][0]["value"] for i in r.get("data", [])}
            if not ins:
                out["hatalar"].append("insights: " + hata(r))
            x["ins"] = ins
            out["medya"].append(x)
        nxt = (d.get("paging") or {}).get("next")
        url, prm = nxt, {}
    out["hatalar"] = sorted(set(out["hatalar"]))[:5]
    return out


def yt(gizli, token):
    out = {"gizli": gizli, "videolar": []}
    try:
        a = yt_ortak.erisim(token)
        h = {"Authorization": "Bearer " + a}
        ch = requests.get("https://www.googleapis.com/youtube/v3/channels", headers=h, timeout=30,
                          params={"part": "snippet,statistics", "mine": "true"}).json()["items"][0]
        out["kanal"] = ch["snippet"]["title"]
        out["istatistik"] = ch["statistics"]
        ids = [v["video_id"] for v in yt_ortak.yt_videolar(a)]
        for i in range(0, len(ids), 50):
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", headers=h, timeout=30, params={
                "part": "snippet,statistics,status,contentDetails", "id": ",".join(ids[i:i + 50])}).json()
            for v in r.get("items", []):
                out["videolar"].append({"id": v["id"], "baslik": v["snippet"]["title"][:90],
                                        "tarih": v["snippet"]["publishedAt"], "durum": v["status"]["privacyStatus"],
                                        "sure": v["contentDetails"]["duration"],
                                        **{k: int(s) for k, s in v["statistics"].items()}})
    except Exception as e:
        out["hata"] = str(e)[:200]
    return out


sonuc = {"zaman": dt.datetime.utcnow().isoformat(), "ig": {}, "yt": {}}
for ad, h in AYAR["hesaplar"].items():
    token = os.environ.get("IG_TEZ_TOKEN" if ad == "tez" else "IG_TOKEN")
    if token and h.get("ig_id"):
        sonuc["ig"][ad] = ig(h, token)
for g in ("YT_OSM_TOKEN", "YT_HASBIHAL_TOKEN", "YT_TEZ_TOKEN"):
    if os.environ.get(g):
        sonuc["yt"][g] = yt(g, os.environ[g])
json.dump(sonuc, open("analiz.json", "w"), ensure_ascii=False)
for ad, o in sonuc["ig"].items():
    print("IG", ad, len(o["medya"]), "medya", o["hatalar"])
for g, o in sonuc["yt"].items():
    print("YT", g, len(o["videolar"]), o.get("hata", ""))
