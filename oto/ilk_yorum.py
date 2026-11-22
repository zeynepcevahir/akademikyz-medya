"""İlk yorum ve yorum cevapları: her hesabın kendi sesiyle, parçalardan kurulan doğal cümleler.

Parçalar (giriş + gövde + kapanış) gönderi/yorum kimliğine göre seçilir; böylece aynı cümle sık tekrarlanmaz
ve robot havası olmaz. Emoji ölçülü: her cümlede değil, en çok bir-iki tane.
Sabitleme (pin) API ile yapılamaz; istenirse telefondan elle sabitlenir.
"""
import hashlib

# --- İlk yorum ------------------------------------------------------------------------------
ILK = {
    "tr": {
        "giris": ["", "", "Küçük bir not:", "Bir de şunu merak ediyorum:", "Hazır buradayken:"],
        "govde": [
            "sen bu adımların hangisinde takılıyorsun?",
            "bunu ilk hangi yazında deneyeceksin?",
            "senin tez/makale sürecinde en çok vakit yiyen kısım hangisi?",
            "listeye ne eklerdin? Yorumlardan topluyorum, sonraki gönderiye koyarım",
            "tez yazan bir arkadaşın varsa ona da gönder, eminim işine yarar",
            "tezle boğuşan bir arkadaşına gönder, bugün ona iyilik yap",
            "kaydet, bir de makale yazan bir arkadaşına at",
            "kaydet ki yazmaya oturduğunda elinin altında olsun",
            "kendi yöntemin farklıysa yaz, ben de öğrenmek isterim",
        ],
        "son": ["", "👇", "📌", "🙂", "✍️", "🤝", ""],
    },
    "en": {
        "giris": ["", "", "Quick question:", "Curious about this one:"],
        "govde": [
            "which step slows you down the most?",
            "what would you add to this list? I'm collecting ideas for the next post",
            "which tip are you trying first?",
            "send it to a friend who's writing their thesis right now",
            "save this and share it with your writing buddy",
            "save it for your next writing session",
            "if you do it differently, tell me how. Always happy to learn",
        ],
        "son": ["", "👇", "📌", "🙂", "✍️", ""],
    },
    "osm": {
        "giris": ["", "", "Bir ricamız var:", "Merak ediyoruz:"],
        "govde": [
            "bu dizeyi Osmanlıca harflerle okuyabildiniz mi? Okuyuşunuzu yoruma yazın",
            "sıradaki şarkıyı siz seçin; hangisini Osmanlıca görmek istersiniz?",
            "en çok hangi kelime gönlünüze dokundu?",
            "harfleri tekrar etmek için kaydedip ara ara dönebilirsiniz",
            "bu şarkıyı seven birini etiketleyin, beraber okuyalım",
            "Osmanlıca öğrenmek isteyen bir dostunuza gönderin",
            "kaydedin, bir de şarkıyı seven birine gönderin",
            "okurken zorlandığınız harf olduysa yazın, bir sonraki videoda onu açalım",
        ],
        "son": ["", "🎶", "🌿", "", "✍️"],
    },
    "hasbihal": {
        "giris": ["", "", "Hasbihal kabilinden:", "Bir sorumuz var:"],
        "govde": [
            "bu cümleyi siz nasıl okurdunuz? Okuyuşunuzu yoruma bırakın",
            "cümlede size en yakın gelen kelime hangisi oldu?",
            "bu sözü kime göndermek isterdiniz?",
            "Osmanlıca okumaya hevesli bir dostunuza gönderin",
            "kaydedin; bir de bu sözü hatırlaması gereken birine gönderin",
            "sıradaki kelimeyi siz seçin; hangisini Osmanlıca görmek istersiniz?",
            "okuyabildiyseniz yoruma bir ✍️ bırakın, kaç kişi olduğumuzu görelim",
            "harfleri tekrar etmek için kaydedebilirsiniz",
        ],
        "son": ["", "🌿", "", "📜", "🤍"],
    },
    "tez": {
        "giris": ["", "", "Merak ediyoruz:", "Bir sorumuz var:"],
        "govde": [
            "bu tezde sizi en çok düşündüren nokta ne oldu?",
            "kendi alanınızdan tanıtılmasını istediğiniz bir tez var mı? Tez numarasını yazmanız yeter",
            "bu konuda çalışan bir arkadaşınızı etiketleyin, işine yarayabilir",
            "tez yazan bir arkadaşınıza gönderin, belki aradığı kaynak budur",
            "kaydedin; bu alanda çalışan bir arkadaşınıza da gönderin",
            "kaynakçanıza eklemek için kaydedebilirsiniz",
            "siz bu soruyu nasıl çalışırdınız?",
        ],
        "son": ["", "📚", "", "👇", "🙏"],
    },
}

