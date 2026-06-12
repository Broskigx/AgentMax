"""
AgentMax Recorder System

Session recording and playback functionality
for automation and debugging.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog

log = structlog.get_logger(__name__)


class RecordingEventType(str, Enum):
    """Types of events that can be recorded"""

    ACTION = "action"
    STATE_CHANGE = "state_change"
    API_CALL = "api_call"
    UI_INTERACTION = "ui_interaction"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    CUSTOM = "custom"


class RecordingStatus(str, Enum):
    """Status of a recording"""

    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    PROCESSING = "processing"


@dataclass
class RecordingEvent:
    """Individual event in a recording"""

    event_id: str
    event_type: RecordingEventType
    timestamp: datetime

    data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    # For state changes
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None

    # For actions
    action_name: str | None = None
    action_params: dict[str, Any] | None = None
    action_result: Any | None = None

    # For errors
    error_type: str | None = None
    error_message: str | None = None
    stack_trace: str | None = None

    # Hierarchy
    parent_event_id: str | None = None
    child_event_ids: list[str] = field(default_factory=list)

    # Tags for filtering
    tags: list[str] = field(default_factory=list)


@dataclass
class Recording:
    """Recording session"""

    recording_id: str
    name: str
    description: str | None

    status: RecordingStatus

    started_at: datetime
    stopped_at: datetime | None = None

    events: list[RecordingEvent] = field(default_factory=list)

    # Metadata
    user_id: str | None = None
    session_id: str | None = None
    device_id: str | None = None

    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Statistics
    event_count: int = 0
    error_count: int = 0
    duration_seconds: float = 0

    # Compression
    is_compressed: bool = False

    def get_size_bytes(self) -> int:
        """Calculate approximate size of recording"""
        return sum(
            len(json.dumps(event.data)) + len(json.dumps(event.metadata)) for event in self.events
        )

    def get_hash(self) -> str:
        """Generate hash of recording content"""
        content = json.dumps(
            [
                {"type": e.event_type.value, "data": e.data}
                for e in self.events[:100]  # First 100 events
            ],
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get_events_by_type(self, event_type: RecordingEventType) -> list[RecordingEvent]:
        """Get all events of a specific type"""
        return [e for e in self.events if e.event_type == event_type]

    def get_errors(self) -> list[RecordingEvent]:
        """Get all error events"""
        return self.get_events_by_type(RecordingEventType.ERROR)


@dataclass
class PlaybackState:
    """State for playback"""

    current_index: int = 0
    speed: float = 1.0
    is_paused: bool = False
    loop_count: int = 0
    start_time: datetime | None = None


@dataclass
class PlaybackResult:
    """Result of playback execution"""

    playback_id: str
    recording_id: str

    status: str
    events_executed: int
    errors: list[dict[str, Any]]

    started_at: datetime
    completed_at: datetime
    duration_ms: int


class EventFilter:
    """Filter for recording events"""

    def __init__(
        self,
        event_types: list[RecordingEventType] | None = None,
        tags: list[str] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        min_importance: int = 0,
        error_only: bool = False,
    ):
        self.event_types = event_types
        self.tags = tags
        self.from_date = from_date
        self.to_date = to_date
        self.min_importance = min_importance
        self.error_only = error_only

    def matches(self, event: RecordingEvent) -> bool:
        """Check if event matches filter"""

        if self.event_types and event.event_type not in self.event_types:
            return False

        if self.tags and not any(tag in event.tags for tag in self.tags):
            return False

        if self.from_date and event.timestamp < self.from_date:
            return False

        if self.to_date and event.timestamp > self.to_date:
            return False

        if self.error_only and event.event_type != RecordingEventType.ERROR:
            return False

        return True


class Recorder:
    """Main recording system"""

    def __init__(self, config: RecorderConfig | None = None):
        self.config = config or RecorderConfig()

        self._current_recording: Recording | None = None
        self._recordings: dict[str, Recording] = {}

        self._event_handlers: dict[RecordingEventType, list[Callable]] = {
            event_type: [] for event_type in RecordingEventType
        }

        self._filters: list[EventFilter] = []

    async def start_recording(
        self,
        name: str,
        description: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start a new recording"""

        if self._current_recording:
            raise RuntimeError("Recording already in progress")

        recording_id = str(uuid4())

        self._current_recording = Recording(
            recording_id=recording_id,
            name=name,
            description=description,
            status=RecordingStatus.RECORDING,
            started_at=datetime.now(UTC),
            user_id=user_id,
            session_id=session_id,
            tags=tags or [],
            metadata=metadata or {},
        )

        log.info("recording_started", recording_id=recording_id, name=name)

        return recording_id

    async def stop_recording(self, name: str | None = None) -> Recording | None:
        """Stop the current recording"""

        if not self._current_recording:
            return None

        recording = self._current_recording

        recording.status = RecordingStatus.STOPPED
        recording.stopped_at = datetime.now(UTC)
        recording.event_count = len(recording.events)
        recording.error_count = len(recording.get_errors())

        if recording.started_at and recording.stopped_at:
            recording.duration_seconds = (
                recording.stopped_at - recording.started_at
            ).total_seconds()

        # Store recording
        self._recordings[recording.recording_id] = recording

        log.info(
            "recording_stopped",
            recording_id=recording.recording_id,
            event_count=recording.event_count,
            duration=recording.duration_seconds,
        )

        self._current_recording = None

        return recording

    async def pause_recording(self):
        """Pause current recording"""
        if self._current_recording and self._current_recording.status == RecordingStatus.RECORDING:
            self._current_recording.status = RecordingStatus.PAUSED
            log.info("recording_paused", recording_id=self._current_recording.recording_id)

    async def resume_recording(self):
        """Resume paused recording"""
        if self._current_recording and self._current_recording.status == RecordingStatus.PAUSED:
            self._current_recording.status = RecordingStatus.RECORDING
            log.info("recording_resumed", recording_id=self._current_recording.recording_id)

    async def record_event(
        self,
        event_type: RecordingEventType,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ):
        """Record an event"""

        if not self._current_recording:
            return

        if self._current_recording.status != RecordingStatus.RECORDING:
            return

        # Check filters
        event = RecordingEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=datetime.now(UTC),
            data=data,
            metadata=metadata or {},
            tags=tags or [],
        )

        # Apply filters
        should_record = True
        for filter_obj in self._filters:
            if not filter_obj.matches(event):
                should_record = False
                break

        if not should_record:
            return

        # Add to recording
        self._current_recording.events.append(event)

        # Call handlers
        for handler in self._event_handlers.get(event_type, []):
            try:
                await handler(event)
            except Exception as e:
                log.warning("event_handler_error", error=str(e))

    async def record_action(
        self, action_name: str, params: dict[str, Any], result: Any | None = None
    ):
        """Record an action event"""
        await self.record_event(
            event_type=RecordingEventType.ACTION,
            data={
                "action_name": action_name,
                "params": params,
                "result": str(result)[:1000] if result else None,
            },
            tags=["action", action_name],
        )

    async def record_state_change(
        self, state_before: dict[str, Any], state_after: dict[str, Any], trigger: str | None = None
    ):
        """Record a state change"""
        await self.record_event(
            event_type=RecordingEventType.STATE_CHANGE,
            data={"trigger": trigger, "changes": self._compute_diff(state_before, state_after)},
            metadata={"state_before": state_before, "state_after": state_after},
        )

    async def record_error(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str | None = None,
        context: dict[str, Any] | None = None,
    ):
        """Record an error event"""
        await self.record_event(
            event_type=RecordingEventType.ERROR,
            data={
                "error_type": error_type,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "context": context,
            },
            tags=["error", error_type],
        )

    def _compute_diff(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        """Compute differences between two states"""
        diff = {}

        all_keys = set(before.keys()) | set(after.keys())

        for key in all_keys:
            if key not in before:
                diff[key] = {"added": after[key]}
            elif key not in after:
                diff[key] = {"removed": before[key]}
            elif before[key] != after[key]:
                diff[key] = {"changed": {"from": before[key], "to": after[key]}}

        return diff

    def register_event_handler(
        self, event_type: RecordingEventType, handler: Callable[[RecordingEvent], Awaitable]
    ):
        """Register event handler"""
        self._event_handlers[event_type].append(handler)

    def add_filter(self, filter_obj: EventFilter):
        """Add event filter"""
        self._filters.append(filter_obj)

    def get_recording(self, recording_id: str) -> Recording | None:
        """Get a recording by ID"""
        return self._recordings.get(recording_id)

    def get_current_recording(self) -> Recording | None:
        """Get current recording"""
        return self._current_recording

    def list_recordings(
        self, status: RecordingStatus | None = None, limit: int = 50
    ) -> list[Recording]:
        """List all recordings"""
        recordings = list(self._recordings.values())

        if status:
            recordings = [r for r in recordings if r.status == status]

        recordings.sort(key=lambda r: r.started_at, reverse=True)

        return recordings[:limit]

    async def delete_recording(self, recording_id: str) -> bool:
        """Delete a recording"""
        if recording_id in self._recordings:
            del self._recordings[recording_id]
            return True
        return False

    async def export_recording(self, recording_id: str, format: str = "json") -> str:
        """Export recording in specified format"""

        recording = self._recordings.get(recording_id)
        if not recording:
            raise ValueError(f"Recording {recording_id} not found")

        if format == "json":
            return json.dumps(
                {
                    "recording_id": recording.recording_id,
                    "name": recording.name,
                    "description": recording.description,
                    "started_at": recording.started_at.isoformat(),
                    "stopped_at": recording.stopped_at.isoformat()
                    if recording.stopped_at
                    else None,
                    "events": [
                        {
                            "event_id": e.event_id,
                            "event_type": e.event_type.value,
                            "timestamp": e.timestamp.isoformat(),
                            "data": e.data,
                            "metadata": e.metadata,
                            "tags": e.tags,
                        }
                        for e in recording.events
                    ],
                    "metadata": recording.metadata,
                    "statistics": {
                        "event_count": recording.event_count,
                        "error_count": recording.error_count,
                        "duration_seconds": recording.duration_seconds,
                    },
                },
                indent=2,
            )

        raise ValueError(f"Unsupported format: {format}")

    async def get_recording_statistics(self, recording_id: str) -> dict[str, Any]:
        """Get statistics for a recording"""

        recording = self._recordings.get(recording_id)
        if not recording:
            raise ValueError(f"Recording {recording_id} not found")

        events_by_type = {}
        for event_type in RecordingEventType:
            events_by_type[event_type.value] = len(recording.get_events_by_type(event_type))

        return {
            "recording_id": recording.recording_id,
            "name": recording.name,
            "status": recording.status.value,
            "duration_seconds": recording.duration_seconds,
            "event_count": recording.event_count,
            "error_count": recording.error_count,
            "events_by_type": events_by_type,
            "size_bytes": recording.get_size_bytes(),
            "hash": recording.get_hash(),
        }


class PlaybackEngine:
    """Engine for playing back recordings"""

    def __init__(self, recorder: Recorder):
        self.recorder = recorder
        self._active_playback: PlaybackState | None = None

    async def start_playback(
        self,
        recording_id: str,
        speed: float = 1.0,
        start_index: int = 0,
        loop: bool = False,
        event_callback: Callable[[RecordingEvent], Awaitable] | None = None,
    ) -> str:
        """Start playback of a recording"""

        recording = self.recorder.get_recording(recording_id)
        if not recording:
            raise ValueError(f"Recording {recording_id} not found")

        playback_id = str(uuid4())

        self._active_playback = PlaybackState(
            current_index=start_index,
            speed=speed,
            loop_count=0,
            is_paused=False,
            start_time=datetime.now(UTC),
        )

        log.info(
            "playback_started", playback_id=playback_id, recording_id=recording_id, speed=speed
        )

        # Execute playback
        await self._execute_playback(recording, playback_id, event_callback, loop)

        return playback_id

    async def _execute_playback(
        self, recording: Recording, playback_id: str, event_callback: Callable | None, loop: bool
    ):
        """Execute playback"""

        errors = []

        try:
            while self._active_playback.current_index < len(recording.events):
                if self._active_playback.is_paused:
                    await asyncio.sleep(0.1)
                    continue

                event = recording.events[self._active_playback.current_index]

                # Execute callback
                if event_callback:
                    await event_callback(event)

                # Wait based on speed
                if self._active_playback.speed > 0:
                    delay = 0.1 / self._active_playback.speed
                    await asyncio.sleep(delay)

                self._active_playback.current_index += 1

                # Handle loop
                if loop and self._active_playback.current_index >= len(recording.events):
                    self._active_playback.current_index = 0
                    self._active_playback.loop_count += 1

        except Exception as e:
            errors.append({"error": str(e)})

        finally:
            self._active_playback = None

    async def pause_playback(self):
        """Pause current playback"""
        if self._active_playback:
            self._active_playback.is_paused = True

    async def resume_playback(self):
        """Resume paused playback"""
        if self._active_playback:
            self._active_playback.is_paused = False

    async def seek_to(self, index: int):
        """Seek to specific event index"""
        if self._active_playback:
            self._active_playback.current_index = max(0, index)

    async def set_speed(self, speed: float):
        """Set playback speed"""
        if self._active_playback:
            self._active_playback.speed = max(0.1, min(10.0, speed))

    async def stop_playback(self):
        """Stop playback"""
        self._active_playback = None

    def get_playback_status(self) -> dict[str, Any] | None:
        """Get current playback status"""
        if not self._active_playback:
            return None

        return {
            "is_playing": not self._active_playback.is_paused,
            "speed": self._active_playback.speed,
            "current_index": self._active_playback.current_index,
            "loop_count": self._active_playback.loop_count,
        }


@dataclass
class RecorderConfig:
    """Configuration for recorder"""

    max_recording_size_mb: int = 100
    max_event_size_kb: int = 1000
    enable_compression: bool = True
    auto_save_interval_seconds: int = 60
    max_recordings: int = 1000
