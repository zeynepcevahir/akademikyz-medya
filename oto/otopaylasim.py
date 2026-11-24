#!/usr/bin/env python3
"""Resmî Instagram API ile yerel paylaşım kuyruğu. Python 3.12+."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import ftplib
import getpass
import hashlib
import importlib
import json
import logging
import logging.handlers
import os
from pathlib import Path
import posixpath
import re
import shutil
import ssl
import subprocess
import urllib.request
import sys
import tempfile
import time
import tomllib
from urllib.parse import quote, urlsplit
import uuid


SERVICE = "OtoPaylasim"
SECRET_NAMES = ("ig_tr_token", "ig_en_token", "yukleme_sifre")
SECRET_RE = re.compile(r"^(?:ig_[a-z0-9]+_token|yukleme_sifre)$")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
VIDEO_SUFFIXES = {".mp4", ".mov"}
FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{4})_([a-z0-9]+)_(.+)$")
REMOTE_RE = re.compile(r"^[0-9a-f]{32}\.(?:jpg|jpeg|mp4|mov|txt)$")
MAX_IMAGE = 8 * 1024 * 1024


class AppError(Exception):
    """Yalnız gizli bilgi içermeyen Türkçe açıklamalar taşımalıdır."""


class ValidationError(AppError):
    pass


class AuthorizationError(AppError):
    pass


class RequestError(AppError):
    pass


class APIError(RequestError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Instagram API isteği başarısız (HTTP {status}).")


class PublicationUncertain(AppError):
    pass


def now_local():
    return dt.datetime.now().astimezone()


def stamp(value):
    return value.isoformat(timespec="seconds")


def from_stamp(value):
    return dt.datetime.fromisoformat(value)


def atomic_write(path, text):
    """Aynı dosya sisteminde atomik değiştir; dosyayı ve dizini diske aktar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".otopaylasim-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


class Redactor(logging.Filter):
    def __init__(self):
        super().__init__()
        self.secrets = set()

    def add(self, secret):
        if secret:
            self.secrets.add(str(secret))

    def clean(self, message):
        message = str(message)
        for secret in sorted(self.secrets, key=len, reverse=True):
            message = message.replace(secret, "***").replace(quote(secret, safe=""), "***")
        return re.sub(r"(?i)(access_token[\s\"']*[:=][\s\"']*)[^&\s\"']+", r"\1***", message)

    def filter(self, record):
        record.msg = self.clean(record.getMessage())
        record.args = ()
        # Dış kitaplık istisnaları URL veya parola içerebilir; traceback kaydedilmez.
        record.exc_info = None
        record.exc_text = None
        return True


