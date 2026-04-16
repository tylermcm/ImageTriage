"""Optional AKAZE-based local feature verification for culling clustering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class AkazeFeatures:
    """Local AKAZE features extracted from one image."""

    keypoint_positions: np.ndarray
    descriptors: np.ndarray
    image_shape: tuple[int, int]


@dataclass
class AkazeMatchResult:
    """Traceable match statistics for one AKAZE feature comparison."""

    good_match_count: int
    inlier_count: int
    inlier_ratio: float
    median_distance: float
    passed: bool


def compute_akaze_features(
    path: Path,
    *,
    max_side: int = 1024,
) -> Optional[AkazeFeatures]:
    """Compute AKAZE features from an image file with lightweight resizing."""

    cv2 = _import_cv2()

    image = _read_grayscale_image(path, cv2)
    if image is None:
        return None

    if max(image.shape[:2]) > max_side:
        scale = float(max_side) / float(max(image.shape[:2]))
        new_width = max(1, int(round(image.shape[1] * scale)))
        new_height = max(1, int(round(image.shape[0] * scale)))
        image = cv2.resize(
            image,
            (new_width, new_height),
            interpolation=cv2.INTER_AREA,
        )

    detector = cv2.AKAZE_create()
    keypoints, descriptors = detector.detectAndCompute(image, None)
    if not keypoints or descriptors is None or len(keypoints) < 4:
        return None

    keypoint_positions = np.asarray(
        [keypoint.pt for keypoint in keypoints],
        dtype=np.float32,
    )
    return AkazeFeatures(
        keypoint_positions=keypoint_positions,
        descriptors=np.asarray(descriptors),
        image_shape=(int(image.shape[0]), int(image.shape[1])),
    )


def match_akaze_features(
    left: Optional[AkazeFeatures],
    right: Optional[AkazeFeatures],
    *,
    ratio_test_threshold: float,
    min_good_matches: int,
    min_inliers: int,
    min_inlier_ratio: float,
    ransac_reproj_threshold: float = 4.0,
) -> Optional[AkazeMatchResult]:
    """Compare two AKAZE feature sets using ratio test plus RANSAC inliers."""

    if left is None or right is None:
        return None

    cv2 = _import_cv2()

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw_matches = matcher.knnMatch(left.descriptors, right.descriptors, k=2)

    good_matches = []
    distances: list[float] = []
    for candidate in raw_matches:
        if len(candidate) < 2:
            continue
        first_match, second_match = candidate
        if first_match.distance < ratio_test_threshold * second_match.distance:
            good_matches.append(first_match)
            distances.append(float(first_match.distance))

    if len(good_matches) < min_good_matches:
        return AkazeMatchResult(
            good_match_count=len(good_matches),
            inlier_count=0,
            inlier_ratio=0.0,
            median_distance=float(np.median(distances)) if distances else 0.0,
            passed=False,
        )

    left_points = np.float32(
        [left.keypoint_positions[match.queryIdx] for match in good_matches]
    ).reshape(-1, 1, 2)
    right_points = np.float32(
        [right.keypoint_positions[match.trainIdx] for match in good_matches]
    ).reshape(-1, 1, 2)

    _, inlier_mask = cv2.findHomography(
        left_points,
        right_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=ransac_reproj_threshold,
    )
    inlier_count = int(inlier_mask.ravel().sum()) if inlier_mask is not None else 0
    inlier_ratio = (
        float(inlier_count) / float(len(good_matches))
        if good_matches
        else 0.0
    )
    median_distance = float(np.median(distances)) if distances else 0.0

    return AkazeMatchResult(
        good_match_count=len(good_matches),
        inlier_count=inlier_count,
        inlier_ratio=inlier_ratio,
        median_distance=median_distance,
        passed=(
            len(good_matches) >= min_good_matches
            and inlier_count >= min_inliers
            and inlier_ratio >= min_inlier_ratio
        ),
    )


def _read_grayscale_image(path: Path, cv2: object) -> Optional[np.ndarray]:
    """Read an image from disk into a grayscale OpenCV array."""

    try:
        encoded = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None

    if encoded.size == 0:
        return None

    decoded = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        return None
    return decoded


def _import_cv2() -> object:
    """Import OpenCV lazily so AKAZE remains optional."""

    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "AKAZE verification requires OpenCV. Install opencv-python-headless>=4.10."
        ) from exc

    return cv2
