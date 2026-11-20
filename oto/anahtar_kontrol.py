"""IG_TOKEN anahtarının geçerliliğini ve bitiş tarihini yazdırır (anahtarın kendisini yazdırmaz)."""
import json, os, sys, time, urllib.parse, urllib.request

token = os.environ.get("IG_TOKEN", "")
if not token:
    print("IG_TOKEN yok."); sys.exit(1)
q = urllib.parse.urlencode({"input_token": token, "access_token": token})
try:
    with urllib.request.urlopen(f"https://graph.facebook.com/v24.0/debug_token?{q}", timeout=30) as r:
        d = json.load(r).get("data", {})
except Exception as e:
    print("Anahtar bilgisi alınamadı:", type(e).__name__); sys.exit(1)
bitis = d.get("expires_at", 0)
print("Geçerli:", d.get("is_valid"))
print("Tür:", d.get("type"))
print("Bitiş:", "süresiz" if not bitis else time.strftime("%Y-%m-%d", time.gmtime(bitis)))
print("Hesap sayısı (izinli):", len(d.get("granular_scopes", [{}])[0].get("target_ids", []) or []))
if not d.get("is_valid"):
    sys.exit(1)
if bitis and bitis - time.time() < 15 * 86400:
    print("UYARI: anahtarın süresi 15 günden az."); sys.exit(1)
