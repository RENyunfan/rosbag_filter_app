from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Tuple

BagFormat = Literal["ros1", "ros2"]
BagFormatHint = Literal["auto", "ros1", "ros2"]
TimeMode = Literal["ros", "relative"]


@dataclass(frozen=True)
class BagTopicInfo:
    name: str
    msgtype: str
    message_count: int


@dataclass(frozen=True)
class BagInfo:
    path: Path
    format: BagFormat
    start_time_ns: int
    end_time_ns: int
    duration_ns: int
    message_count: int
    topics: Tuple[BagTopicInfo, ...]


@dataclass(frozen=True)
class PointCloudBoundsFilter:
    topic: str
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    z_min: Optional[float] = None
    z_max: Optional[float] = None

    def active_bounds(self) -> dict[str, tuple[Optional[float], Optional[float]]]:
        return {
            "x": (self.x_min, self.x_max),
            "y": (self.y_min, self.y_max),
            "z": (self.z_min, self.z_max),
        }

    def has_any_bound(self) -> bool:
        return any(
            value is not None
            for value in (self.x_min, self.x_max, self.y_min, self.y_max, self.z_min, self.z_max)
        )

    def validate(self) -> None:
        if not self.topic.strip():
            raise ValueError("PointCloud2 filtering requires a topic.")
        if not self.has_any_bound():
            raise ValueError("PointCloud2 filtering requires at least one x/y/z bound.")
        for axis, (lower, upper) in self.active_bounds().items():
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"PointCloud2 {axis} min must be <= max.")


@dataclass(frozen=True)
class FilterSpec:
    input_path: Path
    output_path: Path
    topics: Tuple[str, ...]
    start_time_ns: int
    end_time_ns: int
    format: BagFormatHint = "auto"
    workers: int = 1
    pointcloud_filter: Optional[PointCloudBoundsFilter] = None

    def validate(self) -> None:
        if not self.topics:
            raise ValueError("At least one topic must be selected.")
        if self.end_time_ns <= self.start_time_ns:
            raise ValueError("End time must be greater than start time.")
        if self.workers < 1:
            raise ValueError("Workers must be >= 1.")
        if self.pointcloud_filter is not None:
            self.pointcloud_filter.validate()
            if self.pointcloud_filter.topic not in self.topics:
                raise ValueError("PointCloud2 filter topic must be included in selected topics.")


@dataclass(frozen=True)
class FilterProgressEvent:
    kind: Literal["log", "progress"]
    message: str = ""
    current: int = 0
    total: int = 0


ProgressCallback = Callable[[FilterProgressEvent], None]


@dataclass(frozen=True)
class FilterResult:
    input_path: Path
    output_path: Path
    format: BagFormat
    messages_read: int
    messages_written: int
    pointcloud_messages_filtered: int
    duration_seconds: float
    cancelled: bool = False
