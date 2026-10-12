"""Lossless quarter turns and conservative line-based deskew of page images."""

import base64
import math

import cv2
import numpy as np


def detect_skew(image: np.ndarray) -> float:
	"""Return the dominant horizontal line angle in image coordinates."""
	gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
	scale = min(1, 1600 / max(gray.shape))
	if scale < 1:
		gray = cv2.resize(gray, None, fx=scale, fy=scale)
	edges = cv2.Canny(gray, 50, 150)
	lines = cv2.HoughLinesP(
		edges, 1, np.pi / 1800, threshold=40,
		minLineLength=max(30, gray.shape[1] // 8), maxLineGap=20,
	)
	if lines is None:
		return 0.0
	angles = []
	for x1, y1, x2, y2 in lines[:, 0]:
		angle = math.degrees(math.atan2(int(y2 - y1), int(x2 - x1)))
		angle = (angle + 90) % 180 - 90
		if abs(angle) < 45:
			angles.append(angle)
	return float(np.median(angles)) if angles else 0.0


def correct_image(data_url: str, clockwise: int) -> bytes | None:
	"""Return corrected PNG bytes, or None when no correction is needed."""
	if clockwise not in (0, 90, 180, 270):
		raise ValueError("Rotation must be 0, 90, 180 or 270")
	raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
	image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
	if image is None:
		raise ValueError("Cannot decode scanned page image")
	if clockwise:
		image = cv2.rotate(image, {
			90: cv2.ROTATE_90_CLOCKWISE,
			180: cv2.ROTATE_180,
			270: cv2.ROTATE_90_COUNTERCLOCKWISE,
		}[clockwise])
	angle = detect_skew(image)
	deskew = 0.5 <= abs(angle) <= 15
	if deskew:
		height, width = image.shape[:2]
		matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
		cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
		new_width = math.ceil(height * sine + width * cosine)
		new_height = math.ceil(height * cosine + width * sine)
		matrix[0, 2] += (new_width - width) / 2
		matrix[1, 2] += (new_height - height) / 2
		image = cv2.warpAffine(
			image, matrix, (new_width, new_height),
			flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255),
		)
	if not clockwise and not deskew:
		return None
	ok, encoded = cv2.imencode(".png", image)
	if not ok:
		raise ValueError("Cannot encode corrected page image")
	return encoded.tobytes()
