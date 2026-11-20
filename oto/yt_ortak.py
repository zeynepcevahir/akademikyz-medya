"""YouTube ve Instagram için ortak yardımcılar (geriye dönük yükleme ve çift yükleme kontrolü)."""
import datetime as dt, difflib, json, os, re, subprocess, time
from pathlib import Path
import requests

def norm(t):
    t = re.sub(r"#\S+|https?://\S+", " ", (t or "").lower())
    return re.sub(r"\W+", " ", t).strip()

def benzerlik(a, b):
    a, b = norm(a)[:300], norm(b)[:300]
    if not a or not b:
        return 0.0
    return max(difflib.SequenceMatcher(None, a, b).ratio(), difflib.SequenceMatcher(None, a[:80], b[:80]).ratio())

def erisim(yenileme):
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": yenileme, "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Google yetkisi alınamadı (HTTP {r.status_code}).")
    return r.json()["access_token"]

def yt_videolar(anahtar, sinir=None):
    h = {"Authorization": "Bearer " + anahtar}
    ch = requests.get("https://www.googleapis.com/youtube/v3/channels", headers=h, timeout=30,
                      params={"part": "contentDetails", "mine": "true"}).json()["items"][0]
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    out, sayfa = [], None
    while True:
        r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", headers=h, timeout=30, params={
            "part": "snippet", "playlistId": pl, "maxResults": 50, "pageToken": sayfa}).json()
        for it in r.get("items", []):
            s = it["snippet"]
            out.append({"video_id": s["resourceId"]["videoId"], "metin": s["title"] + " " + s.get("description", ""),
                        "tarih": s["publishedAt"]})
        sayfa = r.get("nextPageToken")
        if not sayfa or (sinir and len(out) >= sinir):
            return out

def youtube_da_var_mi(metin, videolar, esik=0.6):
    """Metni dolu bir içerik YouTube'da zaten varsa video_id döner."""
    if len(norm(metin)) < 15:
        return None
    for v in videolar:
        if benzerlik(metin, v["metin"]) >= esik:
            return v["video_id"]
    return None

def depoya_yaz(yol, veri, not_):
    yol = Path(yol)
    yol.write_text(json.dumps(veri, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not os.environ.get("GITHUB_ACTIONS"):
        return True
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True, timeout=120)
    git("add", str(yol))
    if git("diff", "--cached", "--quiet").returncode == 0:
        return True
    git("commit", "-q", "-m", f"YouTube kaydı: {not_}", "--", str(yol))
    for _ in range(4):
        if git("push", "-q").returncode == 0:
            return True
        git("pull", "-q", "--rebase", "--autostash")
        time.sleep(3)
    return False


def ilk_yorum(anahtar, video_id, hesap):
    """Videoya kanal adına ilk yorumu ekler (force-ssl izni gerekir). Hata olursa None."""
    import ilk_yorum as iy
    mesaj = iy.sec(hesap, video_id)
    if not mesaj:
        return None
    try:
        r = requests.post("https://www.googleapis.com/youtube/v3/commentThreads?part=snippet", timeout=30,
                          headers={"Authorization": "Bearer " + anahtar},
                          json={"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": mesaj}}}})
        return r.json().get("id") if r.status_code == 200 else None
    except Exception:
        return None
