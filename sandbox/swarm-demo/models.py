"""Data models for the swarm demo."""

from dataclasses import dataclass


@dataclass
class Operation:
    op: str
    a: float
    b: float
