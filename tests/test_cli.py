from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from rosbag_filter.cli import _build_pointcloud_filter, make_filter_spec
from rosbag_filter.models import BagInfo, BagTopicInfo


def test_build_pointcloud_filter_none_when_unset() -> None:
    args = Namespace(
        pc2_topic="",
        pc2_x_min=None,
        pc2_x_max=None,
        pc2_y_min=None,
        pc2_y_max=None,
        pc2_z_min=None,
        pc2_z_max=None,
    )
    assert _build_pointcloud_filter(args) is None


def test_make_filter_spec_defaults_to_all_topics(monkeypatch, tmp_path: Path) -> None:
    bag_info = BagInfo(
        path=tmp_path / "input.bag",
        format="ros1",
        start_time_ns=1_000_000_000,
        end_time_ns=5_000_000_000,
        duration_ns=4_000_000_000,
        message_count=10,
        topics=(
            BagTopicInfo(name="/imu", msgtype="sensor_msgs/msg/Imu", message_count=4),
            BagTopicInfo(name="/tf", msgtype="tf2_msgs/msg/TFMessage", message_count=6),
        ),
    )

    monkeypatch.setattr("rosbag_filter.cli.inspect_bag", lambda path, fmt: bag_info)
    args = Namespace(
        input=str(tmp_path / "input.bag"),
        output=str(tmp_path / "output.bag"),
        topics="",
        time_mode="relative",
        start=0.0,
        end=4.0,
        workers=2,
        format="auto",
        pc2_topic="",
        pc2_x_min=None,
        pc2_x_max=None,
        pc2_y_min=None,
        pc2_y_max=None,
        pc2_z_min=None,
        pc2_z_max=None,
    )

    spec = make_filter_spec(args)
    assert spec.topics == ("/imu", "/tf")
    assert spec.start_time_ns == bag_info.start_time_ns
    assert spec.end_time_ns == bag_info.end_time_ns
