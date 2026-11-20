"""Instagram'da yayınlanan Osmanlıca Reel'leri ilgili YouTube kanalına Shorts olarak yükler.

Bulutta "Instagram paylaşımı" işinin içinde, Instagram yayınından hemen sonra çalışır.
- Yalnızca osm (Şarkılarla Osmanlıca) ve hasbihal (Osmanlıca Hasbihal) hesapları, tek videolu gönderiler.
- Klasörde ayar.txt içinde "youtube=hayir" varsa ya da adında "elle" geçiyorsa atlanır (elle müzikli gönderiler).
- Çift yükleme engeli: içerik özeti (ozet) oto/youtube_durum.json'da tutulur; yükleme başlamadan
  "yukleniyor" kaydı depoya gönderilir. Sonucu belirsiz kalan yükleme otomatik tekrarlanmaz, uyarı verilir.
- Gizli bilgiler (GitHub gizli alanı): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_OSM_TOKEN, YT_HASBIHAL_TOKEN.
  Bir kanalın anahtarı yoksa o kanal sessizce atlanır.
Çıkış kodu: sorun varsa 1 (iş kırmızı biter, uyarı kaydı açılır), yoksa 0.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

EV = Path(__file__).resolve().parent
KUYRUK = EV.parent / "kuyruk"
DURUM = EV / "durum.json"
YT_DURUM = EV / "youtube_durum.json"
HESAPLAR = {"osm": "YT_OSM_TOKEN", "hasbihal": "YT_HASBIHAL_TOKEN"}
EN_ESKI = dt.timedelta(days=3)  # bundan eski Instagram yayınları yüklenmez (geriye dönük yığılma olmasın)
DENEME_SINIRI = 3


def log(msg):
    print(f"{dt.datetime.now().strftime('%H:%M:%S')} | {msg}", flush=True)


def kaydet(veri, not_):
    YT_DURUM.write_text(json.dumps(veri, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not os.environ.get("GITHUB_ACTIONS"):
        return True
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True, timeout=120)
    git("add", str(YT_DURUM))
    if git("diff", "--cached", "--quiet").returncode == 0:
        return True
    git("commit", "-q", "-m", f"YouTube kaydı: {not_}", "--", str(YT_DURUM))
    for _ in range(4):
        if git("push", "-q").returncode == 0:
            return True
        git("pull", "-q", "--rebase", "--autostash")
        time.sleep(3)
    log(f"UYARI: YouTube kaydı depoya gönderilemedi ({not_}).")
    return False


def erisim_anahtari(yenileme):
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": yenileme, "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Google yetkisi alınamadı (HTTP {r.status_code}: {r.json().get('error', '')}).")
    return r.json()["access_token"]


def baslik_aciklama(metin):
    govde = "\n".join(l for l in metin.splitlines() if not l.strip().startswith("#")).strip()
    etiketler = re.findall(r"#(\w+)", metin)
    ilk = re.split(r"(?<=[.!?…])\s", govde, maxsplit=1)[0] if govde else "Osmanlıca"
    ilk = re.sub(r"[<>]", "", ilk).strip()
    if len(ilk) > 88:
        ilk = ilk[:87].rsplit(" ", 1)[0] + "…"
    baslik = f"{ilk} #Shorts"
    aciklama = re.sub(r"[<>]", "", metin.strip()) + "\n\n#Shorts"
    return baslik, aciklama[:4900], etiketler[:15]


def yukle(anahtar, video, baslik, aciklama, etiketler):
    meta = {
        "snippet": {"title": baslik, "description": aciklama, "tags": etiketler,
                    "categoryId": "27", "defaultLanguage": "tr", "defaultAudioLanguage": "tr"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    boyut = video.stat().st_size
    r = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {anahtar}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(boyut)},
        data=json.dumps(meta), timeout=60)
    if r.status_code != 200 or "Location" not in r.headers:
        raise ValueError(f"Yükleme başlatılamadı (HTTP {r.status_code}: {r.text[:200]}).")
    with video.open("rb") as f:
        r2 = requests.put(r.headers["Location"], data=f, timeout=600,
                          headers={"Authorization": f"Bearer {anahtar}", "Content-Type": "video/mp4",
                                   "Content-Length": str(boyut)})
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"Yükleme sonucu belirsiz (HTTP {r2.status_code}).")
    return r2.json()["id"], r2.json().get("status", {}).get("privacyStatus", "")


def main():
    if not (os.environ.get("YT_CLIENT_ID") and os.environ.get("YT_CLIENT_SECRET")):
        log("YouTube bağlantısı henüz kurulmamış; atlandı.")
        return 0
    isler = json.loads(DURUM.read_text(encoding="utf-8"))["isler"]
    yt = json.loads(YT_DURUM.read_text(encoding="utf-8")) if YT_DURUM.exists() else {}
    simdi = dt.datetime.now(dt.timezone.utc)
    sorun = False
    for kayit in isler.values():
        hesap, ozet = kayit.get("hesap"), kayit.get("ozet")
        if hesap not in HESAPLAR or kayit.get("durum") != "yayinlandi" or not ozet:
            continue
        if simdi - dt.datetime.fromisoformat(kayit["yayin_zamani"]) > EN_ESKI:
            continue
        onceki = yt.get(ozet, {})
        if onceki.get("durum") == "yuklendi":
            continue
        if onceki.get("durum") == "yukleniyor":
            if not onceki.get("uyarildi"):
                log(f"{kayit['klasor']}: önceki YouTube yüklemesinin sonucu belirsiz; kanalı elle kontrol edin.")
                onceki["uyarildi"] = True
                kaydet(yt, kayit["klasor"] + " belirsiz")
                sorun = True
            continue
        if onceki.get("durum") == "hatali" or onceki.get("deneme", 0) >= DENEME_SINIRI:
            continue
        yenileme = os.environ.get(HESAPLAR[hesap])
        if not yenileme:
            continue
        klasor = KUYRUK / kayit.get("arsiv", "") if kayit.get("arsiv") else None
        if not klasor or not klasor.is_dir():
            klasor = KUYRUK / "yayinlandi" / kayit["klasor"]
        if not klasor.is_dir():
            klasor = KUYRUK / kayit["klasor"]
        videolar = sorted(klasor.glob("*.mp4")) + sorted(klasor.glob("*.mov")) if klasor.is_dir() else []
        ayar = (klasor / "ayar.txt").read_text(encoding="utf-8") if (klasor / "ayar.txt").exists() else ""
        if "elle" in kayit["klasor"] or re.search(r"(?im)^youtube\s*=\s*hayir", ayar):
            yt[ozet] = {"klasor": kayit["klasor"], "durum": "atlandi"}
            kaydet(yt, kayit["klasor"] + " atlandı")
            continue
        if len(videolar) != 1 or len(list(klasor.glob("*.[jp][pn]g"))):
            continue  # tek videolu Reel değil
        metin = (klasor / "metin.txt").read_text(encoding="utf-8") if (klasor / "metin.txt").exists() else ""
        baslik, aciklama, etiketler = baslik_aciklama(metin)
        try:
            import yt_ortak
            mevcut = yt_ortak.youtube_da_var_mi(metin, yt_ortak.yt_videolar(erisim_anahtari(yenileme), sinir=50))
        except Exception:
            mevcut = None
        if mevcut:
            yt[ozet] = {"klasor": kayit["klasor"], "hesap": hesap, "durum": "yuklendi", "video_id": mevcut,
                        "not": "kanalda zaten vardı"}
            log(f"{kayit['klasor']}: kanalda zaten var ({mevcut}); yüklenmedi.")
            kaydet(yt, kayit["klasor"] + " zaten var")
            continue
        deneme = onceki.get("deneme", 0) + 1
        yt[ozet] = {"klasor": kayit["klasor"], "hesap": hesap, "durum": "yukleniyor", "deneme": deneme,
                    "baslama": simdi.isoformat(timespec="seconds")}
        if not kaydet(yt, kayit["klasor"] + " yükleniyor"):
            yt[ozet]["durum"] = "bekliyor"
            YT_DURUM.write_text(json.dumps(yt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            sorun = True
            continue
        try:
            anahtar = erisim_anahtari(yenileme)
            vid, gizlilik = yukle(anahtar, videolar[0], baslik, aciklama, etiketler)
            yt[ozet].update(durum="yuklendi", video_id=vid, gizlilik=gizlilik,
                            adres=f"https://youtube.com/shorts/{vid}")
            log(f"{kayit['klasor']}: YouTube'a yüklendi: https://youtube.com/shorts/{vid} ({gizlilik})")
            if gizlilik and gizlilik != "public":
                log("UYARI: YouTube videoyu herkese açık yapmadı (API projesi henüz denetimden geçmemiş olabilir).")
        except ValueError as e:  # yükleme hiç başlamadı: güvenle yeniden denenebilir
            yt[ozet].update(durum="bekliyor" if deneme < DENEME_SINIRI else "hatali", son_hata=str(e)[:300])
            log(f"{kayit['klasor']}: {e}")
            sorun = True
        except Exception as e:
            if "yetkisi" in str(e):  # yetki hatası: video gönderilmedi
                yt[ozet].update(durum="bekliyor", son_hata=str(e)[:300])
            else:
                yt[ozet]["son_hata"] = str(e)[:300]  # durum "yukleniyor" kalır: tekrar yükleme yapılmaz
            log(f"{kayit['klasor']}: {e}")
            sorun = True
        kaydet(yt, kayit["klasor"] + " " + yt[ozet]["durum"])
    return 1 if sorun else 0


if __name__ == "__main__":
    sys.exit(main())
