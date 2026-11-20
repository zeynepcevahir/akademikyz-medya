"""Instagram ve YouTube yorumlarına otomatik, ölçülü cevap.

- Her yoruma en fazla 1 cevap; hesabın kendi yorumlarına ve zaten cevaplanmışlara dokunulmaz.
- Övgü/teşekkür/kısa tepki: hesabın üslubunda şablondan sıcak cevap (ücretsiz).
- Soru: ANTHROPIC_API_KEY varsa ucuz modelle (Haiku) kısa, dikkatli cevap; emin değilse cevap yok.
  Anahtar yoksa cevaplanmaz, size bildirilir.
- Olumsuz/hassas yorum: cevap yok, size bildirilir (depoda "Cevap bekleyen yorumlar" kaydı).
- Beğenme API ile yapılamıyor.
Kayıt: oto/yorum_durum.json. Bir çalışmada hesap başına en çok 15 cevap.
"""
import datetime as dt, hashlib, json, os, re, subprocess, sys, tomllib
from pathlib import Path
import requests

EV = Path(__file__).resolve().parent
sys.path.insert(0, str(EV))
import yt_ortak  # noqa: E402

DURUM = EV / "yorum_durum.json"
GUN = 7
SINIR = 15
YT = {"osm": "YT_OSM_TOKEN", "hasbihal": "YT_HASBIHAL_TOKEN"}

SABLON = {
    "tr": ["Çok teşekkürler! 🙌 Yazmaya devam 💪", "Ne güzel, teşekkür ederiz! Kaydetmeyi unutma 📌",
           "Teşekkürler 🙏 Sıradaki gönderide görüşmek üzere!", "Harika, işine yaradıysa çok sevindik 🌱"],
    "en": ["Thank you so much! 🙌 Keep writing 💪", "Glad it helped! Don't forget to save it 📌",
           "Thanks a lot 🙏 See you in the next post!"],
    "osm": ["Eline, gönlüne sağlık 🌿", "Çok teşekkürler, güzel kalpli dinleyicimiz 🎶",
            "Ne güzel! Sıradaki şarkıda da bekleriz 🎶", "Teşekkürler 🌿 Harfleri tekrar etmeyi unutma!"],
    "hasbihal": ["Eline, gönlüne sağlık 🌿", "Ne güzel okudun, tebrikler ✍️", "Teşekkürler, hasbihale hoş geldin 🌿",
                 "Çok sevindik 🌿 Sıradaki kelimede de bekleriz!"],
}
OLUMSUZ = re.compile(r"(?i)\b(kötü|berbat|rezil|yalan|yanlış|hata|saçma|aptal|spam|dolandır|şikayet|terrible|wrong|"
                     r"fake|scam|stupid|hate|siyaset|din\b|allah|peygamber|ölüm|intihar|hasta)\w*")
SORU = re.compile(r"(?i)\?|\b(nasıl|neden|niçin|nerede|nereden|hangi|kaç|ne zaman|nedir|mısınız|misiniz|"
                  r"musunuz|müsünüz|how|why|where|which|what|can you|could you)\b")


def sablon(hesap, anahtar):
    h = SABLON.get(hesap, SABLON["tr"])
    return h[int(hashlib.sha256(anahtar.encode()).hexdigest(), 16) % len(h)]


def yz_cevap(hesap, metin, baglam):
    anahtar = os.environ.get("ANTHROPIC_API_KEY")
    if not anahtar:
        return None
    dil = "İngilizce" if hesap == "en" else "Türkçe"
    istem = (f"Bir Instagram/YouTube eğitim hesabının ({hesap}) gönderisine gelen yoruma {dil}, en çok 2 kısa "
             f"cümleyle, sıcak ve doğru bir cevap yaz. Emin değilsen, konu hassassa ya da bilgi gerektiriyorsa "
             f"yalnızca BELIRSIZ yaz. Gönderi metni: {baglam[:500]}\nYorum: {metin[:500]}\nCevap:")
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
            "x-api-key": anahtar, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-5-5", "max_tokens": 120, "messages": [{"role": "user", "content": istem}]})
        t = r.json()["content"][0]["text"].strip()
        return None if "BELIRSIZ" in t or len(t) > 300 else t
    except Exception:
        return None


def karar(hesap, metin, baglam, anahtar):
    """(cevap ya da None, bildirim_sebebi ya da None)"""
    if OLUMSUZ.search(metin):
        return None, "olumsuz/hassas"
    if SORU.search(metin):
        c = yz_cevap(hesap, metin, baglam)
        return (c, None) if c else (None, "soru")
    if len(metin) > 220:
        return None, "uzun yorum"
    return sablon(hesap, anahtar), None


