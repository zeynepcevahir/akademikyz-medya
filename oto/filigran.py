"""Eski Hasbihal görsellerindeki '@gizemliosmanlica' filigranını YouTube kopyalarında '@osmanlicahasbihal' ile örter.

Yalnızca eski gece şablonu (1350x1687, koyu gökyüzü, üstte ortalı ad) için çalışır; başka görsele dokunmaz.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import numpy as np

YAZI = Path(__file__).resolve().parent / "yazitipi" / "filigran.ttf"
KUTU = (530, 108, 900, 168)  # eski adın alanı (x0, y0, x1, y1)


def eski_sablon_mu(im):
    if im.size != (1350, 1687):
        return False
    a = np.asarray(im.convert("L")).astype(int)
    alan, cevre = a[115:165, 550:870], a[60:105, 550:870]
    return (alan > 150).sum() > 400 and np.median(cevre) < 35 and np.median(a[115:165, 100:400]) < 35


def duzelt(yol):
    im = Image.open(yol).convert("RGB")
    if not eski_sablon_mu(im):
        return False
    x0, y0, x1, y1 = KUTU
    h = y1 - y0
    yama = im.crop((x0, y0 - h - 4, x1, y0 - 4))  # hemen üstteki gökyüzü (yıldızlarıyla)
    im.paste(yama, (x0, y0))
    # Ek yeri belli olmasın: kenarları yumuşat
    maske = Image.new("L", im.size, 0)
    ImageDraw.Draw(maske).rectangle((x0 + 6, y0 + 4, x1 - 6, y1 - 4), fill=255)
    maske = maske.filter(ImageFilter.GaussianBlur(5))
    orj = Image.open(yol).convert("RGB")
    orj.paste(im, (0, 0), maske)
    im = orj
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(str(YAZI), 38)
    metin = "@osmanlicahasbihal"
    w = d.textlength(metin, font=f)
    d.text(((1350 - w) / 2, 117), metin, font=f, fill=(236, 236, 240))
    im.save(yol, "PNG" if str(yol).endswith(".png") else "JPEG", quality=95)
    return True
