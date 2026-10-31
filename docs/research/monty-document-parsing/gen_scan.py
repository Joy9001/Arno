"""Scan / photo / screenshot fixtures (A10-A12). Pillow + numpy + pypdfium2, test-only."""
import io
import json
import math
import random
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import FIX, inr, write_truth

ARIAL = r"C:\Windows\Fonts\arial.ttf"
ARIALB = r"C:\Windows\Fonts\arialbd.ttf"


def render_pages(pdf_path, dpi=200):
    pdf = pdfium.PdfDocument(str(pdf_path))
    return [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(pdf))]


def image_only_pdf(src_pdf, dst, dpi=200):
    pages = render_pages(src_pdf, dpi)
    pages[0].save(dst, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])


def phone_photo(src_pdf, dst, seed=7, dpi=150, harsh=False):
    rng = random.Random(seed)
    page = render_pages(src_pdf, dpi)[0]
    W, H = page.size
    pad = int(0.12 * W)
    canvas = Image.new("RGB", (W + 2 * pad, H + 2 * pad), (92, 74, 58))     # desk
    canvas.paste(page, (pad, pad))
    cw, ch = canvas.size
    # keystone: top edge pulled in a little, slight left/right skew
    dx = 0.035 * cw
    coeffs_src = [(0 + dx, 0 + 0.01 * ch), (cw - dx * 1.4, 0), (cw, ch), (0, ch - 0.01 * ch)]
    # find perspective coefficients mapping output rect -> source quad
    def find_coeffs(pa, pb):
        A = []
        for p1, p2 in zip(pa, pb):
            A.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0] * p1[0], -p2[0] * p1[1]])
            A.append([0, 0, 0, p1[0], p1[1], 1, -p2[1] * p1[0], -p2[1] * p1[1]])
        a = np.array(A, dtype=float)
        b = np.array(pb).reshape(8)
        return np.linalg.solve(a, b).tolist()
    out_rect = [(0, 0), (cw, 0), (cw, ch), (0, ch)]
    img = canvas.transform((cw, ch), Image.PERSPECTIVE, find_coeffs(out_rect, coeffs_src), Image.BICUBIC, fillcolor=(92, 74, 58))
    img = img.rotate(4.0 if harsh else 2.5, resample=Image.BICUBIC, expand=True, fillcolor=(92, 74, 58))
    img = img.filter(ImageFilter.GaussianBlur(1.7 if harsh else 0.8))
    arr = np.asarray(img).astype(np.float32)
    h, w, _ = arr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    shade = 1.0 - 0.28 * ((xx / w) * 0.7 + (yy / h) * 0.3)                    # uneven lighting gradient
    arr = arr * shade[..., None]
    arr += np.random.default_rng(seed).normal(0, 26 if harsh else 9, arr.shape)                 # sensor noise
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img.save(dst, "JPEG", quality=30 if harsh else 62)


