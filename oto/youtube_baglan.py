#!/usr/bin/env python3
"""YouTube kanalını bulut paylaşım sistemine bağlar (Mac'te bir kez, her kanal için çalıştırılır).

Kullanım (Terminal'de, bu dosyanın bulunduğu klasörde):
    python3 youtube_baglan.py osm        # Şarkılarla Osmanlıca kanalı
    python3 youtube_baglan.py hasbihal   # Osmanlıca Hasbihal kanalı

Gerekenler: aynı klasörde Google'dan indirilen client_secret*.json dosyası.
Tarayıcı açılır; doğru kanalı seçip izin verin. Anahtar GitHub gizli alanına kaydedilir
(gh yüklüyse otomatik; değilse ekrandaki değerleri GitHub > Settings > Secrets > Actions'a elle ekleyin).
"""
import glob, http.server, json, os, secrets, shutil, subprocess, sys, urllib.parse, urllib.request, webbrowser

DEPO = "zeynepcevahir/akademikyz-medya"
GIZLI = {"osm": "YT_OSM_TOKEN", "hasbihal": "YT_HASBIHAL_TOKEN"}
KAPSAM = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.force-ssl"

if len(sys.argv) != 2 or sys.argv[1] not in GIZLI:
    sys.exit("Kullanım: python3 youtube_baglan.py osm|hasbihal")
hesap = sys.argv[1]
dosyalar = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_secret*.json"))
if not dosyalar:  # tarayıcının indirdiği yer
    dosyalar = sorted(glob.glob(os.path.expanduser("~/Downloads/client_secret*.json")), key=os.path.getmtime, reverse=True)
if not dosyalar:
    sys.exit("client_secret*.json bulunamadı; Google Cloud'dan indirip bu klasöre koyun.")
istemci = json.load(open(dosyalar[0]))
istemci = istemci.get("installed") or istemci.get("web")
cid, csec = istemci["client_id"], istemci["client_secret"]

durum = secrets.token_urlsafe(16)
kod = {}
class Yakala(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] == durum:
            kod["v"] = q.get("code", [""])[0]
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write("Tamam. Bu sekmeyi kapatıp Terminal'e dönebilirsiniz.".encode())
    def log_message(self, *a): pass

sunucu = http.server.HTTPServer(("127.0.0.1", 0), Yakala)
donus = f"http://127.0.0.1:{sunucu.server_port}"
adres = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": donus, "response_type": "code", "scope": KAPSAM,
    "access_type": "offline", "prompt": "consent select_account", "state": durum})
print("Tarayıcı açılıyor. Açılmazsa bu adresi kopyalayın:\n" + adres)
webbrowser.open(adres)
while "v" not in kod:
    sunucu.handle_request()
if not kod["v"]:
    sys.exit("İzin verilmedi.")

veri = urllib.parse.urlencode({"code": kod["v"], "client_id": cid, "client_secret": csec,
                               "redirect_uri": donus, "grant_type": "authorization_code"}).encode()
t = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", veri))
yenileme = t.get("refresh_token")
if not yenileme:
    sys.exit("Google yenileme anahtarı vermedi; tekrar deneyin.")
istek = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                               headers={"Authorization": "Bearer " + t["access_token"]})
kanallar = json.load(urllib.request.urlopen(istek)).get("items", [])
print("Bağlanan kanal:", ", ".join(k["snippet"]["title"] for k in kanallar) or "(kanal bulunamadı!)")
BEKLENEN = {"osm": "Şarkılarla Osmanlıca", "hasbihal": "Osmanlıca Hasbihal"}[hesap]
if not any(k["snippet"]["title"].strip() == BEKLENEN for k in kanallar):
    sys.exit(f"YANLIŞ KANAL: '{BEKLENEN}' seçilmeliydi. Hiçbir şey kaydedilmedi; tekrar çalıştırıp doğru kanalı seçin.")

degerler = {"YT_CLIENT_ID": cid, "YT_CLIENT_SECRET": csec, GIZLI[hesap]: yenileme}
if shutil.which("gh"):
    for ad, deger in degerler.items():
        subprocess.run(["gh", "secret", "set", ad, "-R", DEPO], input=deger, text=True, check=True)
    print("Kaydedildi: GitHub gizli alanına", ", ".join(degerler), "eklendi. Bitti.")
else:
    print("gh yüklü değil. Şu üç değeri GitHub > Settings > Secrets and variables > Actions'a ekleyin:")
    for ad, deger in degerler.items():
        print(f"  {ad} = {deger}")
