"""Geriye dönük Shorts görünüm kontrolü: birkaç örnek üretir (YouTube'a yüklemez)."""
import os, subprocess, sys, tomllib
from pathlib import Path
EV = Path(__file__).resolve().parent
sys.path.insert(0, str(EV))
from geriye_donuk import ig_medya, shorts_yap  # noqa: E402

CIKTI = EV.parent / "ornek"
CIKTI.mkdir(exist_ok=True)
ayar = tomllib.loads((EV / "ayar.toml").read_text(encoding="utf-8"))
secim = []
hb = ig_medya(ayar["hesaplar"]["hasbihal"]["ig_id"])
if os.environ.get("HAM") == "1":  # Hasbihal fotoğraflarının asıllarını indir (filigran incelemesi)
    from geriye_donuk import indir
    (CIKTI / "ham").mkdir(exist_ok=True)
    for i, m in enumerate(x for x in hb if x["media_type"] == "IMAGE"):
        indir(m["media_url"], CIKTI / "ham" / f"{i:02d}_{m['timestamp'][:10]}_{m['id']}.jpg")
    sys.exit(0)
for tur in ("IMAGE", "CAROUSEL_ALBUM", "VIDEO"):
    secim += [("hasbihal", m) for m in hb if m["media_type"] == tur][:1]
osm = ig_medya(ayar["hesaplar"]["osm"]["ig_id"])
for ad, liste in (("hasbihal", hb), ("osm", osm)):
    def adresli(m):
        return bool(m.get("media_url")) if m["media_type"] != "CAROUSEL_ALBUM" else any(
            k.get("media_url") for k in m.get("children", {}).get("data", []))
    eksik = [m["permalink"] for m in liste if not adresli(m)]
    print(f"{ad}: {len(liste)} gönderi, indirilemeyen (medya adresi yok): {len(eksik)}", *eksik[:30], sep="\n  ")
secim += [("osm", m) for m in osm if m["media_type"] == "VIDEO" and m.get("media_url")][-1:]
for i, (hesap, m) in enumerate(secim):
    g = CIKTI / f"g{i}"
    g.mkdir(exist_ok=True)
    v = shorts_yap(m, str(g))
    ad = f"{i + 1}_{hesap}_{m['media_type'].lower()}"
    os.replace(v, CIKTI / f"{ad}.mp4")
    for s in ("1", "4"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", s, "-i", str(CIKTI / f"{ad}.mp4"),
                        "-frames:v", "1", str(CIKTI / f"{ad}_{s}sn.png")], check=True)
    print(ad, m["permalink"])
