"""Payload/sensor knowledge base.

The conformance datasets specify a *requirement* (GSD, overlaps, strip overlap,
line spacing) but not the sensor geometry. Coverage geometry (AGL, footprint,
swath spacing) must therefore be derived from a documented camera model, which
lives here with provenance-style notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import CameraModel

#: Reference sensors used to derive AGL/footprint from a required GSD.
CAMERA_CATALOG: dict[str, CameraModel] = {
    "rgb": CameraModel(
        id="rgb_mapping_24mp",
        image_width_px=6000,
        image_height_px=4000,
        focal_length_mm=16.0,
        pixel_pitch_um=3.9,
    ),
    "rgb_video": CameraModel(
        id="rgb_video_20mp",
        image_width_px=5472,
        image_height_px=3648,
        focal_length_mm=12.3,
        pixel_pitch_um=3.3,
    ),
    "rgb_mapping": CameraModel(
        id="rgb_mapping_20mp",
        image_width_px=5472,
        image_height_px=3648,
        focal_length_mm=12.3,
        pixel_pitch_um=3.3,
    ),
    "multispectral": CameraModel(
        id="multispectral_5band",
        image_width_px=1280,
        image_height_px=960,
        focal_length_mm=5.4,
        pixel_pitch_um=3.75,
    ),
    "thermal": CameraModel(
        id="thermal_640",
        image_width_px=640,
        image_height_px=512,
        focal_length_mm=13.0,
        pixel_pitch_um=12.0,
    ),
    "lidar": CameraModel(
        id="lidar_scanner",
        image_width_px=0,
        image_height_px=0,
        focal_length_mm=0.0,
        pixel_pitch_um=0.0,
        fov_across_deg=70.0,
    ),
    "geophysics": CameraModel(
        id="magnetometer_towed",
        image_width_px=0,
        image_height_px=0,
        focal_length_mm=0.0,
        pixel_pitch_um=0.0,
        fov_across_deg=None,
    ),
}

CAMERA_NOTES: dict[str, str] = {
    "rgb": "24 MP APS-C mapping camera, 16 mm lens (Geoscan 201/401 photogrammetry class)",
    "rgb_video": "20 MP 4/3 video/mapping camera, 12.3 mm lens",
    "rgb_mapping": "20 MP mapping camera, 12.3 mm lens",
    "multispectral": "5-band multispectral array, 5.4 mm lens, 1280x960 per band",
    "thermal": "uncooled LWIR 640x512, 13 mm lens",
    "lidar": "rotating LiDAR, across-track FOV 70 deg",
    "geophysics": "towed magnetometer: profile spacing prescribed by survey method",
}

#: Additional survey-speed derating: sensors that need slow flight.
SURVEY_SPEED_FACTOR: dict[str, float] = {
    "rgb": 1.0,
    "rgb_video": 0.9,
    "rgb_mapping": 1.0,
    "multispectral": 0.85,
    "thermal": 0.85,
    "lidar": 0.8,
    "geophysics": 0.7,
}


@dataclass
class PayloadKb:
    cameras: dict[str, CameraModel]

    def camera_for(self, payload_type: str) -> CameraModel | None:
        return self.cameras.get(payload_type)

    def survey_speed_factor(self, payload_type: str) -> float:
        return SURVEY_SPEED_FACTOR.get(payload_type, 0.9)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for ptype, cam in self.cameras.items():
            out.append(
                {
                    "payload_type": ptype,
                    "camera_id": cam.id,
                    "image_px": [cam.image_width_px, cam.image_height_px],
                    "focal_length_mm": cam.focal_length_mm,
                    "pixel_pitch_um": cam.pixel_pitch_um,
                    "fov_across_deg": cam.fov_across_deg,
                    "survey_speed_factor": SURVEY_SPEED_FACTOR.get(ptype),
                    "note": CAMERA_NOTES.get(ptype),
                }
            )
        return out


def default_payload_kb() -> PayloadKb:
    return PayloadKb(cameras=dict(CAMERA_CATALOG))
