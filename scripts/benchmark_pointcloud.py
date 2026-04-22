#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import numpy as np

from rosbag_filter import FilterSpec, PointCloudBoundsFilter, filter_bag, inspect_bag


def _require_rosbags():
    from rosbags.rosbag2 import Writer
    from rosbags.typesys import Stores, get_typestore

    return Writer, Stores, get_typestore


def _make_pointcloud_message(typestore, points):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    PointField = typestore.types["sensor_msgs/msg/PointField"]
    PointCloud2 = typestore.types["sensor_msgs/msg/PointCloud2"]

    fields = [
        PointField(name="x", offset=0, datatype=7, count=1),
        PointField(name="y", offset=4, datatype=7, count=1),
        PointField(name="z", offset=8, datatype=7, count=1),
    ]
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4")])
    packed = np.zeros(points, dtype=dtype)
    packed["x"] = np.random.uniform(-5.0, 5.0, size=points)
    packed["y"] = np.random.uniform(-5.0, 5.0, size=points)
    packed["z"] = np.random.uniform(-1.0, 3.0, size=points)
    return PointCloud2(
        header=Header(stamp=Time(sec=0, nanosec=0), frame_id="map"),
        height=1,
        width=points,
        fields=fields,
        is_bigendian=False,
        point_step=dtype.itemsize,
        row_step=dtype.itemsize * points,
        data=packed.tobytes(),
        is_dense=True,
    )


def _write_bag(path: Path, *, messages: int, points: int) -> None:
    Writer, Stores, get_typestore = _require_rosbags()
    typestore = get_typestore(Stores.ROS2_FOXY)
    PointCloud2 = typestore.types["sensor_msgs/msg/PointCloud2"]
    cloud_msg = _make_pointcloud_message(typestore, points)
    with Writer(path, version=9) as writer:
        cloud = writer.add_connection("/cloud_registered", PointCloud2.__msgtype__, typestore=typestore)
        payload = typestore.serialize_cdr(cloud_msg, PointCloud2.__msgtype__)
        for index in range(messages):
            writer.write(cloud, 1_000_000_000 + index * 100_000_000, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark PointCloud2 bounds filtering throughput.")
    parser.add_argument("--messages", type=int, default=64)
    parser.add_argument("--points", type=int, default=200_000)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="rosbag-filter-bench-") as tmpdir:
        base = Path(tmpdir)
        input_bag = base / "input_ros2"
        _write_bag(input_bag, messages=args.messages, points=args.points)
        bag_info = inspect_bag(input_bag)
        for workers in (1, max(1, args.workers)):
            output_bag = base / f"output_{workers}"
            spec = FilterSpec(
                input_path=input_bag,
                output_path=output_bag,
                topics=("/cloud_registered",),
                start_time_ns=bag_info.start_time_ns,
                end_time_ns=bag_info.end_time_ns,
                format="ros2",
                workers=workers,
                pointcloud_filter=PointCloudBoundsFilter(
                    topic="/cloud_registered",
                    x_min=-1.0,
                    x_max=1.0,
                    z_max=1.0,
                ),
            )
            started = time.perf_counter()
            result = filter_bag(spec)
            elapsed = time.perf_counter() - started
            print(
                f"workers={workers}: wrote {result.messages_written} messages in {elapsed:.2f}s "
                f"({result.messages_written / max(elapsed, 1e-9):.2f} msg/s)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
