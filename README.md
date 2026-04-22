# ROS Bag Filter

Unified ROS1 and ROS2 bag filtering with one Python package, one CLI, and one Tk GUI.

## Features
- ROS1 `.bag` and ROS2 `rosbag2` directory support from the same codebase
- Topic whitelist filtering
- Absolute ROS-time or relative-time windows
- Optional PointCloud2 spatial filter with independent `x/y/z` min/max bounds
- Background GUI execution with progress logging and cancellation
- PyPI-ready packaging with `rosbag-filter` and `rosbag-filter-app` entry points

## Install

Python 3.8+ is supported.

```bash
pip install "rosbag-filter[gui]"
```

`tkinter` is a system dependency on Linux. On Ubuntu:

```bash
sudo apt-get install python3-tk
```

## CLI

Show help:

```bash
rosbag-filter --help
```

Filter a ROS1 bag:

```bash
rosbag-filter \
  --input /data/run.bag \
  --output /data/run_filtered.bag \
  --topics /imu,/tf \
  --time-mode relative \
  --start 5.0 \
  --end 25.0
```

Filter a ROS2 bag and apply PointCloud2 bounds:

```bash
rosbag-filter \
  --input /data/run_ros2 \
  --output /data/run_ros2_filtered \
  --topics /cloud_registered,/tf \
  --time-mode ros \
  --start 1710000000.0 \
  --end 1710000030.0 \
  --workers 8 \
  --pc2-topic /cloud_registered \
  --pc2-x-min -1.0 \
  --pc2-x-max 2.0 \
  --pc2-z-max 1.5
```

## GUI

Launch the GUI:

```bash
rosbag-filter-app
```

The PointCloud2 controls are hidden in the `Advanced PointCloud2 Filter` panel by default. Expand the panel only when you want to enable spatial filtering and set `x/y/z` bounds.

`rosbag-filter-gui` is also installed as a compatibility alias.

For direct source usage, the legacy wrappers still work:

```bash
python3 rosbag_filter_app.py
python3 rosbag2_filter_gui.py
```

## Development

Install dev dependencies:

```bash
pip install -e ".[dev,gui]"
```

Run tests:

```bash
pytest
```

Build distributions:

```bash
python -m build
python -m twine check dist/*
```

## Release

The repository includes GitHub Actions workflows for CI, TestPyPI, and PyPI trusted publishing.

Before the first release:

1. Create the `rosbag-filter` project on TestPyPI.
2. Create the `rosbag-filter` project on PyPI.
3. Configure the `testpypi` and `pypi` GitHub environments.
4. Add trusted publishing for this GitHub repository in both indexes.

Suggested release flow:

1. Run the TestPyPI workflow manually.
2. Validate installation from TestPyPI in a clean environment.
3. Push a version tag like `v0.2.0` to publish to PyPI.
