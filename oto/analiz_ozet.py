"""analiz.json'dan kısa özet basar (yalnız herkese açık sayılar: beğeni, yorum, izlenme, takipçi)."""
import json, collections, datetime as dt
d = json.load(open("analiz.json"))
for k, o in d["ig"].items():
    p, m = o["profil"], o["medya"]
    print(f"OZ ## IG {o['hesap']} takipci={p.get('followers_count')} medya={p.get('media_count')} insights={'var' if any(x['ins'] for x in m) else 'YOK'}")
    for x in m:
        x["e"] = (x.get("like_count") or 0) + (x.get("comments_count") or 0)
        x["t"] = dt.datetime.fromisoformat(x["timestamp"].replace("+0000", "+00:00")) + dt.timedelta(hours=3)
    if not m:
        continue
    print("OZ  aralik", min(x["t"] for x in m).date(), "-", max(x["t"] for x in m).date(), "ort_etk", round(sum(x["e"] for x in m) / len(m), 2))
    s = sorted(m, key=lambda x: -x["e"])
    for isr, grup in (("+", s[:3]), ("-", s[-3:])):
        for x in grup:
            print("OZ ", isr, x["e"], x.get("like_count"), x.get("comments_count"), x["media_product_type"], x["media_type"],
                  x["t"].strftime("%d.%m %H:%M"), x["caption"][:55].replace("\n", " "))
    g, h = collections.defaultdict(list), collections.defaultdict(list)
    for x in m:
        g[x["media_product_type"] + "/" + x["media_type"]].append(x["e"])
        h[x["t"].hour].append(x["e"])
    print("OZ  tur", {a: (len(b), round(sum(b) / len(b), 2)) for a, b in g.items()})
    print("OZ  saat", {a: (len(b), round(sum(b) / len(b), 2)) for a, b in sorted(h.items())})
for k, o in d["yt"].items():
    v = o["videolar"]
    print(f"OZ ## YT {o.get('kanal')} {o.get('istatistik')} {o.get('hata', '')}")
    print("OZ  durum", dict(collections.Counter(x["durum"] for x in v)))
    for x in sorted(v, key=lambda x: -x.get("viewCount", 0)):
        print("OZ  ", x.get("viewCount"), x.get("likeCount"), x.get("commentCount"), x["durum"], x["tarih"][:16], x["sure"], x["baslik"][:50])
