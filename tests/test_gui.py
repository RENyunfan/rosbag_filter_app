from __future__ import annotations

from pathlib import Path

import pytest

from rosbag_filter.gui import RosbagFilterGUI
from rosbag_filter.models import BagInfo, BagTopicInfo


def _fake_bag_info() -> BagInfo:
    return BagInfo(
        path=Path("/tmp/fake_ros2_bag"),
        format="ros2",
        start_time_ns=1_000_000_000,
        end_time_ns=4_000_000_000,
        duration_ns=3_000_000_000,
        message_count=5,
        topics=(BagTopicInfo(name="/cloud_registered", msgtype="sensor_msgs/msg/PointCloud2", message_count=5),),
    )


def test_cloud_panel_collapsed_by_default(tk_root) -> None:
    app = RosbagFilterGUI(tk_root)
    tk_root.update_idletasks()
    assert app.cloud_panel.expanded is False
    assert app.cloud_panel.body.winfo_ismapped() == 0


def test_cloud_values_survive_collapse(tk_root) -> None:
    app = RosbagFilterGUI(tk_root)
    app.cloud_panel.set_expanded(True)
    app.cloud_bounds_vars["x_min"].set("1.25")
    app.cloud_panel.set_expanded(False)
    app.cloud_panel.set_expanded(True)
    assert app.cloud_bounds_vars["x_min"].get() == "1.25"


def test_cloud_filter_requires_topic_and_bounds(tk_root) -> None:
    app = RosbagFilterGUI(tk_root)
    app._bag_info = _fake_bag_info()
    app.topics_listbox.insert("end", "/cloud_registered")
    app.topics_listbox.selection_set(0)
    app.time_mode_var.set("relative")
    app.start_var.set("0.0")
    app.end_var.set("1.0")
    app.cloud_enabled_var.set(True)
    app.cloud_topic_var.set("")
    with pytest.raises(ValueError):
        spec = app._build_spec()
        spec.validate()


@pytest.fixture
def tk_root():
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk tests require a working display")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()
