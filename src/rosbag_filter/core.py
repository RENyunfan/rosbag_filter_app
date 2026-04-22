from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor, TimeoutError
from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from typing import Any, Optional

from .models import (
    BagFormat,
    BagFormatHint,
    BagInfo,
    BagTopicInfo,
    FilterProgressEvent,
    FilterResult,
    FilterSpec,
    PointCloudBoundsFilter,
    ProgressCallback,
    TimeMode,
)
from .pointcloud import POINTCLOUD2_MSGTYPE, filter_pointcloud_bytes


class FilterCancelled(RuntimeError):
    """Raised when a filtering run is cancelled."""


@dataclass
class _WriteTask:
    connection: object
    timestamp: int
    data: bytes | memoryview | None = None
    future: Optional[Future[bytes]] = None

def _require_rosbags() -> tuple[object, object, object]:
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag1 import Writer as Rosbag1Writer
        from rosbags.rosbag2 import Writer as Rosbag2Writer
    except ImportError as exc:  # pragma: no cover - exercised when dependency is missing
        raise RuntimeError(
            "rosbags is required at runtime. Install the project dependencies first."
        ) from exc
    return AnyReader, Rosbag1Writer, Rosbag2Writer


def detect_bag_format(path: Path, hint: BagFormatHint = "auto") -> BagFormat:
    path = Path(path).expanduser().resolve()
    if hint == "ros1":
        if not path.is_file() or path.suffix != ".bag":
            raise ValueError(f"{path} is not a ROS1 .bag file.")
        return "ros1"
    if hint == "ros2":
        if not path.is_dir() or not (path / "metadata.yaml").exists():
            raise ValueError(f"{path} is not a ROS2 bag directory.")
        return "ros2"

    if path.is_file() and path.suffix == ".bag":
        return "ros1"
    if path.is_dir() and (path / "metadata.yaml").exists():
        return "ros2"
    raise ValueError(f"Unable to detect bag format for {path}.")


def inspect_bag(path: Path, hint: BagFormatHint = "auto") -> BagInfo:
    bag_path = Path(path).expanduser().resolve()
    bag_format = detect_bag_format(bag_path, hint)
    AnyReader, _, _ = _require_rosbags()
    with AnyReader([bag_path]) as reader:
        msgtypes_by_topic = {
            connection.topic: connection.msgtype
            for connection in getattr(reader, "connections", [])
        }
        topics = []
        for name, topic_info in sorted(getattr(reader, "topics").items()):
            message_count = int(
                getattr(topic_info, "message_count", getattr(topic_info, "msgcount", 0))
            )
            msgtype = getattr(topic_info, "msgtype", msgtypes_by_topic.get(name, ""))
            topics.append(BagTopicInfo(name=name, msgtype=msgtype, message_count=message_count))
        return BagInfo(
            path=bag_path,
            format=bag_format,
            start_time_ns=int(getattr(reader, "start_time")),
            end_time_ns=int(getattr(reader, "end_time")),
            duration_ns=int(getattr(reader, "duration")),
            message_count=int(getattr(reader, "message_count")),
            topics=tuple(topics),
        )


def resolve_time_window_ns(
    bag_info: BagInfo,
    *,
    time_mode: TimeMode,
    start: float,
    end: float,
) -> tuple[int, int]:
    if time_mode == "ros":
        start_ns = int(start * 1e9)
        end_ns = int(end * 1e9)
    else:
        start_ns = bag_info.start_time_ns + int(start * 1e9)
        end_ns = bag_info.start_time_ns + int(end * 1e9)

    start_ns = max(start_ns, bag_info.start_time_ns)
    end_ns = min(end_ns, bag_info.end_time_ns)
    if end_ns <= start_ns:
        raise ValueError("Resolved time window is empty.")
    return start_ns, end_ns


class _EventEmitter:
    def __init__(self, callback: Optional[ProgressCallback]):
        self._callback = callback
        self._lock = threading.Lock()

    def emit(self, event: FilterProgressEvent) -> None:
        if self._callback is None:
            return
        with self._lock:
            self._callback(event)

    def log(self, message: str) -> None:
        self.emit(FilterProgressEvent(kind="log", message=message))

    def progress(self, current: int, total: int) -> None:
        self.emit(FilterProgressEvent(kind="progress", current=current, total=total))


def _conflict_candidate(path: Path, index: int, bag_format: BagFormat) -> Path:
    if bag_format == "ros1" and path.suffix:
        return path.with_name(f"{path.stem}({index}){path.suffix}")
    return path.with_name(f"{path.name}({index})")


