"""Tests for the WebSocket payload sanitizer."""

import pytest

from core.ipc import _flatten_serialize


class FakeArray:
    shape = (480, 640, 3)
    dtype = "uint8"


def test_primitives_passthrough():
    assert _flatten_serialize(None) is None
    assert _flatten_serialize(True) is True
    assert _flatten_serialize(42) == 42
    assert _flatten_serialize(3.14) == pytest.approx(3.14)
    assert _flatten_serialize("hi") == "hi"


def test_dict_recursed():
    result = _flatten_serialize({"a": 1, "b": {"c": "deep"}})
    assert result == {"a": 1, "b": {"c": "deep"}}


def test_list_and_tuple():
    assert _flatten_serialize([1, 2, 3]) == [1, 2, 3]
    assert _flatten_serialize((1, "x", 3.0)) == [1, "x", 3.0]


def test_bytes_replaced():
    result = _flatten_serialize(b"\xff" * 1000)
    assert isinstance(result, str)
    assert "bytes" in result
    assert "1000" in result


def test_array_like_replaced():
    result = _flatten_serialize(FakeArray())
    assert isinstance(result, str)
    assert "ndarray" in result
    assert "480" in result


def test_unknown_object_stringified():
    class WeirdObj:
        def __str__(self):
            return "weird!"

    assert _flatten_serialize(WeirdObj()) == "weird!"


def test_max_depth_guard():
    data: dict = {}
    current = data
    for _ in range(20):
        child: dict = {}
        current["child"] = child
        current = child

    assert isinstance(_flatten_serialize(data), dict)
