#!/usr/bin/env python3
"""Quiet Empires kanalının API ile yapılabilen ayarları: açıklama, anahtar kelime, ülke, dil, banner, seri oynatma listeleri.
Yapılamayanlar (kanal adı, @handle, profil resmi) Studio'dan elle. Gizli alanlar: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_QUIET_TOKEN."""
import json, os, sys, urllib.request, urllib.parse
if not os.environ.get("YT_QUIET_TOKEN"): sys.exit("YT_QUIET_TOKEN yok.")
def tok():
    return json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token",urllib.parse.urlencode({"client_id":os.environ["YT_CLIENT_ID"],"client_secret":os.environ["YT_CLIENT_SECRET"],"refresh_token":os.environ["YT_QUIET_TOKEN"],"grant_type":"refresh_token"}).encode()))["access_token"]
T=tok(); B="https://www.googleapis.com/youtube/v3/"
def api(path,body=None,method=None,data=None,ctype="application/json"):
    h={"Authorization":"Bearer "+T}
    if body is not None: data=json.dumps(body).encode(); h["Content-Type"]="application/json"
    elif data is not None: h["Content-Type"]=ctype
    r=urllib.request.Request(path,data,h,method=method)
    try: return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e: print("HATA",path.split('?')[0],e.code,e.read().decode()[:300]); return None
ch=api(B+"channels?part=id,snippet,brandingSettings&mine=true")["items"][0]
print("Kanal:",ch["snippet"]["title"],ch["id"])
bs=ch.get("brandingSettings",{}); bs.setdefault("channel",{}); bs.setdefault("image",{})
bs["channel"]["description"]="Short, sourced history in 30 seconds: empires, domes, lost secrets and old maps. Every fact is checked against museum, university or primary-source pages, and the sources are listed in each description. AI-assisted visuals and music."
bs["channel"]["keywords"]="history shorts byzantine ottoman istanbul \"ancient history\" \"sourced history\""
bs["channel"]["country"]="US"; bs["channel"]["defaultLanguage"]="en"
# banner
if os.path.exists("quiet/banner.png"):
    r=api("https://www.googleapis.com/upload/youtube/v3/channelBanners/insert",data=open("quiet/banner.png","rb").read(),ctype="image/png")
    if r and r.get("url"): bs["image"]["bannerExternalUrl"]=r["url"]; print("Banner yüklendi")
api(B+"channels?part=brandingSettings",{"id":ch["id"],"brandingSettings":bs},"PUT"); print("Marka ayarları gönderildi")
# playlistler
SERI={"Istanbul After Dark":"Ottoman life in Istanbul.","Stone and Dome":"Byzantine buildings and walls.","Lost Secrets":"Weapons, machines and recipes we still puzzle over.","Maps and Dates":"Routes, maps and turning points."}
mevcut={p["snippet"]["title"]:p["id"] for p in (api(B+"playlists?part=snippet&mine=true&maxResults=50") or {}).get("items",[])}
liste={}
for ad,acik in SERI.items():
    if ad in mevcut: liste[ad]=mevcut[ad]; continue
    r=api(B+"playlists?part=snippet,status",{"snippet":{"title":ad,"description":acik},"status":{"privacyStatus":"public"}},"POST")
    if r: liste[ad]=r["id"]; print("Oynatma listesi:",ad)
json.dump(liste,open("quiet/playlists.json","w"),indent=1)
