"""
AgentMax Observability System

Complete observability integration including:
- Error tracking (Sentry)
- Performance monitoring
- Custom metrics
- Alerting
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog

log = structlog.get_logger(__name__)


class EventLevel(str, Enum):
    """Log/trace levels"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SpanStatus(str, Enum):
    """Span/trace status"""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class ErrorContext:
    """Context for error tracking"""

    error_type: str
    message: str

    user_id: str | None = None
    session_id: str | None = None

    request_url: str | None = None
    request_method: str | None = None
    request_headers: dict[str, str] | None = None

    stack_trace: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    tags: dict[str, str] = field(default_factory=dict)

    release: str | None = None
    environment: str | None = None


@dataclass
class Span:
    """Performance span for tracing"""

    operation: str
    description: str

    start_time: datetime
    end_time: datetime | None = None

    status: SpanStatus = SpanStatus.OK

    parent_span_id: str | None = None
    span_id: str = field(default_factory=lambda: str(uuid4())[:16])

    tags: dict[str, Any] = field(default_factory=dict)

    children: list[Span] = field(default_factory=list)


@dataclass
class MetricPoint:
    """Metric data point"""

    name: str
    value: float

    metric_type: str  # "counter", "gauge", "histogram"

    timestamp: datetime

    tags: dict[str, str] = field(default_factory=dict)

    unit: str | None = None


