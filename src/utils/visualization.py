import cv2
import numpy as np
from loguru import logger


def make_color_depth_image(
    depth_image: np.ndarray,  # shape: (H, W)
    min_depth: float = None,
    max_depth: float = None,
):
    # Check for NaN/Inf values and replace them
    valid_mask = np.isfinite(depth_image)
    if not np.any(valid_mask):
        logger.warning("Depth image contains only NaN/Inf values")
        return np.zeros((depth_image.shape[0], depth_image.shape[1], 3), dtype=np.uint8)

    # Filter out non-finite values for min/max calculation
    filtered_depth = depth_image[valid_mask]
    if min_depth is None:
        min_depth = np.min(filtered_depth)
    if max_depth is None:
        max_depth = np.max(filtered_depth)

    # Handle case where min equals max (avoid division by zero)
    if np.isclose(min_depth, max_depth):
        logger.debug("Uniform depth image detected (min == max)")
        normalized_depth = np.zeros_like(depth_image)
    else:
        # Create a copy to avoid modifying the original array
        normalized_depth = np.zeros_like(depth_image)
        normalized_depth[valid_mask] = (
            (depth_image[valid_mask] - min_depth) / (max_depth - min_depth) * 255
        )

    # Ensure values are properly clipped before casting
    normalized_depth = np.clip(normalized_depth, 0, 255).astype(np.uint8)
    colored_depth = cv2.applyColorMap(normalized_depth, cv2.COLORMAP_JET)
    return colored_depth
