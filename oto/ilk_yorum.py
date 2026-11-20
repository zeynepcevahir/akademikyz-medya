"""Yayından hemen sonra atılan ilk yorum için hesaba özel, dönüşümlü cümle havuzu.
Sabitleme (pin) API ile yapılamaz; istenirse telefondan elle sabitlenir."""
import hashlib

HAVUZ = {
    "tr": [
        "Bunu sonra uygulamak için kaydet 📌 Sen hangi adımda takılıyorsun?",
        "Hangi kısmı ilk deneyeceksin? Yoruma bir kelime bırak, sırayla cevaplayalım 👇",
        "Tez/makale yazan bir arkadaşına gönder, işine yarar 🤝",
        "Bu konuda en çok zorlandığın şey ne? Bir sonraki gönderiyi ona göre hazırlayalım.",
        "Kaydet, yazmaya oturduğunda yanında olsun 📌",
        "Sen bunu nasıl yapıyorsun? Kendi yöntemini yaz, birlikte geliştirelim 👇",
    ],
    "en": [
        "Save this for your next writing session 📌 Which step do you struggle with most?",
        "Send this to a friend who's writing a thesis 🤝",
        "What would you add to this list? Drop it below 👇",
        "Which tip will you try first? Tell us in one word.",
        "Save it now, thank yourself later 📌",
    ],
    "osm": [
        "Bu dizeyi Osmanlıca harflerle okuyabildin mi? Okuyuşunu yoruma yaz 👇",
        "Sıradaki şarkıyı sen seç: hangi şarkıyı Osmanlıca görmek istersin? 🎶",
        "En sevdiğin kelime hangisi oldu? Yoruma yaz 👇",
        "Kaydet, harfleri tekrar etmek için geri dön 📌",
        "Bu şarkıyı seven birini etiketle 🎶",
        "Hangi harfi okumakta zorlandın? Bir sonraki videoda onu açalım.",
    ],
    "hasbihal": [
        "Bu kelimeyi nasıl okurdun? Okuyuşunu yoruma yaz 👇",
        "Bu cümlede sana en yakın kelime hangisi? 💭",
        "Kaydet, harfleri tekrar etmek için geri dön 📌",
        "Bu sözü kime göndermek isterdin? Etiketle 💌",
        "Sıradaki kelimeyi sen seç: hangi kelimeyi Osmanlıca görmek istersin?",
        "Okuyabildiysen yoruma ✍️ bırak, sayalım bakalım kaç kişiyiz.",
    ],
}


def sec(hesap, anahtar):
    havuz = HAVUZ.get(hesap)
    if not havuz:
        return None
    return havuz[int(hashlib.sha256(anahtar.encode()).hexdigest(), 16) % len(havuz)]
