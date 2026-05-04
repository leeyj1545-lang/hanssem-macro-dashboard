from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable


class SourceError(RuntimeError):
    """Raised when an upstream source cannot be parsed or queried."""


class BaseSource(ABC):
    @abstractmethod
    def fetch(self) -> Iterable[dict]:
        raise NotImplementedError
