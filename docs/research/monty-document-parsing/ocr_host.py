"""Host-side OCR -> positioned words (same shape as doc_words). RapidOCR (Apache-2.0) via ONNX runtime, CPU.

Lines come back from the detector with a 4-point box; we deskew by the median box angle, then split each
line's text into words with x positions proportional to character offsets (or use word boxes if present).
"""
import math
import statistics
import time

_engine = None
LAST = {"seconds": 0.0, "lines": 0, "angle": 0.0}


def engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    return _engine


def ocr_words(pil_image, deskew=True):
    import numpy as np
    t0 = time.perf_counter()
    res = engine()(np.array(pil_image.convert("RGB")), return_word_box=True)
    boxes, txts = res.boxes, res.txts
    LAST["seconds"] = round(time.perf_counter() - t0, 2)
    if boxes is None or txts is None:
        LAST["lines"] = 0
        return []
    boxes = [[(float(x), float(y)) for x, y in b] for b in boxes]
    angle = 0.0
    if deskew and boxes:
        angs = [math.atan2(b[1][1] - b[0][1], b[1][0] - b[0][0]) for b in boxes if abs(b[1][0] - b[0][0]) > 40]
        angle = statistics.median(angs) if angs else 0.0
    LAST.update(lines=len(txts), angle=round(math.degrees(angle), 2))
    cx, cy = pil_image.width / 2, pil_image.height / 2
    ca, sa = math.cos(-angle), math.sin(-angle)

    def rot(p):
        x, y = p[0] - cx, p[1] - cy
        return (cx + x * ca - y * sa, cy + x * sa + y * ca)

    out = []
    for line_words, b in zip(word_results(res), boxes):
        for text, wb in line_words:
            pts = [rot(p) for p in wb]
            out.append({"text": text, "x0": min(p[0] for p in pts), "x1": max(p[0] for p in pts),
                        "top": min(p[1] for p in pts), "bottom": max(p[1] for p in pts)})
    # normalise scale: median word height -> 9 units (a 8pt statement font), so thresholds mean the same as on PDFs
    if out:
        h = statistics.median([w["bottom"] - w["top"] for w in out])
        s = 9.0 / h if h else 1.0
        LAST["scale"] = round(s, 4)
        for w in out:
            for k in ("x0", "x1", "top", "bottom"):
                w[k] = round(w[k] * s, 1)
    return out


def word_results(res):
    """Per detected text box: list of (word, 4-point box). RapidOCR returns word boxes when return_word_box=True."""
    out = []
    for i, t in enumerate(res.txts):
        wr = res.word_results[i] if getattr(res, "word_results", None) else None
        if wr:
            out.append([(w[0], [(float(x), float(y)) for x, y in w[2]]) for w in wr if str(w[0]).strip()])
        else:
            out.append([(str(t), [(float(x), float(y)) for x, y in res.boxes[i]])])
    return out
