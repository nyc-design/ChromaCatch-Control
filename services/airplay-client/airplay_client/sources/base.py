"""Frame source abstractions for the client."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class FrameSource(ABC):
    """Common interface for any frame producer."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable source type."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether source is actively producing frames."""

    @abstractmethod
    def start(self) -> None:
        """Start frame source."""

    @abstractmethod
    def stop(self) -> None:
        """Stop frame source."""

    @abstractmethod
    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        """Get most recent frame, blocking up to timeout seconds."""

