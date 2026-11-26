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
cevap=json.loads(urllib.request.urlopen(u).read().decode()); print(cevap.get("id"))
SERI={"Istanbul After Dark":(1,5,6,7),"Stone and Dome":(2,8,9,10),"Lost Secrets":(3,11,12),"Maps and Dates":(13,)}
try:
    no=int(siradaki["dosya"].split("_")[0]); pl=json.load(open("quiet/playlists.json"))
    ad=next(a for a,n in SERI.items() if no in n)
    if ad in pl:
        r=urllib.request.Request("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet",json.dumps({"snippet":{"playlistId":pl[ad],"resourceId":{"kind":"youtube#video","videoId":cevap["id"]}}}).encode(),{"Authorization":"Bearer "+t,"Content-Type":"application/json"})
        urllib.request.urlopen(r)
except Exception as e: print("Oynatma listesine eklenemedi:",e)
biten.append(siradaki["dosya"]); json.dump(biten,open(YAYIN,"w"))
