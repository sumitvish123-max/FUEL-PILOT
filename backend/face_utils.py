"""Face detection, 128-d embedding generation and matching.

Engines (choose with FUELPILOT_FACE_ENGINE):
  face_recognition  (default) dlib ResNet, 128-d, distance threshold 0.55 (library default is 0.6)
  deepface          optional alternative (Facenet 128-d); needs `pip install deepface`
  demo              OpenCV Haar + pixel features. NOT SECURE – only to smoke-test the pipeline
                    on a machine without dlib. Enabled only if FUELPILOT_ALLOW_DEMO_FACE=1.

Distance is Euclidean in embedding space; a match needs distance <= FUELPILOT_FACE_THRESHOLD.
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

THRESHOLD = float(os.getenv("FUELPILOT_FACE_THRESHOLD", "0.55"))
AMBIGUITY_MARGIN = 0.04       # best vs. second-best *different* person must differ by at least this
MIN_FACE_PX = 80              # reject tiny/far faces – embeddings are unreliable
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_SHARPNESS = 12            # Laplacian variance of the FACE crop (normalised to 160x160)
BRIGHT_MIN, BRIGHT_MAX = 40, 225   # mean gray level of the face crop
MAX_SIDE = 800                # downscale big frames: faster, and HOG works best around this size


class FaceError(Exception):
    """User-facing, recoverable problem with a submitted image."""


class NoFaceError(FaceError):
    pass


class MultipleFacesError(FaceError):
    pass


# ───────────────────────── image decoding ─────────────────────────
_DATA_URL = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,")


def decode_image(b64: str) -> np.ndarray:
    """base64 (optionally a data-URL) -> BGR ndarray, downscaled to MAX_SIDE."""
    try:
        raw = base64.b64decode(_DATA_URL.sub("", b64), validate=False)
    except Exception as exc:  # noqa: BLE001
        raise FaceError("Image is not valid base64") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise FaceError("Image is empty or larger than 8 MB")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FaceError("Could not decode image (use JPEG or PNG)")
    h, w = img.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


_haar = None


def _haar_boxes(img: np.ndarray) -> list:
    """Cheap OpenCV face boxes (top, right, bottom, left) – used for live quality checks."""
    global _haar
    _haar = _haar or cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    return [(y, x + w, y + h, x) for x, y, w, h in _haar.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))]


# ───────────────────────── engines ─────────────────────────
@dataclass
class FaceResult:
    embedding: np.ndarray        # shape (128,)
    box: tuple[int, int, int, int]   # top, right, bottom, left
    landmarks: int               # number of facial landmark groups found (0 if engine has none)


class _DlibEngine:
    name = "face_recognition"

    def __init__(self):
        import face_recognition  # noqa: WPS433 – heavy import, keep lazy
        self.fr = face_recognition

    def locate(self, img: np.ndarray) -> list:
        return self.fr.face_locations(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), model="hog")

    def embed(self, img: np.ndarray, jitters: int = 1) -> FaceResult:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        boxes = self.fr.face_locations(rgb, model="hog")
        if not boxes:
            raise NoFaceError("No face detected – face the camera in good light")
        if len(boxes) > 1:
            raise MultipleFacesError("More than one face in frame – only one person at a time")
        top, right, bottom, left = boxes[0]
        if min(bottom - top, right - left) < MIN_FACE_PX:
            raise FaceError("Face too small – move closer to the camera")
        enc = self.fr.face_encodings(rgb, known_face_locations=boxes, num_jitters=jitters)[0]
        marks = self.fr.face_landmarks(rgb, face_locations=boxes)
        return FaceResult(np.asarray(enc, dtype=np.float64), boxes[0], len(marks[0]) if marks else 0)


class _DeepFaceEngine:
    name = "deepface"

    def __init__(self):
        from deepface import DeepFace  # noqa: WPS433
        self.df = DeepFace

    locate = staticmethod(_haar_boxes)

    def embed(self, img: np.ndarray, jitters: int = 1) -> FaceResult:
        try:
            reps = self.df.represent(img, model_name="Facenet", detector_backend="opencv",
                                     enforce_detection=True)
        except ValueError as exc:
            raise NoFaceError("No face detected – face the camera in good light") from exc
        if len(reps) > 1:
            raise MultipleFacesError("More than one face in frame – only one person at a time")
        area = reps[0]["facial_area"]
        if min(area["w"], area["h"]) < MIN_FACE_PX:
            raise FaceError("Face too small – move closer to the camera")
        vec = np.asarray(reps[0]["embedding"], dtype=np.float64)
        vec = vec / (np.linalg.norm(vec) or 1.0) * 0.8          # Facenet vectors are 128-d; rescale so
        # distances land in the same range as dlib (≈0.4–0.6 same person) – re-tune if you use this engine.
        return FaceResult(vec, (area["y"], area["x"] + area["w"], area["y"] + area["h"], area["x"]), 0)


class _DemoEngine:
    """Insecure – structural pipeline test only (no deep features)."""
    name = "demo"

    def __init__(self):
        self.cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    locate = staticmethod(_haar_boxes)

    def embed(self, img: np.ndarray, jitters: int = 1) -> FaceResult:
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        faces = self.cascade.detectMultiScale(gray, 1.1, 5, minSize=(MIN_FACE_PX, MIN_FACE_PX))
        if len(faces) == 0:
            raise NoFaceError("No face detected – face the camera in good light")
        if len(faces) > 1:
            raise MultipleFacesError("More than one face in frame – only one person at a time")
        x, y, w, h = faces[0]
        crop = cv2.resize(gray[y:y + h, x:x + w], (8, 16), interpolation=cv2.INTER_AREA).astype(np.float64).ravel()
        crop -= crop.mean()
        crop /= (np.linalg.norm(crop) or 1.0)
        return FaceResult(crop, (y, x + w, y + h, x), 0)


_engine = None


def get_engine():
    """Lazy singleton so the API still boots (and reports the problem) if dlib is missing."""
    global _engine
    if _engine is not None:
        return _engine
    choice = os.getenv("FUELPILOT_FACE_ENGINE", "face_recognition").lower()
    try:
        _engine = {"face_recognition": _DlibEngine, "deepface": _DeepFaceEngine, "demo": _DemoEngine}[choice]()
    except ImportError as exc:
        if os.getenv("FUELPILOT_ALLOW_DEMO_FACE") == "1":
            _engine = _DemoEngine()
        else:
            raise RuntimeError(
                f"Face engine '{choice}' is not installed ({exc}). Install it (see requirements.txt) "
                "or set FUELPILOT_ALLOW_DEMO_FACE=1 for an insecure demo engine.") from exc
    return _engine


def engine_threshold() -> float:
    # The demo engine's feature space is different; use a looser, clearly-not-secure cut-off.
    return 0.30 if get_engine().name == "demo" else THRESHOLD


# ───────────────────────── public helpers ─────────────────────────
def embed_single(b64_image: str, jitters: int = 1) -> FaceResult:
    """Decode + detect exactly one face + compute its 128-d embedding."""
    return get_engine().embed(decode_image(b64_image), jitters=jitters)


def sharpness(b64_image: str) -> float:
    """Variance-of-Laplacian focus measure (higher = sharper). Used for registration quality."""
    gray = cv2.cvtColor(decode_image(b64_image), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def assess(b64_image: str) -> dict:
    """Live quality gate for registration photos: one face, big enough, lit, and sharp *on the face itself*.

    (Measuring blur on the whole frame fails on plain walls; the face crop is what matters.)
    """
    out = {"ok": False, "reason": "", "faces": 0, "face_px": 0, "sharpness": 0.0, "brightness": 0.0}
    try:
        img = decode_image(b64_image)
    except FaceError as exc:
        return {**out, "reason": str(exc)}
    boxes = get_engine().locate(img)
    out["faces"] = len(boxes)
    if not boxes:
        return {**out, "reason": "No face found – look at the camera"}
    if len(boxes) > 1:
        return {**out, "reason": "More than one face – only one person"}
    top, right, bottom, left = boxes[0]
    h, w = img.shape[:2]
    out["face_px"] = int(min(bottom - top, right - left))
    if out["face_px"] < MIN_FACE_PX:
        return {**out, "reason": "Come closer to the camera"}
    if top < 0 or left < 0 or bottom > h or right > w:
        return {**out, "reason": "Keep your whole face inside the frame"}
    crop = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    crop = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA if crop.shape[0] > 160 else cv2.INTER_LINEAR)
    out["brightness"] = round(float(crop.mean()), 1)
    out["sharpness"] = round(float(cv2.Laplacian(crop, cv2.CV_64F).var()), 1)
    if out["brightness"] < BRIGHT_MIN:
        return {**out, "reason": "Too dark – turn on a light in front of you"}
    if out["brightness"] > BRIGHT_MAX:
        return {**out, "reason": "Too bright – avoid light directly behind/ on the camera"}
    if out["sharpness"] < MIN_SHARPNESS:
        return {**out, "reason": "Hold still – image is blurry"}
    return {**out, "ok": True, "reason": "Perfect – hold still"}


@dataclass
class Match:
    staff_id: Optional[int]
    distance: float
    reason: str = ""


def match(embedding: np.ndarray, gallery: list[tuple[int, np.ndarray]]) -> Match:
    """Nearest-neighbour search by Euclidean distance.

    gallery: [(staff_id, vector), ...] – a person may have several vectors.
    Returns staff_id=None when nobody is within threshold, or when the best two
    *different* people are too close to call (protects against look-alikes).
    """
    if not gallery:
        return Match(None, float("inf"), "No staff registered")
    ids = np.array([g[0] for g in gallery])
    mat = np.stack([g[1] for g in gallery])
    dists = np.linalg.norm(mat - embedding, axis=1)
    best = int(np.argmin(dists))
    best_d, best_id = float(dists[best]), int(ids[best])
    if best_d > engine_threshold():
        return Match(None, best_d, "Face not recognised")
    others = dists[ids != best_id]
    if others.size and float(others.min()) - best_d < AMBIGUITY_MARGIN:
        return Match(None, best_d, "Ambiguous match – try again or re-register with more photos")
    return Match(best_id, best_d)
