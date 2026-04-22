from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Optional

from .core import FilterCancelled, filter_bag, inspect_bag, resolve_time_window_ns
from .models import FilterProgressEvent, FilterSpec, PointCloudBoundsFilter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Filter ROS1 and ROS2 bag files from one CLI.")
    parser.add_argument("--input", required=True, help="Input ROS1 .bag file or ROS2 bag directory.")
    parser.add_argument("--output", required=True, help="Output ROS1 .bag file or ROS2 bag directory.")
    parser.add_argument(
        "--topics",
        default="",
        help="Comma-separated list of topics to keep. Defaults to all topics.",
    )
    parser.add_argument(
        "--time-mode",
        choices=("ros", "relative"),
        default="ros",
        help="Interpret --start and --end as absolute ROS seconds or seconds relative to bag start.",
    )
    parser.add_argument("--start", type=float, required=True, help="Start of the filter window in seconds.")
    parser.add_argument("--end", type=float, required=True, help="End of the filter window in seconds.")
    parser.add_argument("--workers", type=int, default=1, help="Worker processes for PointCloud2 transforms.")
    parser.add_argument(
        "--format",
        choices=("auto", "ros1", "ros2"),
        default="auto",
        help="Override input format detection.",
    )
    parser.add_argument("--pc2-topic", default="", help="PointCloud2 topic to spatially filter.")
    parser.add_argument("--pc2-x-min", type=float, default=None)
    parser.add_argument("--pc2-x-max", type=float, default=None)
    parser.add_argument("--pc2-y-min", type=float, default=None)
    parser.add_argument("--pc2-y-max", type=float, default=None)
    parser.add_argument("--pc2-z-min", type=float, default=None)
    parser.add_argument("--pc2-z-max", type=float, default=None)
    return parser


def _split_topics(raw_topics: str) -> tuple[str, ...]:
    topics = [topic.strip() for topic in raw_topics.split(",") if topic.strip()]
    return tuple(dict.fromkeys(topics))


def _build_pointcloud_filter(args: argparse.Namespace) -> Optional[PointCloudBoundsFilter]:
    if not args.pc2_topic and all(
        getattr(args, name) is None
        for name in ("pc2_x_min", "pc2_x_max", "pc2_y_min", "pc2_y_max", "pc2_z_min", "pc2_z_max")
    ):
        return None
    return PointCloudBoundsFilter(
        topic=args.pc2_topic.strip(),
        x_min=args.pc2_x_min,
        x_max=args.pc2_x_max,
        y_min=args.pc2_y_min,
        y_max=args.pc2_y_max,
        z_min=args.pc2_z_min,
        z_max=args.pc2_z_max,
    )


def _event_printer(event: FilterProgressEvent) -> None:
    if event.kind == "log":
        print(event.message, file=sys.stderr)
    elif event.kind == "progress":
        print(
            f"Progress: {event.current}/{event.total}",
            file=sys.stderr,
        )


def make_filter_spec(args: argparse.Namespace) -> FilterSpec:
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    bag_info = inspect_bag(input_path, args.format)
    topics = _split_topics(args.topics) or tuple(topic.name for topic in bag_info.topics)
    start_ns, end_ns = resolve_time_window_ns(
        bag_info,
        time_mode=args.time_mode,
        start=args.start,
        end=args.end,
    )
    return FilterSpec(
        input_path=input_path,
        output_path=output_path,
        topics=topics,
        start_time_ns=start_ns,
        end_time_ns=end_ns,
        format=args.format,
        workers=max(1, args.workers),
        pointcloud_filter=_build_pointcloud_filter(args),
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        spec = make_filter_spec(args)
        result = filter_bag(spec, event_cb=_event_printer)
    except FilterCancelled as exc:
        print(str(exc), file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        "Filtered bag written to "
        f"{result.output_path} ({result.messages_written} messages, {result.duration_seconds:.2f}s)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
