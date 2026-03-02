"""CV toolkit test fixtures with synthetic images."""

import cv_toolkit  # noqa: F401 — triggers tool registration

import numpy as np
import pytest


@pytest.fixture
def solid_red() -> np.ndarray:
    """100x100 solid red (BGR)."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:, :] = (0, 0, 255)
    return img


@pytest.fixture
def solid_blue() -> np.ndarray:
    """100x100 solid blue (BGR)."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:, :] = (255, 0, 0)
    return img


@pytest.fixture
def solid_green() -> np.ndarray:
    """100x100 solid green (BGR)."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:, :] = (0, 255, 0)
    return img


@pytest.fixture
def solid_black() -> np.ndarray:
    """100x100 solid black."""
    return np.zeros((100, 100, 3), dtype=np.uint8)


@pytest.fixture
def solid_white() -> np.ndarray:
    """100x100 solid white."""
    return np.full((100, 100, 3), 255, dtype=np.uint8)


@pytest.fixture
def gradient() -> np.ndarray:
    """100x100 horizontal gradient (black to white)."""
    grad = np.tile(np.linspace(0, 255, 100, dtype=np.uint8), (100, 1))
    return np.stack([grad, grad, grad], axis=-1)


@pytest.fixture
def checkerboard() -> np.ndarray:
    """100x100 black/white checkerboard (10x10 squares)."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    for i in range(10):
        for j in range(10):
            if (i + j) % 2 == 0:
                img[i * 10 : (i + 1) * 10, j * 10 : (j + 1) * 10] = 255
    return img


@pytest.fixture
def half_red_half_blue() -> np.ndarray:
    """100x100 left-half red, right-half blue (BGR)."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:, :50] = (0, 0, 255)
    img[:, 50:] = (255, 0, 0)
    return img


@pytest.fixture
def high_res_gradient() -> np.ndarray:
    """1080x1920 horizontal gradient for scale tests."""
    grad = np.tile(np.linspace(0, 255, 1920, dtype=np.uint8), (1080, 1))
    return np.stack([grad, grad, grad], axis=-1)


@pytest.fixture
def low_res_gradient() -> np.ndarray:
    """54x96 horizontal gradient for scale tests."""
    grad = np.tile(np.linspace(0, 255, 96, dtype=np.uint8), (54, 1))
    return np.stack([grad, grad, grad], axis=-1)


@pytest.fixture
def random_image() -> np.ndarray:
    """100x100 deterministic random noise."""
    rng = np.random.RandomState(42)
    return rng.randint(0, 256, (100, 100, 3), dtype=np.uint8)
