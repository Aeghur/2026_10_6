"""Radxa/MSPM0 task-control payloads carried by the common binary frame."""
from __future__ import annotations

from dataclasses import dataclass
import struct

from protocol import Frame, encode_frame

TASK_PROTOCOL_VERSION = 1

TYPE_TASK_START = 0x20
TYPE_TASK_QUERY = 0x21
TYPE_TASK_STATUS = 0xA0

MODE_SERVICE = 0
MODE_DIAGONAL = 1
MODE_ELLIPSE = 2
MODE_INFINITY = 3

STATE_BOOTING = 0
STATE_READY = 1
STATE_ACCEPTED = 2
STATE_RUNNING = 3
STATE_SUCCEEDED = 4
STATE_FAILED = 5
STATE_BUSY = 6
STATE_REJECTED = 7
STATE_FAULT = 8

REASON_NONE = 0
REASON_FORMAT = 1
REASON_SESSION = 2
REASON_BUSY = 3
REASON_TIMEOUT = 4
REASON_FREQUENCY = 5
REASON_HARDWARE = 6
REASON_VERIFY_OUTPUT = 7
REASON_CAPACITY = 8
REASON_ID_CONFLICT = 9
REASON_NOT_READY = 10
REASON_NOT_FOUND = 11

UNKNOWN_CONTROL_MS = 0xFFFFFFFF


@dataclass(frozen=True)
class TaskStart:
    mode: int
    request_id: int
    session_id: int


@dataclass(frozen=True)
class TaskQuery:
    request_id: int
    session_id: int


@dataclass(frozen=True)
class TaskStatus:
    mode: int
    request_id: int
    state: int
    reason: int
    elapsed_ms: int
    stable_ms: int
    control_ms: int
    session_id: int


def _require_u32(name: str, value: int, *, nonzero: bool = False) -> None:
    minimum = 1 if nonzero else 0
    if not minimum <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must be in {minimum}..4294967295")


def encode_task_start(
    sequence: int, mode: int, request_id: int, session_id: int
) -> bytes:
    if mode not in (MODE_DIAGONAL, MODE_ELLIPSE, MODE_INFINITY):
        raise ValueError("task mode must be 1, 2, or 3")
    _require_u32("request_id", request_id, nonzero=True)
    _require_u32("session_id", session_id, nonzero=True)
    payload = struct.pack(
        "<BBII", TASK_PROTOCOL_VERSION, mode, request_id, session_id
    )
    return encode_frame(TYPE_TASK_START, sequence, payload)


def encode_task_query(sequence: int, request_id: int, session_id: int) -> bytes:
    _require_u32("request_id", request_id)
    _require_u32("session_id", session_id)
    if request_id != 0 and session_id == 0:
        raise ValueError("a specific task query requires a nonzero session_id")
    payload = struct.pack("<BII", TASK_PROTOCOL_VERSION, request_id, session_id)
    return encode_frame(TYPE_TASK_QUERY, sequence, payload)


def decode_task_start(frame: Frame) -> TaskStart:
    if frame.frame_type != TYPE_TASK_START or len(frame.payload) != 10:
        raise ValueError("TASK_START must have type 0x20 and a 10-byte payload")
    version, mode, request_id, session_id = struct.unpack("<BBII", frame.payload)
    if version != TASK_PROTOCOL_VERSION:
        raise ValueError("unsupported task protocol version")
    if mode not in (MODE_DIAGONAL, MODE_ELLIPSE, MODE_INFINITY):
        raise ValueError("invalid task mode")
    if request_id == 0 or session_id == 0:
        raise ValueError("TASK_START IDs must be nonzero")
    return TaskStart(mode, request_id, session_id)


def decode_task_query(frame: Frame) -> TaskQuery:
    if frame.frame_type != TYPE_TASK_QUERY or len(frame.payload) != 9:
        raise ValueError("TASK_QUERY must have type 0x21 and a 9-byte payload")
    version, request_id, session_id = struct.unpack("<BII", frame.payload)
    if version != TASK_PROTOCOL_VERSION:
        raise ValueError("unsupported task protocol version")
    if request_id != 0 and session_id == 0:
        raise ValueError("a specific task query requires a nonzero session_id")
    return TaskQuery(request_id, session_id)


def decode_task_status(frame: Frame) -> TaskStatus:
    if frame.frame_type != TYPE_TASK_STATUS or len(frame.payload) != 24:
        raise ValueError("TASK_STATUS must have type 0xA0 and a 24-byte payload")
    fields = struct.unpack("<BBIBBIIII", frame.payload)
    version, mode, request_id, state, reason, elapsed, stable, control, session = fields
    if version != TASK_PROTOCOL_VERSION:
        raise ValueError("unsupported task protocol version")
    if not MODE_SERVICE <= mode <= MODE_INFINITY:
        raise ValueError("invalid task mode")
    if not STATE_BOOTING <= state <= STATE_FAULT:
        raise ValueError("invalid task state")
    if not REASON_NONE <= reason <= REASON_NOT_FOUND:
        raise ValueError("invalid task reason")
    if session == 0:
        raise ValueError("TASK_STATUS session_id must be nonzero")
    return TaskStatus(mode, request_id, state, reason, elapsed, stable, control, session)