def _prepare_output_path(path: Path, bag_format: BagFormat) -> Path:
    output_path = Path(path).expanduser().resolve()
    base_path = output_path
    index = 1
    while output_path.exists():
        output_path = _conflict_candidate(base_path, index, bag_format)
        index += 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def _cleanup_output(path: Path, bag_format: BagFormat) -> None:
    if not path.exists():
        return
    if bag_format == "ros2" and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    if path.is_file():
        path.unlink(missing_ok=True)


def _build_writer_connection(
    writer: object,
    connection: object,
    *,
    bag_format: BagFormat,
    typestore: object,
) -> object:
    ext = getattr(connection, "ext", None)
    if bag_format == "ros1":
        kwargs: dict[str, Any] = {"typestore": typestore}
        callerid = getattr(ext, "callerid", None)
        if callerid is not None:
            kwargs["callerid"] = callerid
        latching = getattr(ext, "latching", None)
        if latching is not None:
            kwargs["latching"] = latching
        return writer.add_connection(getattr(connection, "topic"), getattr(connection, "msgtype"), **kwargs)

    kwargs = {"typestore": typestore}
    serialization_format = getattr(ext, "serialization_format", None)
    if serialization_format:
        kwargs["serialization_format"] = serialization_format
    offered_qos_profiles = getattr(ext, "offered_qos_profiles", None)
    if offered_qos_profiles is not None:
        kwargs["offered_qos_profiles"] = offered_qos_profiles
    rihs01 = getattr(ext, "rihs01", None)
    if rihs01 is not None:
        kwargs["rihs01"] = rihs01
    return writer.add_connection(getattr(connection, "topic"), getattr(connection, "msgtype"), **kwargs)


def _create_rosbag2_writer(writer_type: object, output_path: Path) -> object:
    params = signature(writer_type).parameters
    if "version" in params:
        return writer_type(output_path, version=9)
    return writer_type(output_path)


def _estimate_total_messages(
    bag_info: BagInfo,
    topics: tuple[str, ...],
) -> int:
    counts = {topic.name: topic.message_count for topic in bag_info.topics}
    total = sum(counts.get(topic, 0) for topic in topics)
    return max(total, 1)


def _future_result_or_cancel(future: Future[bytes], cancel_event: threading.Event) -> bytes:
    while True:
        if cancel_event.is_set():
            future.cancel()
            raise FilterCancelled("Filtering cancelled.")
        try:
            return future.result(timeout=0.1)
        except TimeoutError:
            continue


def _uses_pointcloud_filter(
    connection: object,
    cloud_filter: Optional[PointCloudBoundsFilter],
) -> bool:
    return (
        cloud_filter is not None
        and getattr(connection, "topic") == cloud_filter.topic
        and getattr(connection, "msgtype") == POINTCLOUD2_MSGTYPE
    )


