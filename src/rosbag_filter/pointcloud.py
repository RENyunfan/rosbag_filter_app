from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

from .models import BagFormat, PointCloudBoundsFilter

if TYPE_CHECKING:
    from rosbags.typesys.base import Typestore


POINTCLOUD2_MSGTYPE = "sensor_msgs/msg/PointCloud2"

_DTYPE_MAP = {
    1: ("i1", 1),
    2: ("u1", 1),
    3: ("i2", 2),
    4: ("u2", 2),
    5: ("i4", 4),
    6: ("u4", 4),
    7: ("f4", 4),
    8: ("f8", 8),
}


def _require_rosbags_typesys() -> tuple[object, object]:
    try:
        from rosbags.typesys import Stores, get_typestore
    except ImportError as exc:  # pragma: no cover - exercised when dependency is missing
        raise RuntimeError(
            "rosbags is required at runtime. Install the project dependencies first."
        ) from exc
    return Stores, get_typestore


@lru_cache(maxsize=2)
def _get_pointcloud_typestore(bag_format: BagFormat) -> "Typestore":
    Stores, get_typestore = _require_rosbags_typesys()
    store = Stores.ROS1_NOETIC if bag_format == "ros1" else Stores.ROS2_FOXY
    return get_typestore(store)


def _build_dtype(fields: list[object], point_step: int, big_endian: bool) -> np.dtype:
    byteorder = ">" if big_endian else "<"
    names: list[str] = []
    formats: list[object] = []
    offsets: list[int] = []
    seen: set[str] = set()
    for field in fields:
        name = getattr(field, "name")
        if name in seen:
            continue
        seen.add(name)
        datatype = getattr(field, "datatype")
        count = max(1, int(getattr(field, "count")))
        fmt, _ = _DTYPE_MAP[datatype]
        base = np.dtype(f"{byteorder}{fmt}")
        names.append(name)
        formats.append(base if count == 1 else (base, (count,)))
        offsets.append(int(getattr(field, "offset")))
    return np.dtype({"names": names, "formats": formats, "offsets": offsets, "itemsize": point_step})


def _serialize_pointcloud(message: object, bag_format: BagFormat) -> bytes:
    typestore = _get_pointcloud_typestore(bag_format)
    if bag_format == "ros1":
        return bytes(typestore.serialize_ros1(message, POINTCLOUD2_MSGTYPE))
    return bytes(typestore.serialize_cdr(message, POINTCLOUD2_MSGTYPE))


def _deserialize_pointcloud(raw_data: bytes, bag_format: BagFormat) -> object:
    typestore = _get_pointcloud_typestore(bag_format)
    if bag_format == "ros1":
        return typestore.deserialize_ros1(raw_data, POINTCLOUD2_MSGTYPE)
    return typestore.deserialize_cdr(raw_data, POINTCLOUD2_MSGTYPE)


def filter_pointcloud_bytes(
    raw_data: bytes,
    bag_format: BagFormat,
    bounds: PointCloudBoundsFilter,
) -> bytes:
    if not bounds.has_any_bound():
        return raw_data

    message = _deserialize_pointcloud(raw_data, bag_format)
    fields = list(getattr(message, "fields"))
    original_data = getattr(message, "data")
    if getattr(message, "point_step", 0) <= 0:
        return raw_data

    axis_names = {name for name, (lower, upper) in bounds.active_bounds().items() if lower is not None or upper is not None}
    missing = sorted(axis for axis in axis_names if axis not in {getattr(field, "name") for field in fields})
    if missing:
        raise ValueError(f"PointCloud2 message is missing required field(s): {', '.join(missing)}")

    total_points = int(getattr(message, "width")) * int(getattr(message, "height"))
    if total_points <= 0:
        return raw_data

    dtype = _build_dtype(fields, int(getattr(message, "point_step")), bool(getattr(message, "is_bigendian")))
    array = np.frombuffer(memoryview(getattr(message, "data")), dtype=dtype, count=total_points)
    keep_mask = np.ones(total_points, dtype=bool)
    for axis, (lower, upper) in bounds.active_bounds().items():
        if lower is None and upper is None:
            continue
        values = array[axis]
        if values.ndim > 1:
            values = values[:, 0]
        if lower is not None:
            keep_mask &= values >= lower
        if upper is not None:
            keep_mask &= values <= upper

    kept = array[keep_mask]
    message.height = 1
    message.width = int(kept.shape[0])
    message.row_step = int(message.width * getattr(message, "point_step"))
    if hasattr(original_data, "view"):
        message.data = kept.view(np.uint8).reshape(-1)
    else:
        message.data = kept.tobytes()
    return _serialize_pointcloud(message, bag_format)