def upi_screenshot(dst, merchant="SWIGGY", amount=45000, date="05 Jan 2026, 08:14 PM", ref="412345678901", frm="XXXX1234"):
    W, H = 1080, 1920
    im = Image.new("RGB", (W, H), (18, 18, 22))
    d = ImageDraw.Draw(im)
    f = lambda s, b=False: ImageFont.truetype(ARIALB if b else ARIAL, s)
    d.text((W // 2, 260), "Payment successful", fill=(120, 220, 140), font=f(54, True), anchor="mm")
    d.text((W // 2, 420), f"\u20b9{inr(amount)}", fill=(255, 255, 255), font=f(150, True), anchor="mm")
    d.text((W // 2, 560), f"Paid to {merchant}", fill=(230, 230, 230), font=f(58), anchor="mm")
    d.text((W // 2, 640), date, fill=(160, 160, 170), font=f(42), anchor="mm")
    y = 820
    for k, v in (("UPI transaction ID", ref), ("Debited from", f"Savings account {frm}"), ("Status", "Completed")):
        d.text((70, y), k, fill=(150, 150, 160), font=f(38))
        d.text((70, y + 52), v, fill=(240, 240, 240), font=f(46))
        y += 170
    im.save(dst, "PNG")
    write_truth(dst, [dict(name="upi", fields=dict(merchant=merchant, amount=amount, date="2026-01-05", ref=ref), opening=0, closing=0, rows=[])])


def receipt_jpg(dst, seed=3):
    W, H = 760, 1100
    im = Image.new("RGB", (W, H), (250, 248, 240))
    d = ImageDraw.Draw(im)
    f = lambda s, b=False: ImageFont.truetype(ARIALB if b else ARIAL, s)
    d.text((W // 2, 60), "SAI KRIPA RESTAURANT", fill=(20, 20, 20), font=f(40, True), anchor="mm")
    d.text((W // 2, 105), "12 MG Road, Bengaluru 560001   GSTIN 29ABCDE1234F1Z5", fill=(60, 60, 60), font=f(20), anchor="mm")
    d.text((50, 170), "Date: 05/01/2026   Time: 20:07", fill=(20, 20, 20), font=f(26))
    d.text((50, 205), "Bill No: 10492", fill=(20, 20, 20), font=f(26))
    items = [("Masala Dosa", 2, 9000), ("Filter Coffee", 2, 4000), ("Paneer Butter Masala", 1, 24000), ("Butter Naan", 3, 5500)]
    y = 280
    d.text((50, y), "Item", fill=(20, 20, 20), font=f(26, True)); d.text((420, y), "Qty", fill=(20, 20, 20), font=f(26, True)); d.text((710, y), "Amount", fill=(20, 20, 20), font=f(26, True), anchor="ra")
    y += 45
    sub = 0
    for n, q, p in items:
        d.text((50, y), n, fill=(20, 20, 20), font=f(26)); d.text((430, y), str(q), fill=(20, 20, 20), font=f(26))
        d.text((710, y), inr(q * p), fill=(20, 20, 20), font=f(26), anchor="ra"); sub += q * p; y += 42
    gst = sub * 5 // 100
    total = sub + gst
    y += 20
    for k, v in (("Sub Total", sub), ("GST 5%", gst)):
        d.text((50, y), k, fill=(20, 20, 20), font=f(26)); d.text((710, y), inr(v), fill=(20, 20, 20), font=f(26), anchor="ra"); y += 42
    d.text((50, y + 10), "TOTAL", fill=(0, 0, 0), font=f(34, True)); d.text((710, y + 10), "\u20b9" + inr(total), fill=(0, 0, 0), font=f(34, True), anchor="ra")
    d.text((50, y + 80), "Paid by: UPI   Ref 412399887766", fill=(20, 20, 20), font=f(24))
    d.text((W // 2, H - 60), "Thank you! Visit again", fill=(80, 80, 80), font=f(24), anchor="mm")
    im = im.rotate(1.2, expand=True, fillcolor=(210, 205, 195)).filter(ImageFilter.GaussianBlur(0.5))
    im.save(dst, "JPEG", quality=70)
    write_truth(dst, [dict(name="receipt", fields=dict(merchant="SAI KRIPA RESTAURANT", amount=total, date="2026-01-05", ref="412399887766"), opening=0, closing=0, rows=[])])
    return total


def main():
    image_only_pdf(FIX / "a1_sep_cols_jan.pdf", FIX / "a10_image_only_jan.pdf")
    import shutil
    for tag in ("jan", "feb"):
        image_only_pdf(FIX / f"a1_sep_cols_{tag}.pdf", FIX / f"a10_image_only_{tag}.pdf")
        for suffix in (".truth.json",):
            shutil.copy(str(FIX / f"a1_sep_cols_{tag}.pdf") + suffix, str(FIX / f"a10_image_only_{tag}.pdf") + suffix)
    phone_photo(FIX / "a1_sep_cols_jan.pdf", FIX / "a11_phone_photo_jan.jpg")
    shutil.copy(str(FIX / "a1_sep_cols_jan.pdf") + ".truth.json", str(FIX / "a11_phone_photo_jan.jpg") + ".truth.json")
    phone_photo(FIX / "a1_sep_cols_jan.pdf", FIX / "a11b_harsh_photo_jan.jpg", seed=11, dpi=100, harsh=True)
    shutil.copy(str(FIX / "a1_sep_cols_jan.pdf") + ".truth.json", str(FIX / "a11b_harsh_photo_jan.jpg") + ".truth.json")
    upi_screenshot(FIX / "a12_upi_screenshot.png")
    print("receipt total paise:", receipt_jpg(FIX / "a12_receipt.jpg"))


if __name__ == "__main__":
    main()
