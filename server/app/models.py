from __future__ import annotations

from typing import Literal
from pydantic import BaseModel as PydanticBaseModel, Field, model_validator


class BaseModel(PydanticBaseModel):
    model_config = {'allow_inf_nan': False}


class Vec2(BaseModel):
    x: float
    y: float


class Segment(BaseModel):
    id: str
    start: Vec2
    end: Vec2
    height: float | None = None
    kind: str = "wall"


class Furniture(BaseModel):
    id: str
    category: str = "object"
    center: Vec2
    width: float = Field(gt=0)
    depth: float = Field(gt=0)
    rotation: float = 0.0


class NoGoZone(BaseModel):
    id: str
    name: str = "Strefa zakazana"
    x: float
    y: float
    width: float = Field(gt=0.02)
    height: float = Field(gt=0.02)


class VirtualWall(BaseModel):
    id: str
    name: str = "Wirtualna ściana"
    start: Vec2
    end: Vec2


class Dock(BaseModel):
    x: float
    y: float
    rotation: float = 0.0


class RoomScanPayload(BaseModel):
    name: str = "Skan z iPhone"
    source: str = "apple-roomplan"
    device_name: str | None = None
    walls: list[Segment] = []
    doors: list[Segment] = []
    windows: list[Segment] = []
    openings: list[Segment] = []
    furniture: list[Furniture] = []


class MapDocument(BaseModel):
    @model_validator(mode='after')
    def valid_geometry(self):
        groups = [self.walls, self.doors, self.windows, self.openings, self.furniture, self.no_go_zones, self.virtual_walls]
        if sum(map(len, groups)) > 3000:
            raise ValueError('Mapa może zawierać najwyżej 3000 obiektów.')
        for group in groups:
            if len({v.id for v in group}) != len(group):
                raise ValueError('Identyfikatory obiektów w warstwie muszą być unikalne.')
        return self
    version: int = 1
    revision: int = 0
    name: str = "Mieszkanie"
    source: str = "manual"
    walls: list[Segment] = []
    doors: list[Segment] = []
    windows: list[Segment] = []
    openings: list[Segment] = []
    furniture: list[Furniture] = []
    no_go_zones: list[NoGoZone] = []
    virtual_walls: list[VirtualWall] = []
    dock: Dock | None = None


class RobotPose(BaseModel):
    x: float
    y: float
    heading: float = 0.0


class RobotStatus(BaseModel):
    provider: Literal["mock", "homeassistant", "local_miot"]
    state: str
    error_code: int | None = None
    error_name: str | None = None
    bumper_hit: bool = False
    bumper_supported: bool = False
    battery: int | None = None
    pose: RobotPose | None = None
    supports_pose: bool = False
    pose_source: str | None = None
    pose_confidence: float | None = None
    pose_updated_at: str | None = None
    pose_note: str | None = None
    supports_software_no_go: bool = False
    detail: str | None = None


class RobotActionResult(BaseModel):
    ok: bool
    action: str
    detail: str | None = None
