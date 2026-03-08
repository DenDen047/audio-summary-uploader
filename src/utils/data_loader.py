import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from tqdm import tqdm


def ensure_even(value):
    return value if value % 2 == 0 else value + 1


def read_frames_from_folder(folder_path, process_length=-1, target_fps=30, max_res=-1):
    """
    Load frames from a folder containing image files.

    Args:
        folder_path: Path to the folder containing image frames
        process_length: Maximum number of frames to process (-1 means no limit)
        target_fps: Target frames per second for the output video
        max_res: Maximum resolution (-1 means no limit)

    Returns:
        frames: numpy array of frames with shape (n_frames, height, width, 3)
        fps: frames per second
    """

    # Get all image files in the folder
    image_extensions = [".jpg", ".jpeg", ".png", ".bmp", ".tiff"]
    image_files = []

    for ext in image_extensions:
        image_files.extend(list(Path(folder_path).glob(f"*{ext}")))
        image_files.extend(list(Path(folder_path).glob(f"*{ext.upper()}")))

    # Sort files by name to ensure correct sequence
    image_files = sorted(image_files)

    if not image_files:
        logger.error(f"No image files found in {folder_path}")
        raise ValueError(f"No image files found in {folder_path}")

    logger.info(f"Found {len(image_files)} image files in {folder_path}")

    # Limit number of frames if specified
    if process_length > 0 and process_length < len(image_files):
        image_files = image_files[:process_length]
        logger.info(f"Processing only first {process_length} frames")

    # Read first image to get dimensions
    sample_img = cv2.imread(str(image_files[0]))
    original_height, original_width = sample_img.shape[:2]
    height, width = original_height, original_width

    # Resize dimensions if needed
    if max_res > 0 and max(height, width) > max_res:
        scale = max_res / max(original_height, original_width)
        height = int(original_height * scale)
        width = int(original_width * scale)
        # Ensure even dimensions similar to the video function
        if "ensure_even" in globals():
            height = ensure_even(height)
            width = ensure_even(width)
        logger.info(
            f"Resizing images from {original_width}x{original_height} to {width}x{height}"
        )

    # Load all frames
    frames = []
    for img_path in tqdm(image_files, desc="Loading frames"):
        img = cv2.imread(str(img_path))
        if img is None:
            logger.warning(f"Could not read image: {img_path}")
            continue

        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)  # Convert BGR to RGB

        if max_res > 0 and max(original_height, original_width) > max_res:
            img = cv2.resize(img, (width, height))

        frames.append(img)

    frames = np.stack(frames, axis=0)
    logger.info(f"Loaded {len(frames)} frames with shape {frames.shape}")

    return frames, target_fps


def load_vda_npz(npz_path: str, key: str = "depths") -> np.ndarray:
    with np.load(npz_path) as npz:
        return npz[key]


def load_calibration_json(json_path: str) -> dict:
    """
    Load camera calibration data from a JSON file.

    Args:
        json_path: Path to the calibration JSON file

    Returns:
        dict: Dictionary containing intrinsics, extrinsics_R, and extrinsics_t as numpy arrays
    """

    try:
        logger.info(f"Loading calibration data from {json_path}")
        with open(json_path, "r") as f:
            data = json.load(f)

        # Convert data to numpy arrays
        intrinsics = np.array(data["intrinsics"], dtype=np.float32)
        extrinsics_R = np.array(data["extrinsics_R"], dtype=np.float32)
        extrinsics_t = np.array(data["extrinsics_t"], dtype=np.float32)

        K = np.array(
            [
                [intrinsics[0], 0, intrinsics[2]],
                [0, intrinsics[1], intrinsics[3]],
                [0, 0, 1],
            ]
        )
        # Reshape extrinsics_t to match dimensions with extrinsics_R
        extrinsics_t_reshaped = extrinsics_t.reshape(3, 1)
        extrinsics_Rt = np.concatenate([extrinsics_R, extrinsics_t_reshaped], axis=1)

        calibration = {
            "intrinsics_K": K,
            "extrinsics_R": extrinsics_R,
            "extrinsics_t": extrinsics_t,
            "extrinsics_Rt": extrinsics_Rt,
        }

        logger.success(f"Successfully loaded calibration data from {json_path}")
        return calibration

    except Exception as e:
        logger.error(f"Failed to load calibration file: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Video Depth Anything")
    parser.add_argument(
        "--input_npz",
        default="data/2024-09-10_08-12-30.810_measurement_calibration_part1/mono_depth/sync_rgb_depths.npz",
        type=str,
    )
    parser.add_argument("--key", type=str, default="depths")
    parser.add_argument("--calibration", type=str, help="Path to calibration JSON file")
    args = parser.parse_args()

    if args.input_npz:
        vda_npz = load_vda_npz(args.input_npz, args.key)
        logger.success(f"Loaded {vda_npz.shape} depth maps")

    if args.calibration:
        calibration = load_calibration_json(args.calibration)
        logger.success(f"Loaded calibration data: {list(calibration.keys())}")