def filter_bag(
    spec: FilterSpec,
    event_cb: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> FilterResult:
    spec.validate()
    run_cancel_event = cancel_event or threading.Event()
    if run_cancel_event.is_set():
        raise FilterCancelled("Filtering cancelled before start.")

    emitter = _EventEmitter(event_cb)
    AnyReader, Rosbag1Writer, Rosbag2Writer = _require_rosbags()
    bag_info = inspect_bag(spec.input_path, spec.format)
    bag_format = bag_info.format
    output_path = _prepare_output_path(spec.output_path, bag_format)
    total_estimate = _estimate_total_messages(bag_info, spec.topics)

    spec_topics = set(spec.topics)
    cloud_filter = spec.pointcloud_filter
    started_at = time.perf_counter()
    state: dict[str, Any] = {
        "messages_read": 0,
        "messages_written": 0,
        "pointcloud_messages_filtered": 0,
        "total_estimate": total_estimate,
    }

    executor: Optional[ProcessPoolExecutor] = None

    try:
        with AnyReader([spec.input_path]) as reader:
            selected_connections = [
                connection
                for connection in getattr(reader, "connections", [])
                if getattr(connection, "topic") in spec_topics
            ]
            if not selected_connections:
                raise ValueError("None of the selected topics were found in the bag.")
            if cloud_filter is not None and not any(
                getattr(connection, "topic") == cloud_filter.topic for connection in selected_connections
            ):
                raise ValueError("PointCloud2 filter topic was not found in the selected topics.")
            if cloud_filter is not None and not any(
                getattr(connection, "topic") == cloud_filter.topic
                and getattr(connection, "msgtype") == POINTCLOUD2_MSGTYPE
                for connection in selected_connections
            ):
                raise ValueError(
                    f"{cloud_filter.topic} is not a {POINTCLOUD2_MSGTYPE} topic in the input bag."
                )

            if bag_format == "ros1":
                writer = Rosbag1Writer(output_path)
            else:
                writer = _create_rosbag2_writer(Rosbag2Writer, output_path)

            with writer:
                conn_map = {
                    getattr(connection, "id"): _build_writer_connection(
                        writer,
                        connection,
                        bag_format=bag_format,
                        typestore=getattr(reader, "typestore"),
                    )
                    for connection in selected_connections
                }

                if cloud_filter is not None and spec.workers > 1:
                    executor = ProcessPoolExecutor(max_workers=spec.workers)
                    emitter.log(f"PointCloud2 bounds filter enabled with {spec.workers} worker processes.")
                else:
                    emitter.log("Filtering bag with single-process transforms.")
                pending: list[_WriteTask] = []
                pending_max = max(8, spec.workers * 4)

                def write_payload(task: _WriteTask) -> None:
                    if task.future is not None:
                        payload = _future_result_or_cancel(task.future, run_cancel_event)
                    else:
                        payload = task.data
                    if payload is None:
                        raise RuntimeError("Missing payload for write task.")
                    writer.write(task.connection, task.timestamp, payload)
                    state["messages_written"] += 1
                    if state["messages_written"] % 200 == 0:
                        emitter.progress(
                            state["messages_written"],
                            max(state["messages_written"], state["total_estimate"]),
                        )

                def drain_pending(force: bool) -> None:
                    while pending and (force or len(pending) >= pending_max):
                        task = pending.pop(0)
                        write_payload(task)

                emitter.log(
                    f"Filtering {spec.input_path.name} -> {output_path.name} ({len(selected_connections)} selected topic(s))."
                )
                for connection, timestamp, raw_data in reader.messages(
                    connections=selected_connections,
                    start=spec.start_time_ns,
                    stop=spec.end_time_ns,
                ):
                    if run_cancel_event.is_set():
                        raise FilterCancelled("Filtering cancelled.")
                    write_connection = conn_map[getattr(connection, "id")]
                    task: _WriteTask
                    if _uses_pointcloud_filter(connection, cloud_filter):
                        state["pointcloud_messages_filtered"] += 1
                        raw_bytes = bytes(raw_data)
                        if executor is not None:
                            future = executor.submit(filter_pointcloud_bytes, raw_bytes, bag_format, cloud_filter)
                            task = _WriteTask(connection=write_connection, timestamp=timestamp, future=future)
                        else:
                            payload = filter_pointcloud_bytes(raw_bytes, bag_format, cloud_filter)
                            task = _WriteTask(connection=write_connection, timestamp=timestamp, data=payload)
                    else:
                        task = _WriteTask(connection=write_connection, timestamp=timestamp, data=raw_data)

                    if executor is not None:
                        pending.append(task)
                        drain_pending(force=False)
                    else:
                        write_payload(task)
                    state["messages_read"] += 1
                    if state["messages_read"] % 200 == 0:
                        emitter.progress(state["messages_read"], total_estimate)

                drain_pending(force=True)

        if run_cancel_event.is_set():
            raise FilterCancelled("Filtering cancelled.")
        duration = time.perf_counter() - started_at
        emitter.progress(state["messages_written"], max(state["messages_written"], 1))
        emitter.log(
            "Completed filter run: "
            f"read {state['messages_read']} messages, "
            f"wrote {state['messages_written']} messages, "
            f"filtered {state['pointcloud_messages_filtered']} PointCloud2 message(s)."
        )
        return FilterResult(
            input_path=spec.input_path,
            output_path=output_path,
            format=bag_format,
            messages_read=state["messages_read"],
            messages_written=state["messages_written"],
            pointcloud_messages_filtered=state["pointcloud_messages_filtered"],
            duration_seconds=duration,
            cancelled=False,
        )
    except FilterCancelled:
        _cleanup_output(output_path, bag_format)
        raise
    except Exception:
        _cleanup_output(output_path, bag_format)
        raise
    finally:
        if executor is not None:
            should_wait = not run_cancel_event.is_set()
            executor.shutdown(wait=should_wait, cancel_futures=not should_wait)
