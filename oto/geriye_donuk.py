"""Eski Osmanlıca Instagram gönderilerini YouTube Shorts olarak geriye dönük yükler.

Her çalışmada en fazla 1 gönderi yükler. Etkileşimin yüksek olduğu saatlerde (TR): hafta içi 18:00-19:00,
hafta sonu 12:30-13:30. Günde en çok 2 yükleme, her kanala günde en çok 1 (yeni Reel'ler 20:30/21:00'da çıkar).
NOT: 2-3 hafta sonra YouTube Studio > Analytics > Kitle > "İzleyicilerinizin YouTube'da olduğu zamanlar"
verisine göre PENCERE'yi güncelleyin.
- YouTube'da zaten olan içerik (metin benzerliği) ya da yeni sistemin yüklediği içerik atlanır.
- Kayıt: oto/youtube_geriye.json (Instagram medya kimliğiyle). Yükleme başlamadan "yukleniyor"
  kaydı depoya gönderilir; sonucu belirsiz kalan tekrar denenmez.
- Fotoğraf: 7 sn dikey video; karusel: her kare 3,5 sn; video: 9:16'ya oturtulur, en çok 59 sn.
Çıkış kodu: sorun varsa 1, yoksa 0.
"""
import datetime as dt, json, os, re, subprocess, sys, tempfile, tomllib
from pathlib import Path

import requests

EV = Path(__file__).resolve().parent
sys.path.insert(0, str(EV))
import yt_ortak  # noqa: E402
from youtube import baslik_aciklama, yukle  # noqa: E402

KAYIT = EV / "youtube_geriye.json"
HESAPLAR = {"hasbihal": "YT_HASBIHAL_TOKEN", "osm": "YT_OSM_TOKEN"}
VARSAYILAN_BASLIK = {"osm": "Şarkılarla Osmanlıca", "hasbihal": "Osmanlıca Hasbihal"}
GUNLUK = 2


def pencerede(t):
    dk = t.hour * 60 + t.minute
    if t.weekday() >= 5:  # cumartesi, pazar
        return 12 * 60 + 30 <= dk < 13 * 60 + 30
    return 18 * 60 <= dk < 19 * 60
TR = dt.timezone(dt.timedelta(hours=3))
VF = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30"


def log(m):
    print(m, flush=True)


def ig_medya(ig_id):
    url = f"https://graph.facebook.com/v24.0/{ig_id}/media"
    p = {"fields": "id,media_type,timestamp,caption,permalink,media_url,children{media_type,media_url}",
         "limit": 50, "access_token": os.environ["IG_TOKEN"]}
    out = []
    while url:
        r = requests.get(url, params=p, timeout=60).json()
        if "error" in r:
            raise RuntimeError("Instagram listesi alınamadı: " + r["error"].get("message", "")[:150])
        out += r.get("data", [])
        url, p = r.get("paging", {}).get("next"), None
    return sorted(out, key=lambda m: m["timestamp"])  # eskiden yeniye


def indir(url, yol):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(yol, "wb") as f:
            for parca in r.iter_content(1 << 20):
                f.write(parca)


def ff(*a):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True, timeout=600)


