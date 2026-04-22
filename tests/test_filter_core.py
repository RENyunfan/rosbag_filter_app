from __future__ import annotations

from pathlib import Path
from inspect import signature

import numpy as np
import pytest

from rosbag_filter.core import _prepare_output_path, filter_bag, inspect_bag
from rosbag_filter.models import FilterSpec, PointCloudBoundsFilter


rosbags = pytest.importorskip("rosbags")

from rosbags.highlevel import AnyReader
from rosbags.rosbag1 import Writer as Rosbag1Writer
from rosbags.rosbag2 import Writer as Rosbag2Writer
from rosbags.typesys import Stores, get_typestore


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
    packed = np.array(points, dtype=dtype)
    return PointCloud2(
        header=Header(stamp=Time(sec=0, nanosec=0), frame_id="map"),
        height=1,
        width=len(points),
        fields=fields,
        is_bigendian=False,
        point_step=dtype.itemsize,
        row_step=dtype.itemsize * len(points),
        data=packed.view(np.uint8).reshape(-1),
        is_dense=True,
    )


def _write_ros1_bag(path: Path) -> None:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    String = typestore.types["std_msgs/msg/String"]
    with Rosbag1Writer(path) as writer:
        chatter = writer.add_connection("/chatter", String.__msgtype__, typestore=typestore)
        writer.write(chatter, 1_000_000_000, typestore.serialize_ros1(String(data="a"), String.__msgtype__))
        writer.write(chatter, 2_000_000_000, typestore.serialize_ros1(String(data="b"), String.__msgtype__))
        writer.write(chatter, 3_000_000_000, typestore.serialize_ros1(String(data="c"), String.__msgtype__))


def _write_ros2_bag(path: Path) -> None:
    typestore = get_typestore(Stores.ROS2_FOXY)
    String = typestore.types["std_msgs/msg/String"]
    PointCloud2 = typestore.types["sensor_msgs/msg/PointCloud2"]
    writer_kwargs = {"version": 9} if "version" in signature(Rosbag2Writer).parameters else {}
    with Rosbag2Writer(path, **writer_kwargs) as writer:
        chatter = writer.add_connection("/chatter", String.__msgtype__, typestore=typestore)
        cloud = writer.add_connection("/cloud_registered", PointCloud2.__msgtype__, typestore=typestore)
        writer.write(chatter, 1_000_000_000, typestore.serialize_cdr(String(data="hi"), String.__msgtype__))
        cloud_msg = _make_pointcloud_message(
            typestore,
            [(-1.0, 0.0, 0.25), (0.5, 1.0, 0.5), (0.75, 2.0, 1.5)],
        )
        writer.write(cloud, 2_000_000_000, typestore.serialize_cdr(cloud_msg, PointCloud2.__msgtype__))


def test_filter_ros1_topics_and_time_window(tmp_path: Path) -> None:
    input_bag = tmp_path / "input.bag"
    output_bag = tmp_path / "filtered.bag"
    _write_ros1_bag(input_bag)

    info = inspect_bag(input_bag)
    spec = FilterSpec(
        input_path=input_bag,
        output_path=output_bag,
        topics=("/chatter",),
        start_time_ns=info.start_time_ns + 1_000_000_000,
        end_time_ns=info.end_time_ns,
        format="ros1",
        workers=1,
    )
    result = filter_bag(spec)
    assert result.messages_written == 2

    with AnyReader([output_bag]) as reader:
        payloads = [reader.deserialize(raw, conn.msgtype).data for conn, _, raw in reader.messages()]
    assert payloads == ["b", "c"]


def test_filter_ros2_pointcloud_bounds(tmp_path: Path) -> None:
    input_bag = tmp_path / "input_ros2"
    output_bag = tmp_path / "filtered_ros2"
    _write_ros2_bag(input_bag)

    info = inspect_bag(input_bag)
    spec = FilterSpec(
        input_path=input_bag,
        output_path=output_bag,
        topics=("/cloud_registered",),
        start_time_ns=info.start_time_ns,
        end_time_ns=info.end_time_ns,
        format="ros2",
        workers=1,
        pointcloud_filter=PointCloudBoundsFilter(
            topic="/cloud_registered",
            x_min=0.0,
            x_max=1.0,
            z_max=1.0,
        ),
    )
    result = filter_bag(spec)
    assert result.pointcloud_messages_filtered == 1

    with AnyReader([output_bag]) as reader:
        messages = [reader.deserialize(raw, conn.msgtype) for conn, _, raw in reader.messages()]
    assert len(messages) == 1
    assert messages[0].width == 1


def test_prepare_output_path_increments_existing_ros1_file(tmp_path: Path) -> None:
    existing = tmp_path / "filtered.bag"
    existing.write_bytes(b"")

    output = _prepare_output_path(existing, "ros1")

    assert output == tmp_path / "filtered(1).bag"


def test_prepare_output_path_increments_existing_ros2_folder(tmp_path: Path) -> None:
    existing = tmp_path / "filtered_ros2"
    existing.mkdir()

    output = _prepare_output_path(existing, "ros2")

    assert output == tmp_path / "filtered_ros2(1)"
