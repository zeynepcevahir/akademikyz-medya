#!/usr/bin/env python3
"""Sıradaki Quiet Empires Shorts'unu YouTube'a yükler (günde 1). Gizli alanlar: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_QUIET_TOKEN."""
import json, os, sys, urllib.request, urllib.parse
LISTE="quiet/yayin_listesi.json"; YAYIN="quiet/yayinlandi.json"; KLASOR="quiet/videolar/"
GIZLILIK=os.environ.get("QUIET_GIZLILIK","private")  # denetim onaylanınca "public"
if not os.environ.get("YT_QUIET_TOKEN"): (print("YT_QUIET_TOKEN yok: kanal henuz baglanmadi, bu gun atlandi."), sys.exit(0))
liste=json.load(open(LISTE)); biten=json.load(open(YAYIN)) if os.path.exists(YAYIN) else []
siradaki=next((v for v in liste if v["dosya"] not in biten),None)
if not siradaki: sys.exit("Yüklenecek video kalmadı.")
t=json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token",urllib.parse.urlencode({"client_id":os.environ["YT_CLIENT_ID"],"client_secret":os.environ["YT_CLIENT_SECRET"],"refresh_token":os.environ["YT_QUIET_TOKEN"],"grant_type":"refresh_token"}).encode()))["access_token"]
meta={"snippet":{"title":siradaki["baslik"],"description":siradaki["aciklama"],"tags":siradaki["etiketler"],"categoryId":"27"},"status":{"privacyStatus":GIZLILIK,"selfDeclaredMadeForKids":False,"containsSyntheticMedia":True}}
r=urllib.request.Request("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",json.dumps(meta).encode(),{"Authorization":"Bearer "+t,"Content-Type":"application/json; charset=UTF-8","X-Upload-Content-Type":"video/mp4"})
yer=urllib.request.urlopen(r).headers["Location"]
veri=open(KLASOR+siradaki["dosya"],"rb").read()
u=urllib.request.Request(yer,veri,{"Content-Type":"video/mp4"},method="PUT")
print(urllib.request.urlopen(u).read().decode()[:300])
biten.append(siradaki["dosya"]); json.dump(biten,open(YAYIN,"w"))