def ig_isle(hesap, ig_id, kullanici, durum, bildir):
    tok = os.environ["IG_TOKEN"]
    g = "https://graph.facebook.com/v24.0/"
    sinir = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=GUN)
    medya = requests.get(g + f"{ig_id}/media", timeout=30, params={
        "fields": "id,timestamp,caption,permalink", "limit": 15, "access_token": tok}).json().get("data", [])
    sayi = 0
    for m in medya:
        if dt.datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00")) < sinir:
            continue
        r = requests.get(g + f"{m['id']}/comments", timeout=30, params={
            "fields": "id,text,username,replies{username}", "limit": 50, "access_token": tok}).json()
        if "error" in r:
            print(f"{hesap}: yorumlar okunamadı ({r['error'].get('message', '')[:80]})"); return
        for c in r.get("data", []):
            if c["id"] in durum or c.get("username", "").lower() == kullanici.lower():
                continue
            if any(x.get("username", "").lower() == kullanici.lower() for x in c.get("replies", {}).get("data", [])):
                durum[c["id"]] = {"durum": "zaten_cevapli"}; continue
            cevap, sebep = karar(hesap, c.get("text", ""), m.get("caption", ""), c["id"])
            if cevap and sayi < SINIR:
                rr = requests.post(g + f"{c['id']}/replies", timeout=30, data={"message": cevap, "access_token": tok})
                durum[c["id"]] = {"durum": "cevaplandi" if rr.status_code == 200 else "hata", "hesap": hesap}
                sayi += 1
            elif sebep:
                durum[c["id"]] = {"durum": "bildirildi", "hesap": hesap}
                bildir.append(f"- **{hesap}** ({sebep}): \"{c.get('text', '')[:150]}\" — {m['permalink']}")
    print(f"{hesap} (Instagram): {sayi} cevap")


def yt_isle(hesap, yenileme, durum, bildir):
    anahtar = yt_ortak.erisim(yenileme)
    h = {"Authorization": "Bearer " + anahtar}
    ch = requests.get("https://www.googleapis.com/youtube/v3/channels", headers=h, timeout=30,
                      params={"part": "id", "mine": "true"}).json()["items"][0]["id"]
    r = requests.get("https://www.googleapis.com/youtube/v3/commentThreads", headers=h, timeout=30, params={
        "part": "snippet,replies", "allThreadsRelatedToChannelId": ch, "maxResults": 50, "order": "time"}).json()
    sinir = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=GUN)
    sayi = 0
    for t in r.get("items", []):
        top = t["snippet"]["topLevelComment"]
        s = top["snippet"]
        if top["id"] in durum or s.get("authorChannelId", {}).get("value") == ch:
            continue
        if dt.datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00")) < sinir:
            continue
        if any(x["snippet"].get("authorChannelId", {}).get("value") == ch for x in t.get("replies", {}).get("comments", [])):
            durum[top["id"]] = {"durum": "zaten_cevapli"}; continue
        cevap, sebep = karar(hesap, s.get("textOriginal", ""), "", top["id"])
        if cevap and sayi < SINIR:
            rr = requests.post("https://www.googleapis.com/youtube/v3/comments?part=snippet", headers=h, timeout=30,
                               json={"snippet": {"parentId": top["id"], "textOriginal": cevap}})
            durum[top["id"]] = {"durum": "cevaplandi" if rr.status_code == 200 else "hata", "hesap": hesap}
            sayi += 1
        elif sebep:
            durum[top["id"]] = {"durum": "bildirildi", "hesap": hesap}
            bildir.append(f"- **{hesap} YouTube** ({sebep}): \"{s.get('textOriginal', '')[:150]}\" — "
                          f"https://youtube.com/watch?v={s.get('videoId', '')}")
    print(f"{hesap} (YouTube): {sayi} cevap")


def main():
    ayar = tomllib.loads((EV / "ayar.toml").read_text(encoding="utf-8"))
    durum = json.loads(DURUM.read_text(encoding="utf-8")) if DURUM.exists() else {}
    bildir = []
    for hesap, h in ayar["hesaplar"].items():
        try:
            ig_isle(hesap, h["ig_id"], h["kullanici_adi"], durum, bildir)
        except Exception as e:
            print(f"{hesap} Instagram: {type(e).__name__}")
        if os.environ.get(YT.get(hesap, "-")) and os.environ.get("YT_CLIENT_ID"):
            try:
                yt_isle(hesap, os.environ[YT[hesap]], durum, bildir)
            except Exception as e:
                print(f"{hesap} YouTube: {type(e).__name__}")
    yt_ortak.depoya_yaz(DURUM, durum, "yorum cevapları")
    if bildir and os.environ.get("GITHUB_ACTIONS"):
        subprocess.run([str(EV / "uyari.sh"), "Cevap bekleyen yorumlar",
                        "Otomatik cevaplanmayan yorumlar (soru, uzun ya da hassas):\n\n" + "\n".join(bildir)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