class SentryIntegrator:
    """
    Sentry integration for AgentMax.
    Tracks errors, performance, and user feedback.
    """

    def __init__(
        self,
        dsn: str | None = None,
        environment: str = "development",
        release: str | None = None,
        sample_rate: float = 1.0,
        traces_sample_rate: float = 0.1,
    ):
        self.dsn = dsn
        self.environment = environment
        self.release = release
        self.sample_rate = sample_rate
        self.traces_sample_rate = traces_sample_rate

        self._initialized = bool(dsn)

        self._current_spans: dict[str, Span] = {}
        self._metrics_buffer: list[MetricPoint] = []

        self._before_send: list[Callable] = []
        self._after_capture: list[Callable] = []

    def init(self):
        if not self.dsn:
            log.warning("sentry_dsn_not_configured")
            return

        self._initialized = True

        log.info("sentry_initialized", environment=self.environment, release=self.release)

    def capture_exception(self, error: Exception, context: ErrorContext | None = None):
        """Capture an exception with context"""

        if not self._initialized:
            return

        error_data = {
            "level": "error",
            "message": str(error),
            "type": type(error).__name__,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if context:
            error_data["user"] = {"id": context.user_id, "session": context.session_id}

            error_data["request"] = {
                "url": context.request_url,
                "method": context.request_method,
                "headers": context.request_headers,
            }

            error_data["tags"] = context.tags
            error_data["extra"] = context.extra
            error_data["release"] = context.release or self.release
            error_data["environment"] = context.environment or self.environment

        if not context or not context.stack_trace:
            error_data["stacktrace"] = self._format_stack_trace(error)
        else:
            error_data["stacktrace"] = context.stack_trace

        for handler in self._before_send:
            error_data = handler(error_data) or error_data

        log.error(
            "exception_captured", error_type=error_data["type"], message=error_data["message"]
        )

        for handler in self._after_capture:
            handler(error_data)

    def capture_message(
        self,
        message: str,
        level: EventLevel = EventLevel.INFO,
        context: ErrorContext | None = None,
    ):
        """Capture a message (non-error)"""

        if not self._initialized:
            return

        message_data = {
            "level": level.value,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if context:
            message_data["user"] = {"id": context.user_id}
            message_data["tags"] = context.tags
            message_data["extra"] = context.extra

        log.log(level.value, "message_captured", **message_data)

    def start_span(
        self, operation: str, description: str, parent_span_id: str | None = None
    ) -> Span:
        """Start a new performance span"""

        span = Span(
            operation=operation,
            description=description,
            start_time=datetime.now(UTC),
            parent_span_id=parent_span_id,
        )

        self._current_spans[span.span_id] = span

        return span

    def end_span(
        self, span: Span, status: SpanStatus = SpanStatus.OK, tags: dict[str, Any] | None = None
    ):
        """End a performance span"""

        span.end_time = datetime.now(UTC)
        span.status = status

        if tags:
            span.tags.update(tags)

        duration_ms = (span.end_time - span.start_time).total_seconds() * 1000

        span.tags["duration_ms"] = round(duration_ms, 2)

        log.info(
            "span_completed",
            operation=span.operation,
            description=span.description,
            duration_ms=span.tags["duration_ms"],
            status=status.value,
        )

        if span.span_id in self._current_spans:
            del self._current_spans[span.span_id]

    def record_metric(
        self,
        name: str,
        value: float,
        metric_type: str = "counter",
        tags: dict[str, str] | None = None,
        unit: str | None = None,
    ):
        """Record a metric"""

        metric = MetricPoint(
            name=name,
            value=value,
            metric_type=metric_type,
            timestamp=datetime.now(UTC),
            tags=tags or {},
            unit=unit,
        )

        self._metrics_buffer.append(metric)

        # Flush if buffer is large
        if len(self._metrics_buffer) >= 100:
            self._flush_metrics()

    def _flush_metrics(self):
        if not self._metrics_buffer:
            return

        metrics_by_name: dict[str, list[MetricPoint]] = {}

        for metric in self._metrics_buffer:
            if metric.name not in metrics_by_name:
                metrics_by_name[metric.name] = []
            metrics_by_name[metric.name].append(metric)

        for name, points in metrics_by_name.items():
            values = [p.value for p in points]

            log.info(
                "metrics_recorded",
                name=name,
                count=len(values),
                sum=sum(values),
                avg=sum(values) / len(values) if values else 0,
                min=min(values) if values else 0,
                max=max(values) if values else 0,
            )

        self._metrics_buffer.clear()

    def add_breadcrumb(
        self,
        category: str,
        message: str,
        level: EventLevel = EventLevel.INFO,
        data: dict[str, Any] | None = None,
    ):
        """Add a breadcrumb (user action trail)"""

        breadcrumb = {
            "category": category,
            "message": message,
            "level": level.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data or {},
        }

        log.debug("breadcrumb_added", **breadcrumb)

    def set_user(
        self,
        user_id: str,
        email: str | None = None,
        username: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        """Set current user context"""

        user_data = {
            "id": user_id,
            "email": email,
            "username": username,
            **(extra or {}),
        }

        log.info("sentry_user_set", **user_data)

    def clear_user(self):
        """Clear user context"""

        log.info("sentry_user_cleared")

    def set_tag(self, key: str, value: str):
        """Set a tag for all future events"""

        log.debug("sentry_tag_set", key=key, value=value)

    def set_extra(self, key: str, value: Any):
        """Set extra data for all future events"""

        log.debug("sentry_extra_set", key=key)

    def before_send(self, handler: Callable):
        """Add a before-send handler"""
        self._before_send.append(handler)

    def after_capture(self, handler: Callable):
        """Add an after-capture handler"""
        self._after_capture.append(handler)

    def _format_stack_trace(self, error: Exception) -> str:
        """Format exception stack trace"""

        return "".join(traceback.format_exception(type(error), error, error.__traceback__))

    def _handle_before_send(self, event: dict) -> dict | None:
        """Handle event before sending to Sentry"""

        # Can modify or drop events here
        return event


class PerformanceMonitor:
    """Monitor application performance"""

    def __init__(self, sentry: SentryIntegrator):
        self.sentry = sentry

        self._request_count = 0
        self._error_count = 0
        self._response_times: list[float] = []

        self._start_time = datetime.now(UTC)

    async def track_request(self, method: str, path: str, duration_ms: float, status_code: int):
        """Track an HTTP request"""

        self._request_count += 1
        self._response_times.append(duration_ms)

        if status_code >= 400:
            self._error_count += 1

        # Record metrics
        self.sentry.record_metric(
            "http.request.duration",
            duration_ms,
            "histogram",
            tags={"method": method, "path": path, "status": str(status_code)},
            unit="ms",
        )

        self.sentry.record_metric(
            "http.request.count", 1, "counter", tags={"method": method, "path": path}
        )

        # Track slow requests
        if duration_ms > 1000:
            self.sentry.add_breadcrumb(
                "performance",
                f"Slow request: {method} {path} took {duration_ms}ms",
                EventLevel.WARNING,
                {"duration_ms": duration_ms},
            )

    def get_stats(self) -> dict[str, Any]:
        """Get current performance statistics"""

        uptime = (datetime.now(UTC) - self._start_time).total_seconds()

        response_times = self._response_times[-100:]  # Last 100

        return {
            "uptime_seconds": uptime,
            "total_requests": self._request_count,
            "total_errors": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
            "response_time_avg": sum(response_times) / len(response_times) if response_times else 0,
            "response_time_p50": sorted(response_times)[len(response_times) // 2]
            if response_times
            else 0,
            "response_time_p95": sorted(response_times)[int(len(response_times) * 0.95)]
            if response_times
            else 0,
            "response_time_p99": sorted(response_times)[int(len(response_times) * 0.99)]
            if response_times
            else 0,
        }


class AlertManager:
    """Manage alerts based on metrics and errors"""

    def __init__(self, sentry: SentryIntegrator):
        self.sentry = sentry

        self._alert_rules: dict[str, dict[str, Any]] = {}
        self._alert_history: list[dict[str, Any]] = []

    def add_rule(
        self,
        name: str,
        condition: str,  # e.g., "error_rate > 0.05"
        severity: str = "warning",
        cooldown_seconds: int = 300,
    ):
        """Add an alert rule"""

        self._alert_rules[name] = {
            "condition": condition,
            "severity": severity,
            "cooldown_seconds": cooldown_seconds,
            "last_triggered": None,
        }

    async def evaluate_rules(self, stats: dict[str, Any]):
        """Evaluate alert rules against current stats"""

        for name, rule in self._alert_rules.items():
            # Parse condition (simplified)
            condition = rule["condition"]

            if "error_rate" in condition:
                threshold = float(condition.split(">")[1])
                if stats.get("error_rate", 0) > threshold:
                    await self._trigger_alert(name, rule, stats)

            elif "response_time" in condition:
                threshold = float(condition.split(">")[1])
                if stats.get("response_time_p95", 0) > threshold:
                    await self._trigger_alert(name, rule, stats)

    async def _trigger_alert(self, name: str, rule: dict[str, Any], context: dict[str, Any]):
        """Trigger an alert"""

        cooldown = rule["cooldown_seconds"]
        last_triggered = rule.get("last_triggered")

        if last_triggered:
            elapsed = (datetime.now(UTC) - last_triggered).total_seconds()
            if elapsed < cooldown:
                return

        alert = {
            "name": name,
            "severity": rule["severity"],
            "condition": rule["condition"],
            "context": context,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        self._alert_history.append(alert)
        rule["last_triggered"] = datetime.now(UTC)

        # Capture alert in Sentry
        self.sentry.capture_message(
            f"Alert: {name}",
            EventLevel.WARNING if rule["severity"] == "warning" else EventLevel.CRITICAL,
        )

        log.warning("alert_triggered", **alert)


class ObservabilityService:
    """Main observability service combining all components"""

    def __init__(
        self,
        dsn: str | None = None,
        environment: str = "development",
        release: str | None = None,
    ):
        self.sentry = SentryIntegrator(dsn, environment, release)
        self.monitor = PerformanceMonitor(self.sentry)
        self.alerts = AlertManager(self.sentry)

    def init(self):
        """Initialize the observability service"""
        self.sentry.init()

        # Set up default alert rules
        self.alerts.add_rule(
            "high_error_rate", "error_rate > 0.05", "warning", cooldown_seconds=300
        )

        self.alerts.add_rule(
            "slow_response", "response_time_p95 > 1000", "warning", cooldown_seconds=60
        )

    def capture_error(
        self,
        error: Exception,
        user_id: str | None = None,
        request: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ):
        """Capture an error with full context"""

        context = ErrorContext(
            error_type=type(error).__name__,
            message=str(error),
            user_id=user_id,
            request_url=request.get("url") if request else None,
            request_method=request.get("method") if request else None,
            request_headers=request.get("headers") if request else None,
            extra=extra or {},
        )

        self.sentry.capture_exception(error, context)

    def capture_message(
        self, message: str, level: EventLevel = EventLevel.INFO, user_id: str | None = None
    ):
        """Capture a message"""

        context = ErrorContext(error_type="message", message=message, user_id=user_id)

        self.sentry.capture_message(message, level, context)

    async def track_request(self, method: str, path: str, duration_ms: float, status_code: int):
        """Track an HTTP request"""

        await self.monitor.track_request(method, path, duration_ms, status_code)

        # Periodically evaluate alerts
        if self.monitor._request_count % 100 == 0:
            await self.alerts.evaluate_rules(self.monitor.get_stats())

    def start_trace(
        self, operation: str, description: str, parent_span_id: str | None = None
    ) -> Span:
        """Start a performance trace"""
        return self.sentry.start_span(operation, description, parent_span_id)

    def end_trace(self, span: Span, status: SpanStatus = SpanStatus.OK):
        """End a performance trace"""
        self.sentry.end_span(span, status)

    def record_metric(
        self,
        name: str,
        value: float,
        metric_type: str = "counter",
        tags: dict[str, str] | None = None,
        unit: str | None = None,
    ):
        """Record a metric"""
        self.sentry.record_metric(name, value, metric_type, tags, unit)

    def add_breadcrumb(self, category: str, message: str, data: dict[str, Any] | None = None):
        """Add a user action breadcrumb"""
        self.sentry.add_breadcrumb(category, message, EventLevel.INFO, data)

    def set_user(self, user_id: str, email: str | None = None):
        """Set current user"""
        self.sentry.set_user(user_id, email)

    def clear_user(self):
        """Clear user context"""
        self.sentry.clear_user()

    def get_stats(self) -> dict[str, Any]:
        """Get observability statistics"""
        return {
            "performance": self.monitor.get_stats(),
            "alerts": len(self.alerts._alert_history),
            "errors": self.monitor._error_count,
        }


# Global observability instance
_observability: ObservabilityService | None = None


def get_observability() -> ObservabilityService:
    """Get global observability instance"""
    global _observability
    if _observability is None:
        _observability = ObservabilityService()
    return _observability


# FastAPI integration
class ObservabilityMiddleware:
    """Middleware to track requests and errors"""

    def __init__(self, app, observability: ObservabilityService):
        self.app = app
        self.obs = observability

    async def __call__(self, scope, receive, send):

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Start timing
        import time

        start_time = time.perf_counter()

        # Get request details
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")

        # Extract user from scope if available
        user_id = None
        headers = dict(scope.get("headers", []))
        if b"x-user-id" in headers:
            user_id = headers[b"x-user-id"].decode()

        # Set user context
        if user_id:
            self.obs.set_user(user_id)

        # Add breadcrumb
        self.obs.add_breadcrumb("http", f"{method} {path}", data={"method": method, "path": path})

        # Create status code holder
        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message["status"]

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            # Capture exception
            self.obs.capture_error(e, user_id=user_id, request={"method": method, "path": path})
            raise
        finally:
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Track request
            await self.obs.track_request(method, path, duration_ms, status_code)

            # Clear user
            if user_id:
                self.obs.clear_user()


# Error handler integration
class ObservabilityErrorHandler:
    """FastAPI error handler with observability"""

    def __init__(self, observability: ObservabilityService):
        self.obs = observability

    async def handle_exception(self, request, exc: Exception):
        """Handle uncaught exceptions"""

        self.obs.capture_error(
            exc,
            user_id=getattr(request.state, "user_id", None),
            request={
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
            },
        )

        # Re-raise for FastAPI to handle
        raise exc
