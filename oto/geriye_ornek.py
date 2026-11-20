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
for tur in ("IMAGE", "CAROUSEL_ALBUM", "VIDEO"):
    secim += [("hasbihal", m) for m in hb if m["media_type"] == tur][:1]
osm = ig_medya(ayar["hesaplar"]["osm"]["ig_id"])
secim += [("osm", m) for m in osm if m["media_type"] == "VIDEO"][-1:]
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