# --- Yorum cevapları (övgü/teşekkür/kısa tepki) -------------------------------------------
CEVAP = {
    "tr": {
        "bas": ["Çok teşekkürler", "Ne güzel", "Teşekkür ederim", "Sevindim", "Çok sağ ol", "Ah ne hoş"],
        "orta": ["", "işine yaradıysa ne mutlu", "yazmaya devam", "sıradakinde de görüşelim",
                 "kaydetmeyi unutma", "takıldığın yer olursa yaz"],
        "son": ["🙌", "🙏", "🙂", "", "✨", "💪", ""],
    },
    "en": {
        "bas": ["Thank you so much", "Love that", "Thanks a lot", "So glad", "Appreciate it"],
        "orta": ["", "hope it helps", "keep writing", "see you in the next one", "save it for later"],
        "son": ["🙌", "🙏", "🙂", "", "✨"],
    },
    "osm": {
        "bas": ["Eline, gönlüne sağlık", "Çok teşekkür ederiz", "Ne güzel", "Var olun", "Teşekkürler"],
        "orta": ["", "sıradaki şarkıda da bekleriz", "harfleri tekrar etmeyi unutmayın",
                 "güzel kalpli dinleyicimiz", "birlikte okumaya devam"],
        "son": ["🌿", "🎶", "", "🤍", ""],
    },
    "hasbihal": {
        "bas": ["Eline, gönlüne sağlık", "Ne güzel okumuşsunuz", "Çok teşekkür ederiz", "Var olun", "Hoş geldiniz"],
        "orta": ["", "sıradaki kelimede de bekleriz", "hasbihale devam", "tebrik ederiz",
                 "okumaya devam edin"],
        "son": ["🌿", "", "✍️", "🤍", ""],
    },
    "tez": {
        "bas": ["İlginiz için teşekkürler", "Çok teşekkür ederiz", "Ne güzel", "Sevindik"],
        "orta": ["", "sıradaki tezde görüşmek üzere", "tanıtılmasını istediğiniz tezleri de yazabilirsiniz",
                 "kaydetmeyi unutmayın"],
        "son": ["📚", "🙏", "", "🙂"],
    },
}


def _sec(liste, anahtar, tuz):
    return liste[int(hashlib.sha256(f"{tuz}:{anahtar}".encode()).hexdigest(), 16) % len(liste)]


def _birlestir(giris, govde, son):
    if giris and "?" in govde:  # "Merak ediyoruz:" gibi girişler yalnız soru cümlelerine
        metin = f"{giris} {govde}"
    else:
        metin = govde[0].upper() + govde[1:]
    if not metin.endswith(("?", ".", "!")):
        metin += "."
    return f"{metin} {son}".strip() if son else metin


def sec(hesap, anahtar):
    """Gönderi için ilk yorum (anahtar: klasör adı)."""
    p = ILK.get(hesap)
    if not p:
        return None
    return _birlestir(_sec(p["giris"], anahtar, "g"), _sec(p["govde"], anahtar, "b"), _sec(p["son"], anahtar, "s"))


def cevap(hesap, anahtar, kullanici=None):
    """Övgü/teşekkür yorumuna kısa, doğal cevap (anahtar: yorum kimliği)."""
    p = CEVAP.get(hesap, CEVAP["tr"])
    bas, orta, son = _sec(p["bas"], anahtar, "1"), _sec(p["orta"], anahtar, "2"), _sec(p["son"], anahtar, "3")
    metin = f"{bas}, {orta}" if orta else bas
    metin = metin + ("!" if not orta and _sec([0, 1], anahtar, "4") else "")
    if son:
        metin = f"{metin} {son}"
    if kullanici and _sec([0, 0, 1], anahtar, "5"):  # bazen adıyla hitap
        metin = f"@{kullanici} {metin}"
    return metin
