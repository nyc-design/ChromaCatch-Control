"""Tests for contour_detect tool."""

import cv2
import numpy as np

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestContourDetect:
    def test_white_rect_on_black(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (255, 255, 255), -1)
        ti = ToolInput(tool="contour_detect", threshold=0.0)
        result = run_tool(img, ti)
        assert result.details["num_contours"] >= 1

    def test_solid_color_no_contours(self, solid_red):
        ti = ToolInput(tool="contour_detect", threshold=0.0)
        result = run_tool(solid_red, ti)
        assert result.details["num_contours"] == 0

    def test_with_reference_same_shape(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (255, 255, 255), -1)
        ti = ToolInput(tool="contour_detect", threshold=0.5)
        result = run_tool(img, ti, reference=img)
        assert result.score > 0.5

    def test_with_reference_different_shape(self):
        rect = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(rect, (50, 50), (150, 150), (255, 255, 255), -1)
        circle = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.circle(circle, (100, 100), 50, (255, 255, 255), -1)
        ti = ToolInput(tool="contour_detect", threshold=0.99)
        result = run_tool(rect, ti, reference=circle)
        # Different shapes should not be a perfect match
        assert result.score < 0.99

    def test_details_contain_areas(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (60, 60), (255, 255, 255), -1)
        cv2.rectangle(img, (100, 100), (180, 180), (255, 255, 255), -1)
        ti = ToolInput(tool="contour_detect", threshold=0.0)
        result = run_tool(img, ti)
        assert "contour_areas" in result.details