def make_logger(log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"otopaylasim.{log_path}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    redactor = Redactor()
    for handler in (
        logging.handlers.RotatingFileHandler(log_path, maxBytes=1024 * 1024,
                                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.addFilter(redactor)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        logger.addHandler(handler)
    return logger, redactor


class State:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if (not isinstance(self.data, dict)
                        or not isinstance(self.data.get("isler"), dict)
                        or not isinstance(self.data.get("tokenlar"), dict)):
                    raise ValueError
                for record in self.data["isler"].values():
                    if (not isinstance(record, dict)
                            or record.get("durum") not in {"bekliyor", "yayinlaniyor", "yayinlandi", "hatali"}
                            or type(record.get("deneme_sayisi", 0)) is not int
                            or record.get("deneme_sayisi", 0) < 0):
                        raise ValueError
                    for field in ("sonraki_deneme", "yayin_zamani", "yayina_baslama"):
                        if field in record and from_stamp(record[field]).tzinfo is None:
                            raise ValueError
                    if record["durum"] == "yayinlandi" and (
                            not record.get("media_id") or not record.get("yayin_zamani")):
                        raise ValueError
                    if record["durum"] == "yayinlaniyor" and (
                            not record.get("konteyner_id") or not record.get("yayina_baslama")):
                        raise ValueError
                for token in self.data["tokenlar"].values():
                    if not isinstance(token, dict):
                        raise ValueError
            except (ValueError, TypeError, OSError):
                raise AppError("Durum dosyası okunamıyor; yinelenen yayını önlemek için işlem durduruldu.") from None
        else:
            self.data = {"surum": 1, "isler": {}, "tokenlar": {}}

    def save(self):
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def exclusive_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Keychain:
    def __init__(self, redactor, runner=subprocess.run):
        self.redactor = redactor
        self.runner = runner

    def get(self, name):
        # Bulutta (GitHub Actions) Anahtar Zinciri yok: ortam değişkeninden oku.
        env_value = os.environ.get(name.upper()) or (os.environ.get("IG_TOKEN") if name.startswith("ig_") else None)
        if env_value:
            self.redactor.add(env_value)
            return env_value
        try:
            result = self.runner(
                ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise AppError("Anahtar Zinciri okunamadı.") from None
        if result.returncode:
            raise AppError(f"Anahtar Zinciri kaydı bulunamadı veya erişilemiyor: {name}.")
        secret = result.stdout.rstrip("\r\n")
        if not secret:
            raise AppError(f"Anahtar Zinciri kaydı boş: {name}.")
        self.redactor.add(secret)
        return secret

    def set(self, name, secret):
        self.redactor.add(secret)
        if not SECRET_RE.fullmatch(name) or not secret or any(ord(c) < 32 for c in secret):
            raise AppError("Anahtar adı veya değeri geçersiz; boş değer ve kontrol karakterleri kullanılamaz.")
        # security etkileşimli ayrıştırıcısına çift tırnaklı değer gönderilir.
        # Parola process argv içinde görünmez; shell kullanılmaz, çıktılar bastırılır.
        escaped = secret.replace("\\", "\\\\").replace('"', '\\"')
        command = f'add-generic-password -U -s "{SERVICE}" -a "{name}" -w "{escaped}"\n'
        try:
            result = self.runner(["security", "-i"], input=command, text=True,
                                 capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            raise AppError("Anahtar Zinciri kaydı yazılamadı.") from None
        if result.returncode or "SecKeychain" in result.stderr or "error:" in result.stderr.lower():
            raise AppError("Anahtar Zinciri kaydı yazılamadı.")


class HTTP:
    """requests yalnız ilk gerçek istekte yüklenir; testler bu nesneyi değiştirir."""
    def __init__(self):
        self.session = None

    def request(self, method, url, **kwargs):
        if self.session is None:
            try:
                requests = importlib.import_module("requests")
            except ImportError:
                raise AppError("HTTP işlemleri için requests gerekli; kur.sh betiğini kullanın.") from None
            self.session = requests.Session()
        try:
            return self.session.request(method, url, timeout=(15, 60),
                                        allow_redirects=False, **kwargs)
        except Exception:
            raise RequestError("HTTP bağlantısı kurulamadı veya zaman aşımına uğradı.") from None


class InstagramAPI:
    def __init__(self, version, token, http, logger, sleeper=time.sleep, monotonic=time.monotonic):
        # EAA… = Facebook Login for Business / sistem kullanıcısı token'ı (Unified login): graph.facebook.com
        host = "graph.facebook.com" if str(token).startswith("EAA") else "graph.instagram.com"
        self.base = f"https://{host}/{version}"
        self.token = token
        self.http = http
        self.logger = logger
        self.sleep = sleeper
        self.monotonic = monotonic

    def call(self, method, path, **params):
        url = path if path.startswith("https://") else f"{self.base}/{path.lstrip('/')}"
        params["access_token"] = self.token
        self.logger.info("API isteği: %s %s", method, url.split("?")[0])
        response = self.http.request(method, url, **{"params" if method == "GET" else "data": params})
        if response.status_code in (401, 403):
            raise AuthorizationError("Instagram yetkilendirmesi başarısız; token'ı denetleyin.")
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise RequestError("API yanıtı geçerli JSON değil.") from None
        if not isinstance(data, dict):
            raise RequestError("API yanıt biçimi geçersiz.")
        error = data.get("error")
        if response.status_code in (401, 403) or (isinstance(error, dict) and (
                error.get("code") == 190 or error.get("type") == "OAuthException")):
            raise AuthorizationError("Instagram yetkilendirmesi başarısız; token'ı denetleyin.")
        if not 200 <= response.status_code < 300 or error:
            raise APIError(response.status_code)
        return data

    def create(self, ig_id, **params):
        data = self.call("POST", f"{ig_id}/media", **params)
        if not data.get("id"):
            raise RequestError("API konteyner kimliği döndürmedi.")
        return str(data["id"])

    def wait(self, container_id):
        deadline = self.monotonic() + 600
        interval = 2.0
        while True:
            data = self.call("GET", container_id, fields="status_code")
            status = data.get("status_code")
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise RequestError(f"Medya konteyneri hazırlanamadı: {status}.")
            if status != "IN_PROGRESS":
                raise RequestError("Medya konteynerinin durumu tanınmıyor.")
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise RequestError("Medya konteyneri için 10 dakikalık bekleme süresi doldu.")
            self.sleep(min(interval, remaining))
            interval = min(interval * 1.5, 30)

    def build(self, ig_id, kind, urls, caption, options):
        if kind == "karusel":
            children = []
            for url, video in urls:
                params = {"is_carousel_item": "true"}
                if video:
                    params.update(media_type="VIDEO", video_url=url)
                else:
                    params["image_url"] = url
                child = self.create(ig_id, **params)
                self.wait(child)
                children.append(child)
            parent = self.create(ig_id, media_type="CAROUSEL", children=",".join(children), caption=caption)
        elif kind == "reel":
            parent = self.create(ig_id, media_type="REELS", video_url=urls[0][0],
                                 caption=caption, **options)
        else:
            parent = self.create(ig_id, image_url=urls[0][0], caption=caption)
        self.wait(parent)
        return parent


@dataclasses.dataclass
class Candidate:
    path: Path
    scheduled: dt.datetime | None
    account: str | None
    error: str | None = None


@dataclasses.dataclass
class Post:
    candidate: Candidate
    caption: str
    media: list[Path]
    kind: str
    options: dict
    digest: str

    @property
    def key(self):
        return self.candidate.path.name + ":" + self.digest


def parse_candidate(path):
    match = FOLDER_RE.fullmatch(path.name)
    if not match:
        return Candidate(path, None, None, "Klasör adı YYYY-MM-DD_HHMM_hesap_ad biçiminde olmalı.")
    try:
        scheduled = dt.datetime.strptime(match[1], "%Y-%m-%d_%H%M").astimezone()
    except ValueError:
        return Candidate(path, None, match[2], "Klasör adındaki tarih veya saat geçersiz.")
    return Candidate(path, scheduled, match[2])


def scan_queue(queue):
    if not queue.exists():
        return []
    candidates = [parse_candidate(path) for path in queue.iterdir()
                  if path.is_dir() and not path.is_symlink()
                  and not path.name.startswith((".", "_"))
                  and path.name not in {"yayinlandi", "hatali"}]
    return sorted(candidates, key=lambda c: (c.scheduled.timestamp() if c.scheduled else 0, c.path.name))


def syncing(path, now):
    # Bulutta dosyalar her çalışmada yeni checkout edilir; eşitleme beklemesi yalnız Mac'te anlamlı.
    if os.environ.get("GITHUB_ACTIONS"):
        return False
    return any(now.timestamp() - p.stat().st_mtime < 300
               for p in [path, *path.rglob("*")])


def fingerprint(path):
    digest = hashlib.sha256()
    for item in sorted(path.iterdir(), key=lambda p: p.name):
        if item.name in {"SONUC.txt", "HATA.txt"}:
            continue
        if item.is_symlink():
            raise ValidationError("Paylaşım klasöründe sembolik bağlantı kullanılamaz.")
        if not item.is_file():
            continue
        name = item.name.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(item.stat().st_size.to_bytes(8, "big"))
        with item.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def validate(candidate):
    if candidate.error:
        raise ValidationError(candidate.error)
    digest = fingerprint(candidate.path)
    caption_path = candidate.path / "metin.txt"
    if not caption_path.is_file():
        raise ValidationError("metin.txt zorunludur.")
    try:
        caption = caption_path.read_text(encoding="utf-8")
    except UnicodeError:
        raise ValidationError("metin.txt UTF-8 kodlamasında olmalı.") from None
    if len(caption) > 2200:
        raise ValidationError("Açıklama 2200 karakter sınırını aşıyor.")
    if len(re.findall(r"#[\w]+", caption)) > 30:
        raise ValidationError("Açıklama 30 hashtag sınırını aşıyor.")
    media = sorted((p for p in candidate.path.iterdir() if p.is_file()
                    and p.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES), key=lambda p: p.name)
    if not 1 <= len(media) <= 10:
        raise ValidationError("Paylaşımda 1–10 medya dosyası bulunmalı.")
    if any(p.stat().st_size == 0 for p in media):
        raise ValidationError("Medya dosyaları boş olamaz.")
    kind = "karusel" if len(media) > 1 else ("reel" if media[0].suffix.lower() in VIDEO_SUFFIXES else "gorsel")
    options = {}
    settings = candidate.path / "ayar.txt"
    if settings.exists():
        try:
            lines = settings.read_text(encoding="utf-8").splitlines()
        except UnicodeError:
            raise ValidationError("ayar.txt UTF-8 kodlamasında olmalı.") from None
        seen = set()
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key in seen or key not in {"kapak_saniye", "reels_akista"}:
                raise ValidationError("ayar.txt içinde geçersiz veya yinelenen anahtar var.")
            seen.add(key)
            if key == "reels_akista":
                if value not in {"evet", "hayir"}:
                    raise ValidationError("reels_akista evet veya hayir olmalı.")
                options["share_to_feed"] = "true" if value == "evet" else "false"
            else:
                from decimal import Decimal, InvalidOperation
                try:
                    seconds = Decimal(value)
                    if not seconds.is_finite() or seconds < 0 or seconds > 86400:
                        raise ValueError
                    options["thumb_offset"] = int(seconds * 1000)
                except (InvalidOperation, ValueError, OverflowError):
                    raise ValidationError("kapak_saniye 0–86400 arasında bir sayı olmalı.") from None
    return Post(candidate, caption, media, kind, options, digest)


def prepare_media(media, temporary, runner=subprocess.run):
    result = []
    for index, path in enumerate(media):
        suffix = path.suffix.lower()
        video = suffix in VIDEO_SUFFIXES
        prepared = path
        if not video and (suffix == ".png" or path.stat().st_size > MAX_IMAGE) and not shutil.which("sips"):
            prepared = Path(temporary) / f"{index}.jpg"
            for q in (2, 4, 7, 10, 15, 20, 31):
                conversion = runner(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-q:v", str(q), str(prepared)],
                                    capture_output=True, timeout=120, check=False)
                if conversion.returncode:
                    raise ValidationError("Görsel JPEG biçimine dönüştürülemedi (ffmpeg).")
                if prepared.stat().st_size <= MAX_IMAGE:
                    break
            result.append((prepared, video))
            continue
        if not video and (suffix == ".png" or path.stat().st_size > MAX_IMAGE):
            prepared = Path(temporary) / f"{index}.jpg"
            try:
                conversion = runner(["sips", "-s", "format", "jpeg", str(path), "--out", str(prepared)],
                                    capture_output=True, timeout=120, check=False)
                if conversion.returncode:
                    raise ValidationError("Görsel JPEG biçimine dönüştürülemedi.")
                for quality in (85, 70, 55, 40, 25, 10):
                    if prepared.stat().st_size <= MAX_IMAGE:
                        break
                    conversion = runner(["sips", "-s", "formatOptions", str(quality),
                                         str(prepared), "--out", str(prepared)],
                                        capture_output=True, timeout=120, check=False)
                    if conversion.returncode:
                        raise ValidationError("Görsel boyutu küçültülemedi.")
            except (OSError, subprocess.SubprocessError):
                raise ValidationError("Görsel işleme başarısız; macOS sips aracını denetleyin.") from None
        if not video and prepared.stat().st_size > MAX_IMAGE:
            raise ValidationError("Görsel küçültüldükten sonra da 8 MB sınırını aşıyor.")
        result.append((prepared, video))
    return result


class Uploader:
    def __init__(self, config, password):
        self.config = config
        self.password = password
        self.connection = None
        self.transport = None

    def __enter__(self):
        config = self.config
        protocol = config.get("protokol", "sftp")
        if protocol not in {"sftp", "ftps", "ftp"}:
            raise AppError("Yükleme protokolü sftp, ftps veya ftp olmalı.")
        if any(not config.get(key) for key in ("sunucu", "kullanici", "uzak_klasor", "genel_adres")):
            raise AppError("Yükleme ayarları eksik.")
        address = urlsplit(config["genel_adres"])
        if address.scheme not in {"http", "https"} or not address.netloc or address.query or address.fragment or address.username:
            raise AppError("genel_adres kimlik bilgisi içermeyen geçerli bir HTTP(S) adresi olmalı.")
        try:
            if protocol == "sftp":
                try:
                    paramiko = importlib.import_module("paramiko")
                except ImportError:
                    raise AppError("SFTP devre dışı: paramiko kurulu değil; kur.sh betiğini kullanın.") from None
                self.transport = paramiko.SSHClient()
                self.transport.load_system_host_keys()
                self.transport.set_missing_host_key_policy(paramiko.RejectPolicy())
                self.transport.connect(config["sunucu"], port=config.get("port") or 22,
                                       username=config["kullanici"], password=self.password,
                                       timeout=30, banner_timeout=30, auth_timeout=30,
                                       look_for_keys=False, allow_agent=False)
                self.connection = self.transport.open_sftp()
                self.connection.get_channel().settimeout(60)
            else:
                self.connection = ftplib.FTP_TLS(context=ssl.create_default_context()) if protocol == "ftps" else ftplib.FTP()
                self.connection.connect(config["sunucu"], config.get("port") or 21, timeout=60)
                self.connection.login(config["kullanici"], self.password)
                if protocol == "ftps":
                    self.connection.prot_p()
            return self
        except AppError:
            self.__exit__(None, None, None)
            raise
        except Exception:
            self.__exit__(None, None, None)
            raise AppError("Yükleme sunucusuna bağlanılamadı; bağlantı ve sunucu anahtarını denetleyin.") from None

    def __exit__(self, *args):
        for connection in (self.connection, self.transport):
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()

    def remote_path(self, name):
        if not REMOTE_RE.fullmatch(name):
            raise AppError("Uzak dosya adı program biçimine uymuyor.")
        return posixpath.join(self.config["uzak_klasor"], name)

    def upload(self, path, name):
        remote = self.remote_path(name)
        try:
            if self.config["protokol"] == "sftp":
                self.connection.put(str(path), remote)
            else:
                with path.open("rb") as stream:
                    self.connection.storbinary(f"STOR {remote}", stream)
        except Exception:
            raise RequestError("Medya dosyası uzak sunucuya yüklenemedi.") from None
        return self.config["genel_adres"].rstrip("/") + "/" + quote(name)

    def delete(self, name):
        remote = self.remote_path(name)
        try:
            if self.config["protokol"] == "sftp":
                self.connection.remove(remote)
            else:
                self.connection.delete(remote)
        except Exception:
            raise RequestError("Uzak dosya silinemedi.") from None

    def cleanup_old(self, now, logger):
        try:
            if self.config["protokol"] == "sftp":
                entries = [(item.filename, item.st_mtime) for item in
                           self.connection.listdir_attr(self.config["uzak_klasor"])]
            else:
                entries = []
                for name, facts in self.connection.mlsd(self.config["uzak_klasor"], facts=["modify", "type"]):
                    if facts.get("type") != "file" or not REMOTE_RE.fullmatch(name):
                        continue
                    modified = dt.datetime.strptime(facts["modify"].split(".")[0], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
                    entries.append((name, modified.timestamp()))
            for name, modified in entries:
                if REMOTE_RE.fullmatch(name) and modified is not None and now.timestamp() - modified > 86400:
                    try:
                        self.delete(name)
                        logger.info("Bir günden eski artık dosya silindi: %s", name)
                    except AppError as error:
                        logger.warning("Artık dosya temizlenemedi: %s", error)
        except Exception:
            raise RequestError("Uzak klasördeki artık dosyalar listelenemedi.") from None


class GitHubPagesUploader:
    """Medyayı GitHub Pages deposuna koyar; tek push ile yayınlar, adres açılana kadar bekler."""
    def __init__(self, config, password=None, runner=subprocess.run, opener=None, sleeper=time.sleep):
        self.config = config
        self.run = runner
        self.sleep = sleeper
        self.opener = opener or (lambda url: urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20).status)
        self.pending = []

    def __enter__(self):
        repo = self.config.get("depo")
        if not repo or not self.config.get("genel_adres"):
            raise AppError("GitHub yükleme ayarları eksik (depo, genel_adres).")
        self.repo = Path(repo).expanduser()
        self.folder = self.repo / self.config.get("uzak_klasor", "m")
        if not (self.repo / ".git").exists():
            raise AppError("GitHub medya deposu bulunamadı.")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.git("pull", "--rebase", "--autostash", "--quiet")
        return self

    def __exit__(self, *args):
        return False

    def git(self, *args):
        env = dict(os.environ, PATH=os.environ.get("PATH", "") + ":/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
                   GIT_AUTHOR_NAME="OtoPaylasim", GIT_AUTHOR_EMAIL="noreply@anthropic.com",
                   GIT_COMMITTER_NAME="OtoPaylasim", GIT_COMMITTER_EMAIL="noreply@anthropic.com")
        result = self.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, timeout=120, env=env)
        if result.returncode:
            raise RequestError("GitHub medya deposu işlemi başarısız: git " + args[0])
        return result.stdout

    def url(self, name):
        return self.config["genel_adres"].rstrip("/") + "/" + quote(name)

    def upload(self, path, name):
        if not REMOTE_RE.fullmatch(name):
            raise AppError("Uzak dosya adı program biçimine uymuyor.")
        shutil.copyfile(path, self.folder / name)
        self.pending.append(name)
        return self.url(name)

    def finalize(self):
        if not self.pending:
            return
        self.git("add", "--", *[str(self.folder / n) for n in self.pending])
        self.git("commit", "--quiet", "-m", "Paylaşım medyası")
        self.push()
        if os.environ.get("GITHUB_ACTIONS") and os.environ.get("GITHUB_REPOSITORY"):
            # GITHUB_TOKEN ile yapılan push Pages derlemesini tetiklemeyebilir; açıkça iste.
            self.run(["gh", "api", "-X", "POST", f"repos/{os.environ['GITHUB_REPOSITORY']}/pages/builds"],
                     capture_output=True, text=True, timeout=60)
        deadline = time.monotonic() + 1200
        for name in self.pending:
            while True:
                try:
                    if self.opener(self.url(name)) == 200:
                        break
                except Exception:
                    pass
                if time.monotonic() > deadline:
                    raise RequestError("GitHub Pages medya adresi 20 dakikada açılmadı.")
                self.sleep(10)
        self.pending = []

    def delete(self, name):
        target = self.folder / name
        if not REMOTE_RE.fullmatch(name) or not target.exists():
            return
        self.git("rm", "--quiet", "--", str(target))
        self.git("commit", "--quiet", "-m", "Paylaşım medyası silindi")
        self.push()

    def push(self):
        for attempt in range(4):
            try:
                self.git("push", "--quiet")
                return
            except RequestError:
                if attempt == 3:
                    raise
                self.git("pull", "--rebase", "--autostash", "--quiet")
                self.sleep(3)

    def cleanup_old(self, now, logger):
        for item in self.folder.iterdir():
            if REMOTE_RE.fullmatch(item.name) and now.timestamp() - item.stat().st_mtime > 86400:
                try:
                    self.delete(item.name)
                    logger.info("Bir günden eski artık dosya silindi: %s", item.name)
                except AppError as error:
                    logger.warning("Artık dosya temizlenemedi: %s", error)


def make_uploader(config, password):
    if config.get("protokol") == "github":
        return GitHubPagesUploader(config)
    return Uploader(config, password)


def notify(message):
    # AppleScript koduna kullanıcı metni eklenmez; mesaj argv ile aktarılır.
    script = ('on run argv\n display notification (item 1 of argv) '
              'with title "OtoPaylasim"\nend run')
    try:
        subprocess.run(["osascript", "-e", script, message], capture_output=True,
                       timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def load_config(path):
    try:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    except FileNotFoundError:
        raise AppError("Ayar dosyası yok; ayar.ornek.toml dosyasını ayar.toml adıyla kopyalayın.") from None
    except (OSError, tomllib.TOMLDecodeError):
        raise AppError("Ayar dosyası okunamıyor veya TOML sözdizimi geçersiz.") from None
    if config.get("mod") not in {"deneme", "canli"}:
        raise AppError("mod ayarı deneme veya canli olmalı.")
    if not isinstance(config.get("kuyruk"), str) or not config["kuyruk"]:
        raise AppError("kuyruk ayarı boş olmayan bir yol olmalı.")
    limit = config.get("gunluk_sinir", 3)
    if type(limit) is not int or limit < 1:
        raise AppError("gunluk_sinir pozitif tam sayı olmalı.")
    if not re.fullmatch(r"v\d+\.\d+", str(config.get("api_surumu", ""))):
        raise AppError("api_surumu v24.0 gibi bir sürüm olmalı.")
    accounts = config.get("hesaplar", {})
    if not isinstance(accounts, dict) or any(
            not isinstance(accounts.get(account), dict)
            or not isinstance(accounts[account].get("kullanici_adi"), str)
            or not accounts[account]["kullanici_adi"]
            or not isinstance(accounts[account].get("ig_id", ""), str)
            or (accounts[account].get("ig_id") and not accounts[account]["ig_id"].isdigit())
            for account in accounts) or not accounts or not all(re.fullmatch(r"[a-z0-9]+", a) for a in accounts):
        raise AppError("Hesapların kullanıcı adı ve ig_id ayarlarını denetleyin.")
    upload = config.get("yukleme", {})
    if not isinstance(upload, dict):
        raise AppError("yukleme ayarı bir TOML tablosu olmalı.")
    if upload.get("protokol", "sftp") not in {"sftp", "ftps", "ftp", "github"}:
        raise AppError("Yükleme protokolü sftp, ftps veya ftp olmalı.")
    if type(upload.get("port", 0)) is not int or not 0 <= upload.get("port", 0) <= 65535:
        raise AppError("Yükleme portu 0–65535 arasında bir tam sayı olmalı.")
    for key in ("sunucu", "kullanici", "uzak_klasor", "genel_adres"):
        value = upload.get(key, "")
        if not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise AppError("Yükleme ayarlarında geçersiz metin veya kontrol karakteri var.")
    return config


class Application:
    def __init__(self, home=None, *, http=None, keychain=None, uploader_factory=None,
                 clock=now_local, notifier=notify):
        override = home or os.environ.get("OTOPAYLASIM_EV")
        self.home = Path(override).expanduser().resolve() if override else Path(__file__).resolve().parent
        self.config_path = self.home / "ayar.toml"
        self.config = load_config(self.config_path)
        self.queue = Path(self.config["kuyruk"]).expanduser()
        if not self.queue.is_absolute():
            self.queue = self.home / self.queue
        log_dir = self.home / "kayitlar" if override else Path.home() / "Library/Logs/OtoPaylasim"
        self.logger, self.redactor = make_logger(log_dir / "otopaylasim.log")
        self.http = http if http is not None else HTTP()
        self.keychain = keychain if keychain is not None else Keychain(self.redactor)
        self.uploader_factory = uploader_factory
        self.clock = clock
        self.notifier = notifier
        self.state = None
        # Çalışmada yayın/doğrulama hatası olduysa komut sıfırdan farklı kodla biter (bulutta iş kırmızı olur).
        self.hata_var = False

    @property
    def dry(self):
        return self.config["mod"] == "deneme"

    def secret(self, name):
        value = self.keychain.get(name)
        self.redactor.add(value)
        return value

    def api(self, account):
        return InstagramAPI(self.config["api_surumu"], self.secret(f"ig_{account}_token"),
                            self.http, self.logger)

    def uploader(self):
        config = self.config.get("yukleme", {})
        if self.uploader_factory is None and config.get("protokol") == "github":
            return GitHubPagesUploader(config)
        return (self.uploader_factory or Uploader)(config, self.secret("yukleme_sifre"))

    def inform(self, message):
        self.hata_var = True
        try:
            self.notifier(self.redactor.clean(message))
        except Exception:
            self.logger.warning("macOS bildirimi gösterilemedi.")

    def refresh_tokens(self, force=False):
        if self.dry:
            self.logger.info("DENEME: token yenileme isteği gönderilmeyecek.")
            return
        now = self.clock()
        for account in self.config["hesaplar"]:
            record = self.state.data["tokenlar"].setdefault(account, {})
            if not self.config["hesaplar"][account].get("ig_id"):
                continue  # henüz bağlanmamış hesap
            try:
                if self.secret(f"ig_{account}_token").startswith("EAA"):
                    continue  # sistem kullanıcısı token'ı süresiz; yenileme gerekmez
                expires = record.get("bitis")
                if expires and from_stamp(expires) - now < dt.timedelta(days=10):
                    self.inform(f"{account} hesabının token süresinin dolmasına 10 günden az kaldı.")
                previous = record.get("son_yenileme") or record.get("alinma")
                if not previous and not force:
                    self.logger.warning("%s: token tarihi bilinmiyor; önce hesap-bagla komutunu kullanın.", account)
                    continue
                if previous and not force and now - from_stamp(previous) <= dt.timedelta(days=7):
                    continue
                api = self.api(account)
                data = api.call("GET", "https://graph.instagram.com/refresh_access_token",
                                grant_type="ig_refresh_token")
                token = data.get("access_token")
                lifetime = data.get("expires_in")
                if not isinstance(token, str) or not token or type(lifetime) is not int or lifetime <= 0:
                    raise RequestError("Token yenileme yanıtı eksik veya geçersiz.")
                self.redactor.add(token)
                self.keychain.set(f"ig_{account}_token", token)
                record.update(son_yenileme=stamp(now), bitis=stamp(now + dt.timedelta(seconds=lifetime)),
                              bitis_tahmini=False)
                self.state.save()
                self.logger.info("%s hesabının token'ı yenilendi.", account)
            except Exception as error:
                message = str(error) if isinstance(error, AppError) else "Token tarihleri veya yerel kayıt güncellenemedi."
                self.logger.error("%s: token yenileme başarısız: %s", account, message)
                if isinstance(error, AuthorizationError):
                    self.inform(f"{account}: Instagram token yetkilendirmesi başarısız.")

    def bind_account(self, account):
        if self.dry:
            self.logger.info("DENEME: %s hesabı doğrulanacak ve ig_id ayara yazılacaktı.", account)
            return
        api = self.api(account)
        system_token = self.secret(f"ig_{account}_token").startswith("EAA")
        if system_token:
            known = str(self.config["hesaplar"][account].get("ig_id") or "")
            if not known.isdigit():
                raise AppError("Sistem kullanıcısı token'ı için ayar.toml'da ig_id önceden yazılı olmalı.")
            data = api.call("GET", known, fields="id,username")
            identifier = str(data.get("id", ""))
        else:
            data = api.call("GET", "me", fields="user_id,username")
            identifier = str(data.get("user_id", ""))
        username = data.get("username")
        if not identifier.isdigit() or not isinstance(username, str) or not username:
            raise RequestError("Hesap yanıtında user_id veya username eksik.")
        expected = self.config["hesaplar"][account]["kullanici_adi"]
        if username.casefold() != expected.casefold():
            self.logger.warning("UYARI: %s hesabının kullanıcı adı ayarla uyuşmuyor; beklenen: %s, dönen: %s.",
                                account, expected, username)
        source = self.config_path.read_text(encoding="utf-8")
        header = re.search(rf"(?m)^\s*\[hesaplar\.{account}\]\s*(?:#.*)?$", source)
        if not header:
            raise AppError("Hesap tablosu düzenlenemedi; örnek TOML'daki ayrı tablo biçimini kullanın.")
        following = re.search(r"(?m)^\s*\[", source[header.end():])
        end = header.end() + following.start() if following else len(source)
        section = source[header.end():end]
        if re.search(r"(?m)^\s*ig_id\s*=", section):
            section = re.sub(r"(?m)^[ \t]*ig_id\s*=.*$", f'ig_id = "{identifier}"', section)
        else:
            section = f'\nig_id = "{identifier}"\n' + section
        updated = source[:header.end()] + section + source[end:]
        # Başka ayarların bozulmadığı yazmadan önce doğrulanır.
        parsed = tomllib.loads(updated)
        if parsed["hesaplar"][account]["ig_id"] != identifier:
            raise AppError("Hesap kimliği ayara yazılamadı.")
        atomic_write(self.config_path, updated)
        self.config["hesaplar"][account]["ig_id"] = identifier
        now = self.clock()
        self.state.data["tokenlar"][account] = {
            "alinma": stamp(now), "son_yenileme": stamp(now),
            "bitis": None if system_token else stamp(now + dt.timedelta(days=60)),
            "bitis_tahmini": not system_token,
        }
        self.state.save()
        self.logger.info("%s hesabı bağlandı; token alınma tarihi kaydedildi.", account)

    def daily_count(self, account):
        today = self.clock().astimezone().date()
        count = 0
        for record in self.state.data["isler"].values():
            if record.get("hesap") != account:
                continue
            if record.get("durum") in {"yayinlandi", "yayinlaniyor"}:
                value = record.get("yayin_zamani") or record.get("yayina_baslama")
            else:
                value = record.get("deneme_zamani") if self.dry else None
            if value and from_stamp(value).astimezone().date() == today:
                count += 1
        return count

    def relative(self, destination):
        try:
            return str(Path(destination).resolve().relative_to(self.queue.resolve()))
        except Exception:
            return Path(destination).name

    def move_folder(self, path, category):
        destination = self.queue / category / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination = destination.with_name(path.name + "_" + uuid.uuid4().hex[:8])
        shutil.move(str(path), str(destination))
        return destination

    def finish_local(self, path, record):
        if not path.exists():
            return
        text = (f"Zaman: {record['yayin_zamani']}\nHesap: {record['hesap']}\n"
                f"Medya kimliği (media_id): {record['media_id']}\n"
                f"Kalıcı bağlantı (permalink): {record.get('permalink', '')}\n")
        try:
            atomic_write(path / "SONUC.txt", text)
            destination = self.move_folder(path, "yayinlandi")
            record["arsiv"] = self.relative(destination)
            record.pop("yerel_hata", None)
            self.state.save()
        except Exception:
            record["yerel_hata"] = "Yayın başarılı; sonuç dosyası veya klasör taşıması tamamlanamadı."
            self.logger.error("%s: yayın başarılı; yerel arşivleme tamamlanamadı, tekrar yayınlanmayacak.", path.name)
            with contextlib.suppress(Exception):
                self.state.save()

    def reject(self, candidate, message, key=None):
        if key is None:
            try:
                key = candidate.path.name + ":" + fingerprint(candidate.path)
            except (OSError, ValidationError):
                key = "gecersiz:" + candidate.path.name
        record = self.state.data["isler"].setdefault(key, {})
        if self.dry and record.get("deneme_hatasi") == message:
            return
        self.logger.error("%s%s: doğrulama hatası: %s%s", "DENEME: " if self.dry else "",
                          candidate.path.name, message,
                          " Hatalı klasörüne ayıracaktım." if self.dry else "")
        if self.dry:
            record.update(klasor=candidate.path.name, durum="bekliyor", deneme_hatasi=message, son_hata=message)
            self.state.save()
            return
        self.hata_var = True
        record.update(klasor=candidate.path.name, hesap=candidate.account, durum="hatali", son_hata=message)
        self.state.save()
        self.archive_error(candidate.path, record, message)

    def archive_error(self, path, record, message):
        try:
            atomic_write(path / "HATA.txt", f"Zaman: {stamp(self.clock())}\nHata: {message}\n")
            record["arsiv"] = self.relative(self.move_folder(path, "hatali"))
            self.state.save()
        except Exception:
            self.logger.error("%s: hatalı klasör arşivlenemedi; sonraki çalışmada yeniden taşınacak.", path.name)

    def failure(self, post, record, error):
        self.hata_var = True
        message = self.redactor.clean(str(error) if isinstance(error, AppError) else "Yerel dosya veya yükleme işlemi başarısız.")
        record["son_hata"] = message
        record["hata_zamani"] = stamp(self.clock())
        self.logger.error("%s: %s", post.candidate.path.name, message)
        if isinstance(error, AuthorizationError):
            record["durum"] = "bekliyor"
            record.pop("sonraki_deneme", None)
            self.state.save()
            self.inform(f"{post.candidate.account}: Instagram yetkilendirmesi başarısız; token'ı denetleyin.")
            return
        attempts = record.get("deneme_sayisi", 0) + 1
        record["deneme_sayisi"] = attempts
        if attempts >= 3 or isinstance(error, ValidationError):
            record["durum"] = "hatali"
            self.state.save()
            self.archive_error(post.candidate.path, record, message)
            self.inform(f"{post.candidate.path.name}: paylaşım hatalı klasörüne ayrıldı.")
        else:
            delay = 15 if attempts == 1 else 60
            record.update(durum="bekliyor", sonraki_deneme=stamp(self.clock() + dt.timedelta(minutes=delay)))
            self.state.save()
            self.logger.info("%s: %s. deneme başarısız; %s dakika beklenecek.",
                             post.candidate.path.name, attempts, delay)

    def checkpoint(self, note):
        """Bulutta durum.json'u hemen depoya gönderir; iş yarıda kesilse de kayıt kaybolmaz."""
        if not os.environ.get("GITHUB_ACTIONS"):
            return
        state_path = str(self.state.path)
        def git(*args):
            return subprocess.run(["git", *args], capture_output=True, text=True, timeout=120)
        try:
            git("add", state_path)
            if git("diff", "--cached", "--quiet").returncode == 0:
                return
            git("commit", "-q", "-m", f"Paylaşım kaydı: {note}", "--", state_path)
            for _ in range(4):
                if git("push", "-q").returncode == 0:
                    return
                git("pull", "-q", "--rebase", "--autostash")
                time.sleep(3)
            self.logger.error("Anında kayıt depoya gönderilemedi (%s).", note)
            self.hata_var = True
        except Exception:
            self.logger.error("Anında kayıt yapılamadı (%s).", note)
            self.hata_var = True

    def cleanup_uploads(self, uploader, names):
        for name in names:
            try:
                uploader.delete(name)
            except Exception:
                self.logger.warning("Uzak dosya temizlenemedi; günlük artık temizliği tekrar deneyecek: %s", name)

    def publish(self, post, record):
        account = post.candidate.account
        try:
            ig_id = self.config["hesaplar"][account].get("ig_id")
            if not ig_id:
                raise AuthorizationError("Hesap bağlı değil; hesap-bagla komutunu kullanın.")
            api = self.api(account)
            with tempfile.TemporaryDirectory(prefix="otopaylasim-") as temporary:
                media = prepare_media(post.media, temporary)
                with self.uploader() as uploader:
                    names = []
                    try:
                        urls = []
                        for path, video in media:
                            name = uuid.uuid4().hex + path.suffix.lower()
                            # Yarım kalan yüklemenin temizliği de denensin.
                            names.append(name)
                            urls.append((uploader.upload(path, name), video))
                        if hasattr(uploader, "finalize"):
                            uploader.finalize()
                        container_id = api.build(ig_id, post.kind, urls, post.caption, post.options)
                        if fingerprint(post.candidate.path) != post.digest:
                            raise RequestError("Paylaşım içeriği işlem sırasında değişti; kararlı dosyalar beklenecek.")
                        # Ağ çağrısından ÖNCE kalıcı niyet kaydı: çökme/yanıt kaybı
                        # durumunda aynı içerik körlemesine yeniden yayımlanmaz.
                        record.update(durum="yayinlaniyor", konteyner_id=container_id,
                                      yayina_baslama=stamp(self.clock()))
                        self.state.save()
                        self.checkpoint(post.candidate.path.name + " yayınlanıyor")
                        try:
                            data = api.call("POST", f"{ig_id}/media_publish", creation_id=container_id)
                            media_id = str(data.get("id", ""))
                            if not media_id:
                                raise RequestError("Yayın yanıtında medya kimliği eksik.")
                        except AuthorizationError:
                            record["durum"] = "bekliyor"
                            raise
                        except APIError as error:
                            if 400 <= error.status < 500:
                                record["durum"] = "bekliyor"
                                raise
                            raise PublicationUncertain("Yayın isteğinin sonucu belirsiz; otomatik tekrar engellendi.") from None
                        except Exception:
                            raise PublicationUncertain("Yayın isteğinin sonucu belirsiz; tekrar yayını önlemek için otomatik deneme durduruldu. Instagram hesabını ve konteyneri denetleyin.") from None
                        record.update(durum="yayinlandi", media_id=media_id, yayin_zamani=stamp(self.clock()), permalink="")
                        record.pop("son_hata", None)
                        record.pop("sonraki_deneme", None)
                        self.state.save()
                        self.checkpoint(post.candidate.path.name + " yayınlandı")
                        # Buradaki hatalar yayını veya deneme sayısını değiştirmez.
                        try:
                            record["permalink"] = api.call("GET", media_id, fields="permalink").get("permalink", "")
                            self.state.save()
                        except Exception:
                            self.logger.warning("%s: yayın başarılı, kalıcı bağlantı alınamadı.", post.candidate.path.name)
                        # İlk yorum (ayar.toml: ilk_yorum = false ile kapalı); hata yayını etkilemez.
                        if self.config.get("ilk_yorum", True):
                          try:
                            import ilk_yorum
                            ozel = post.candidate.path / "ilk_yorum.txt"
                            mesaj = (ozel.read_text(encoding="utf-8").strip() if ozel.is_file()
                                     else ilk_yorum.sec(account, post.candidate.path.name))
                            if mesaj and not record.get("ilk_yorum"):
                                cevap = api.call("POST", f"{media_id}/comments", message=mesaj)
                                record["ilk_yorum"] = cevap.get("id", "") or "gonderildi"
                                self.state.save()
                          except Exception:
                            self.logger.warning("%s: ilk yorum eklenemedi.", post.candidate.path.name)
                        # Gönderiyi hikâyede de paylaş (ayar.toml: hikaye = false ile kapatılır); hata yayını etkilemez.
                        if self.config.get("hikaye", True) and not record.get("hikaye") and urls:
                            try:
                                url, video = urls[0]
                                hikaye = api.create(ig_id, media_type="STORIES",
                                                    **({"video_url": url} if video else {"image_url": url}))
                                api.wait(hikaye)
                                cevap = api.call("POST", f"{ig_id}/media_publish", creation_id=hikaye)
                                record["hikaye"] = str(cevap.get("id", "")) or "gonderildi"
                                self.state.save()
                                self.logger.info("%s: hikâyede de paylaşıldı.", post.candidate.path.name)
                            except Exception:
                                record["hikaye"] = "hata"
                                self.state.save()
                                self.logger.warning("%s: hikâye paylaşılamadı.", post.candidate.path.name)
                        self.logger.info("%s: paylaşım yayınlandı; medya kimliği: %s.", post.candidate.path.name, media_id)
                    finally:
                        self.cleanup_uploads(uploader, names)
            self.finish_local(post.candidate.path, record)
        except Exception as error:
            if record.get("durum") == "yayinlandi":
                self.logger.error("%s: yayın başarılı, yerel kayıt tamamlanamadı; yeniden yayınlanmayacak.", post.candidate.path.name)
                with contextlib.suppress(Exception):
                    self.state.save()
                self.finish_local(post.candidate.path, record)
            elif record.get("durum") == "yayinlaniyor":
                self.hata_var = True
                record["son_hata"] = "Yayın sonucu belirsiz; otomatik tekrar engellendi. Instagram hesabını denetleyin."
                self.logger.error("%s: %s", post.candidate.path.name, record["son_hata"])
                with contextlib.suppress(Exception):
                    self.state.save()
                self.inform(f"{post.candidate.path.name}: yayın sonucu belirsiz; elle denetim gerekiyor.")
            else:
                self.failure(post, record, error)

    def run_queue(self, immediate=None):
        if immediate:
            path = Path(immediate).expanduser().resolve()
            if path.parent != self.queue.resolve() or not path.is_dir() or path.is_symlink():
                raise AppError("--simdi klasörü kuyruğun doğrudan altındaki bir paylaşım klasörü olmalı.")
            if path.name.startswith((".", "_")) or path.name in {"yayinlandi", "hatali"}:
                raise AppError("Arşiv veya özel klasör --simdi ile işlenemez.")
            candidates = [parse_candidate(path)]
        else:
            candidates = scan_queue(self.queue)
        if self.dry:
            self.logger.info("DENEME: token yenileme, yükleme ve bir günden eski uzak artık temizliği simüle ediliyor.")
        else:
            self.refresh_tokens()
            try:
                with self.uploader() as uploader:
                    uploader.cleanup_old(self.clock(), self.logger)
            except Exception as error:
                message = str(error) if isinstance(error, AppError) else "Uzak klasör temizliği başarısız."
                self.logger.warning("Artık temizliği tamamlanamadı: %s", message)
        used_accounts = set()
        for candidate in candidates:
            # Klasör yayın sonrasında düzenlense veya metin silinse bile aynı
            # adı yeniden yayımlama. İçerik özeti ayrıca durum kaydında saklanır.
            completed = next((r for r in self.state.data["isler"].values()
                              if r.get("klasor") == candidate.path.name
                              and r.get("durum") in {"yayinlandi", "yayinlaniyor"}), None)
            if completed:
                if completed["durum"] == "yayinlandi":
                    if not self.dry:
                        self.finish_local(candidate.path, completed)
                else:
                    self.logger.warning("%s: önceki yayın isteğinin sonucu belirsiz; elle denetim gerekiyor.", candidate.path.name)
                continue
            if candidate.scheduled and candidate.scheduled > self.clock() and not immediate:
                continue
            try:
                digest = fingerprint(candidate.path)
            except (OSError, ValidationError):
                digest = None
            same = digest and next((r for r in self.state.data["isler"].values()
                                    if r.get("ozet") == digest
                                    and r.get("durum") in {"yayinlandi", "yayinlaniyor"}), None)
            if same:
                self.logger.warning("%s: aynı içerik daha önce '%s' adıyla %s; yeniden yayınlanmayacak.",
                                    candidate.path.name, same.get("klasor"),
                                    "yayınlandı" if same["durum"] == "yayinlandi" else "yayına gönderildi")
                if not self.dry:
                    with contextlib.suppress(Exception):
                        atomic_write(candidate.path / "SONUC.txt",
                                     f"Yinelenen içerik; önceki yayın: {same.get('klasor')} {same.get('permalink', '')}\n")
                        self.move_folder(candidate.path, "yayinlandi")
                continue
            try:
                if candidate.scheduled and candidate.scheduled > self.clock() and not immediate:
                    continue
                if syncing(candidate.path, self.clock()):
                    self.logger.info("%s: son 5 dakikada değişmiş; eşitleme beklenecek.", candidate.path.name)
                    continue
                post = validate(candidate)
                if candidate.account not in self.config["hesaplar"]:
                    raise ValidationError(f"'{candidate.account}' hesabı ayar.toml'da tanımlı değil.")
            except ValidationError as error:
                self.reject(candidate, str(error))
                continue
            except OSError:
                self.logger.warning("%s: dosyalar okunamadı; sonraki çalışmada yeniden denenecek.", candidate.path.name)
                continue
            record = self.state.data["isler"].setdefault(post.key, {
                "klasor": candidate.path.name, "ozet": post.digest, "hesap": candidate.account,
                "durum": "bekliyor", "deneme_sayisi": 0,
            })
            if record.get("durum") == "yayinlandi":
                if not self.dry:
                    self.finish_local(candidate.path, record)
                continue
            if record.get("durum") == "yayinlaniyor":
                self.logger.warning("%s: önceki yayın isteğinin sonucu belirsiz; elle denetim gerekiyor.", candidate.path.name)
                continue
            if record.get("durum") == "hatali":
                if not self.dry:
                    self.archive_error(candidate.path, record, record.get("son_hata", "Paylaşım hatalı."))
                continue
            if self.dry and record.get("deneme_ozeti") == post.digest:
                continue
            if candidate.account in used_accounts:
                continue
            if record.get("sonraki_deneme") and from_stamp(record["sonraki_deneme"]) > self.clock():
                continue
            if self.daily_count(candidate.account) >= self.config.get("gunluk_sinir", 3):
                self.logger.info("%s: hesap için günlük paylaşım sınırına ulaşıldı.", candidate.path.name)
                continue
            used_accounts.add(candidate.account)
            if self.dry:
                if record.get("deneme_ozeti") != post.digest:
                    self.logger.info("DENEME: %s hesabında %s türünde %s medya ile %s klasörünü yayınlayacaktım; dönüştürme, yükleme, konteyner bekleme, yayın ve arşivleme simüle edildi.",
                                     candidate.account, post.kind, len(post.media), candidate.path.name)
                    record.update(deneme_ozeti=post.digest, deneme_zamani=stamp(self.clock()))
                    self.state.save()
                continue
            self.publish(post, record)

    def upload_test(self):
        if self.dry:
            self.logger.info("DENEME: küçük test dosyası yüklenip HTTP 200 doğrulanacak ve silinecekti.")
            return
        with tempfile.TemporaryDirectory(prefix="otopaylasim-deneme-") as temporary:
            path = Path(temporary) / "deneme.txt"
            content = "OtoPaylasim yükleme denemesi.\n".encode("utf-8")
            path.write_bytes(content)
            with self.uploader() as uploader:
                name = uuid.uuid4().hex + ".txt"
                try:
                    url = uploader.upload(path, name)
                    if hasattr(uploader, "finalize"):
                        uploader.finalize()
                    response = self.http.request("GET", url)
                    if response.status_code != 200 or response.content != content:
                        raise RequestError("Test dosyası genel adresten HTTP 200 ve aynı içerikle indirilemedi.")
                    self.logger.info("Yükleme denemesi başarılı; genel adres HTTP 200 ve doğru içerik döndürdü.")
                finally:
                    # Testin başarılı sayılması için test dosyasının silinmesi de gerekir.
                    uploader.delete(name)

    def show_status(self):
        candidates = scan_queue(self.queue)
        records = self.state.data["isler"]
        published = {r.get("klasor") for r in records.values() if r.get("durum") == "yayinlandi"}
        failed = {r.get("klasor") for r in records.values() if r.get("durum") == "hatali"}
        pending = [c for c in candidates if c.path.name not in published | failed]
        def count_archive(name):
            path = self.queue / name
            return sum(p.is_dir() for p in path.iterdir()) if path.exists() else 0
        self.logger.info("Mod: %s", self.config["mod"])
        for account, config in self.config["hesaplar"].items():
            self.logger.info("Hesap %s: %s; %s", account, config["kullanici_adi"],
                             "bağlı" if config.get("ig_id") else "bağlı değil")
        self.logger.info("Bekleyen: %s | Yayınlanan: %s | Hatalı: %s", len(pending),
                         max(len(published), count_archive("yayinlandi")),
                         max(len(failed), count_archive("hatali")))
        self.logger.info("Sıradaki paylaşım: %s", pending[0].path.name if pending else "yok")
        errors = sorted((r for r in records.values() if r.get("son_hata") or r.get("yerel_hata")),
                        key=lambda r: r.get("hata_zamani", ""), reverse=True)
        for record in errors[:5]:
            self.logger.info("Son hata (%s): %s", record.get("klasor", "bilinmiyor"),
                             record.get("son_hata") or record.get("yerel_hata"))
        uncertain = sum(r.get("durum") == "yayinlaniyor" for r in records.values())
        if uncertain:
            self.logger.warning("Sonucu belirsiz ve otomatik tekrarı engellenmiş yayın: %s", uncertain)

    def execute(self, command, **options):
        with exclusive_lock(self.home / "otopaylasim.kilit") as acquired:
            if not acquired:
                self.logger.info("Başka bir çalışma sürüyor; bu çalışma hemen sonlandırıldı.")
                return
            self.state = State(self.home / "durum.json")
            if command == "calistir":
                self.run_queue(options.get("simdi"))
            elif command == "durum":
                self.show_status()
            elif command == "token-yenile":
                self.refresh_tokens(options.get("zorla", False))
            elif command == "hesap-bagla":
                self.bind_account(options["hesap"])
            elif command == "yukleme-dene":
                self.upload_test()


class TurkishParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        # argparse'ın İngilizce iç hata mesajları kullanıcıya aktarılmaz.
        self.exit(2, "Hata: Komut veya seçenekler geçersiz; --help ile kullanımı inceleyin.\n")


def build_parser():
    # argparse başlıkları ve otomatik yardım metni Türkçeleştirilir.
    translations = {"usage: ": "kullanım: ", "positional arguments": "konumsal bağımsız değişkenler",
                    "options": "seçenekler", "show this help message and exit": "bu yardım metnini göster ve çık"}
    argparse._ = lambda text: translations.get(text, text)
    parser = TurkishParser(description="Instagram paylaşım kuyruğunu yönetin.")
    commands = parser.add_subparsers(dest="komut", required=True, title="komutlar", metavar="KOMUT")
    secret = commands.add_parser("anahtar-ekle", help="Anahtar Zinciri'ne gizli bilgi kaydet")
    secret.add_argument("ad", help="kaydedilecek gizli bilginin adı")
    bind = commands.add_parser("hesap-bagla", help="Instagram hesabını doğrula ve bağla")
    bind.add_argument("hesap", help="bağlanacak hesap anahtarı (ayar.toml [hesaplar.X])")
    refresh = commands.add_parser("token-yenile", help="gerekiyorsa token'ları yenile")
    refresh.add_argument("--zorla", action="store_true", help="7 günlük süreyi bekleme")
    commands.add_parser("yukleme-dene", help="yükleme ve genel HTTP adresini doğrula")
    commands.add_parser("durum", help="kuyruk ve hesap durumlarını göster")
    run = commands.add_parser("calistir", help="saati gelmiş paylaşımları işle")
    run.add_argument("--simdi", metavar="KLASOR", help="tek klasörün yalnız zamanını bekleme")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.komut == "anahtar-ekle":
            # Kurulumdan önce de kullanılabilir; şifre ekleme açık bir kullanıcı işlemidir.
            secret = getpass.getpass("Gizli değer (ekranda görünmez): ")
            Keychain(Redactor()).set(args.ad, secret)
            print("Gizli bilgi Anahtar Zinciri'ne kaydedildi.")
            return 0
        app = Application()
        app.execute(args.komut, **{k: v for k, v in vars(args).items() if k != "komut"})
        if app.hata_var:
            print("Hata: Bu çalışmada en az bir paylaşım sorunu oldu; ayrıntı kayıt dosyasında.", file=sys.stderr)
            return 1
        return 0
    except AppError as error:
        print(f"Hata: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("İşlem kullanıcı tarafından durduruldu.", file=sys.stderr)
        return 130
    except Exception:
        # Kütüphane hata metinleri token/parola içerebilir.
        print("Hata: Yerel işlem tamamlanamadı; ayarları, dosya izinlerini ve kayıtları denetleyin.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
