from __future__ import annotations

from models import Operation


def add(op: Operation) -> float:
    return op.a + op.b


def sub(op: Operation) -> float:
    return op.a - op.b


def mul(op: Operation) -> float:
    return op.a * op.b


def div(op: Operation) -> float:
    if op.b == 0:
        raise ValueError("division by zero")
    return op.a / op.b