def parca(kalem, i, gecici, sure_foto):
    kaynak = Path(gecici) / f"k{i}"
    indir(kalem["media_url"], kaynak)
    cikti = Path(gecici) / f"p{i}.mp4"
    sessiz = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    if kalem["media_type"] == "VIDEO":
        ses_var = bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                       "stream=index", "-of", "csv=p=0", str(kaynak)],
                                      capture_output=True, text=True).stdout.strip())
        ses = ["-map", "0:a:0"] if ses_var else ["-map", "1:a", "-shortest"]
        ff("-i", str(kaynak), *sessiz, "-filter_complex", f"[0:v]{VF}[v]", "-map", "[v]", *ses,
           "-t", "59", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(cikti))
    else:
        ff("-loop", "1", "-t", str(sure_foto), "-i", str(kaynak), *sessiz, "-vf",
           VF + ",zoompan=z='min(zoom+0.0007,1.08)':d=1:s=1080x1920:fps=30", "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(cikti))
    return cikti


def shorts_yap(m, gecici):
    if m["media_type"] == "CAROUSEL_ALBUM":
        kalemler = m.get("children", {}).get("data", [])[:15]
        sure = 3.5
    else:
        kalemler, sure = [m], 7
    parcalar = [parca(k, i, gecici, sure) for i, k in enumerate(kalemler) if k.get("media_url")]
    if not parcalar:
        raise ValueError("Medya adresi yok.")
    son = Path(gecici) / "shorts.mp4"
    if len(parcalar) == 1:
        return parcalar[0]
    liste = Path(gecici) / "liste.txt"
    liste.write_text("".join(f"file '{p}'\n" for p in parcalar))
    ff("-f", "concat", "-safe", "0", "-i", str(liste), "-t", "59", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", str(son))
    return son


def main():
    if not (os.environ.get("YT_CLIENT_ID") and os.environ.get("IG_TOKEN")):
        log("YouTube bağlantısı yok; atlandı.")
        return 0
    simdi = dt.datetime.now(TR)
    kayit = json.loads(KAYIT.read_text(encoding="utf-8")) if KAYIT.exists() else {}
    bugun = [v for v in kayit.values() if v.get("durum") in ("yuklendi", "yukleniyor")
             and v.get("baslama", "")[:10] == simdi.date().isoformat()]
    zorla = os.environ.get("GERIYE_ZORLA") == "1"
    if not zorla:
        if not pencerede(simdi):
            log("Yükleme saati değil (hafta içi 18-19, hafta sonu 12:30-13:30)."); return 0
        if len(bugun) >= GUNLUK:
            log("Bugünkü geriye dönük yükleme sınırı doldu."); return 0
    ayar = tomllib.loads((EV / "ayar.toml").read_text(encoding="utf-8"))
    yeni_sistem = json.loads((EV / "youtube_durum.json").read_text(encoding="utf-8")) if (EV / "youtube_durum.json").exists() else {}
    # Hesapları sırayla dene: bugün hangisi daha az yüklendiyse önce o.
    sira = [h for h in HESAPLAR if zorla or not any(v.get("hesap") == h for v in bugun)]  # kanal başına günde 1
    for hesap in sira:
        yenileme = os.environ.get(HESAPLAR[hesap])
        if not yenileme:
            continue
        anahtar = yt_ortak.erisim(yenileme)
        videolar = yt_ortak.yt_videolar(anahtar)
        kullanilan = {v.get("video_id") for v in kayit.values()} | {v.get("video_id") for v in yeni_sistem.values()}
        for m in ig_medya(ayar["hesaplar"][hesap]["ig_id"]):
            onceki = kayit.get(m["id"], {})
            if onceki.get("durum") in ("yuklendi", "yukleniyor", "atlandi", "hatali", "zaten_var"):
                continue
            metin = m.get("caption") or ""
            var = yt_ortak.youtube_da_var_mi(metin, videolar, esik=0.45)
            if not var and len(yt_ortak.norm(metin)) < 15:
                # Metinsiz gönderi: aynı gün (±2 gün) yayınlanmış, başka şeyle eşleşmemiş bir video varsa var say.
                t = dt.datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
                yakin = [v for v in videolar if v["video_id"] not in kullanilan
                         and abs(dt.datetime.fromisoformat(v["tarih"].replace("Z", "+00:00")) - t) < dt.timedelta(days=2)]
                var = yakin[0]["video_id"] if yakin else None
            if var:
                kayit[m["id"]] = {"hesap": hesap, "durum": "zaten_var", "video_id": var, "permalink": m["permalink"]}
                kullanilan.add(var)
                continue
            if any(v.get("permalink") == m["permalink"] for v in yeni_sistem.values()):
                continue
            # Yükle
            kayit[m["id"]] = {"hesap": hesap, "durum": "yukleniyor", "permalink": m["permalink"],
                              "baslama": simdi.isoformat(timespec="seconds"), "deneme": onceki.get("deneme", 0) + 1}
            if not yt_ortak.depoya_yaz(KAYIT, kayit, f"geriye {hesap} {m['id']} yükleniyor"):
                log("Kayıt depoya yazılamadı; yükleme yapılmadı."); return 1
            try:
                with tempfile.TemporaryDirectory() as g:
                    video = shorts_yap(m, g)
                    baslik, aciklama, etiketler = baslik_aciklama(metin or VARSAYILAN_BASLIK[hesap])
                    vid, gizlilik = yukle(anahtar, video, baslik, aciklama, etiketler)
                kayit[m["id"]].update(durum="yuklendi", video_id=vid, gizlilik=gizlilik)
                log(f"{hesap}: {m['permalink']} -> https://youtube.com/shorts/{vid} ({gizlilik})")
                sonuc = 0
            except (ValueError, subprocess.CalledProcessError, requests.HTTPError) as e:
                # Video YouTube'a hiç gönderilmedi: güvenle tekrar denenebilir (3 denemeden sonra vazgeç).
                kayit[m["id"]].update(durum="bekliyor" if kayit[m["id"]]["deneme"] < 3 else "hatali",
                                      son_hata=str(e)[:200])
                log(f"{hesap}: {m['permalink']} hazırlanamadı: {str(e)[:200]}")
                sonuc = 1
            except Exception as e:
                kayit[m["id"]]["son_hata"] = str(e)[:200]  # sonuç belirsiz: "yukleniyor" kalır
                log(f"{hesap}: {m['permalink']} yükleme sonucu belirsiz: {str(e)[:200]}")
                sonuc = 1
            yt_ortak.depoya_yaz(KAYIT, kayit, f"geriye {hesap} {m['id']} {kayit[m['id']]['durum']}")
            return sonuc
        yt_ortak.depoya_yaz(KAYIT, kayit, f"geriye {hesap} eşleştirme")
    log("Geriye dönük kuyrukta yüklenecek gönderi kalmadı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
