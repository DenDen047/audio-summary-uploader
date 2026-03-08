import sys

import torch  # isort:skip

import matplotlib.cm as cm
import numpy as np
import rerun as rr
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO")


def lidar_to_camera_coordinates(
    point_cloud: torch.Tensor,
    intrinsic_mat: torch.Tensor,
    extrinsic_mat: torch.Tensor,
    without_batch_dim: bool = False,
):
    """
    Project 3D LiDAR points to 3D camera coordinates using camera parameters.

    Parameters
    ----------
    point_cloud : torch.Tensor
        LiDAR point cloud with shape (B, N, 3) or (B, N, 4) where B is batch size,
        N is number of points, and the last dimension contains XYZ coordinates
        (and optionally intensity).
    intrinsic_mat : torch.Tensor
        Camera intrinsic matrix with shape (3, 3) containing focal lengths and principal point.
    extrinsic_mat : torch.Tensor
        Camera extrinsic matrix with shape (3, 4) representing the transformation
        from LiDAR coordinates to camera coordinates.
    without_batch_dim : bool, optional
        If True, remove the batch dimension from the output.

    Returns
    -------
    torch.Tensor
        Projected points in image coordinates with shape (B, N, 3), where the last dimension
        contains (x, y, z) with x, y being pixel coordinates and z being depth.
    """
    if without_batch_dim:
        if len(point_cloud.shape) == 2:
            point_cloud = point_cloud.unsqueeze(0)
        else:
            logger.error(f"Invalid point cloud shape: {point_cloud.shape}")
            raise ValueError(f"Invalid point cloud shape: {point_cloud.shape}")

    projection_mat = intrinsic_mat @ extrinsic_mat
    tmp = point_cloud.clone()
    if tmp.shape[-1] == 4:  # having the LiDAR intensity values
        tmp[:, :, 3] = 1
    elif tmp.shape[-1] == 3:
        tmp = torch.cat([tmp, torch.ones_like(tmp[:, :, :1])], dim=-1)
    else:
        raise ValueError(f"Invalid point cloud shape: {tmp.shape}")
    projected_points = projection_mat[None, :, :] @ tmp.transpose(1, 2)

    result = projected_points[:, :3, :].transpose(1, 2)

    if without_batch_dim:
        result = result.squeeze(0)

    return result


def camera_to_image_coordinates(
    point_cloud: torch.Tensor,  # [B,N,3] or [N,3]
    without_batch_dim: bool = False,
):
    """
    Project 3D camera points to 2D image coordinates.

    Parameters
    ----------
    point_cloud : torch.Tensor
        Camera points with shape (B, N, 3) where B is batch size,
        N is number of points, and the last dimension contains XYZ coordinates
        in camera space. If without_batch_dim is True, shape should be (N, 3).
    without_batch_dim : bool, optional
        If True, assumes input has no batch dimension and returns output without batch dimension.

    Returns
    -------
    torch.Tensor
        Projected points in image coordinates with shape (B, N, 3) or (N, 3) if without_batch_dim is True,
        where the last dimension contains (u, v, d) with u, v being pixel coordinates and d being depth in camera space.
    """
    if without_batch_dim:
        if len(point_cloud.shape) == 2:
            point_cloud = point_cloud.unsqueeze(0)
        else:
            logger.error(f"Invalid point cloud shape: {point_cloud.shape}")
            raise ValueError(f"Invalid point cloud shape: {point_cloud.shape}")

    D = point_cloud[:, :, 2]
    U = point_cloud[:, :, 0] / D
    V = point_cloud[:, :, 1] / D
    result = torch.stack([U, V, D], dim=-1)

    if without_batch_dim:
        result = result.squeeze(0)

    return result


def lidar_to_image_coordinates(
    point_cloud: torch.Tensor,
    intrinsic_mat: torch.Tensor,
    extrinsic_mat: torch.Tensor,
    depth_type: str = "planar",
    without_batch_dim: bool = False,
):
    """
    3D LiDAR点群を2D画像座標 (u, v) と深度 (d) に投影する。
    """
    if without_batch_dim:
        if len(point_cloud.shape) != 2:
            raise ValueError(
                f"Invalid point cloud shape for without_batch_dim=True: {point_cloud.shape}"
            )
        point_cloud = point_cloud.unsqueeze(0)

    camera_points_3d = lidar_to_camera_3d(point_cloud, extrinsic_mat)
    image_coords = camera_3d_to_image_coordinates(
        camera_points_3d, intrinsic_mat, depth_type
    )

    if without_batch_dim:
        image_coords = image_coords.squeeze(0)

    return image_coords


def filter_points_in_image_frame(
    points_2d: torch.Tensor,  # [B,N,3] or [N,3]
    image_width: int,
    image_height: int,
    return_mask: bool = False,
    without_batch_dim: bool = False,
) -> list[torch.Tensor] | tuple[list[torch.Tensor], list[torch.Tensor]] | torch.Tensor:
    """
    Filter 3D points to keep only those that project within the image frame.

    Parameters
    ----------
    points_2d : torch.Tensor
        Points in image coordinates with shape (B, N, 3) or (N, 3) if without_batch_dim is True,
        where the last dimension contains (u, v, d) with u, v being pixel coordinates and d being depth.
    image_width : int
        Width of the image in pixels.
    image_height : int
        Height of the image in pixels.
    return_mask : bool, optional
        If True, also return the boolean masks used for filtering.
    without_batch_dim : bool, optional
        If True, assumes input has no batch dimension and returns output without batch dimension.

    Returns
    -------
    list[torch.Tensor] | tuple[list[torch.Tensor], list[torch.Tensor]] | torch.Tensor
        If without_batch_dim is False:
            - If return_mask is False: List of filtered points tensors
            - If return_mask is True: Tuple of (filtered points list, masks list)
        If without_batch_dim is True:
            - If return_mask is False: Filtered points tensor with shape (M, 3)
            - If return_mask is True: Tuple of (filtered points tensor, mask tensor)
    """
    # Handle case without batch dimension
    if without_batch_dim:
        if len(points_2d.shape) == 2:
            points_2d = points_2d.unsqueeze(0)
        else:
            logger.error(
                f"Invalid points_2d shape for without_batch_dim=True: {points_2d.shape}"
            )
            raise ValueError(
                f"Invalid points_2d shape for without_batch_dim=True: {points_2d.shape}"
            )

    batch_size = points_2d.shape[0]
    filtered_points = []
    masks = []

    for b in range(batch_size):
        # Extract u, v coordinates
        u = points_2d[b, :, 0]
        v = points_2d[b, :, 1]

        # Create mask for points within image boundaries
        mask = (u >= 0) & (u < image_width) & (v >= 0) & (v < image_height)

        # Filter points using the mask
        valid_points = points_2d[b, mask, :]
        filtered_points.append(valid_points)
        masks.append(mask)

        if valid_points.shape[0] == 0:
            logger.warning(f"No points found in the image frame for batch {b}")

    # Return results based on flags
    if without_batch_dim:
        if return_mask:
            return filtered_points[0], masks[0]
        else:
            return filtered_points[0]
    else:
        if return_mask:
            return filtered_points, masks
        else:
            return filtered_points


def create_batched_point_cloud(
    point_cloud_list: list[torch.Tensor],
    num_points: int,
    random_seed: int = None,
    without_batch_dim: bool = False,
) -> torch.Tensor:
    """
    Create a batched point cloud tensor from a list of point clouds with varying numbers of points.
    Points are randomly sampled to ensure each batch element has the same number of points.

    Parameters
    ----------
    point_cloud_list : list[torch.Tensor] or torch.Tensor
        List of point cloud tensors, each with shape (N_i, 3) where N_i is the number of 3D points
        (which can vary between tensors). If without_batch_dim is True, this can be a single tensor
        with shape (N, 3).
    num_points : int
        Number of points to sample for each point cloud in the batch.
    random_seed : int, optional
        Random seed for reproducibility.
    without_batch_dim : bool, default=False
        If True, treats the input as a single point cloud tensor with shape (N, 3) instead of a list
        of tensors, and returns a tensor with shape (num_points, 3) instead of (1, num_points, 3).

    Returns
    -------
    torch.Tensor
        If without_batch_dim is False:
            Batched point cloud tensor with shape (B, num_points, 3) where B is the batch size.
        If without_batch_dim is True:
            Point cloud tensor with shape (num_points, 3).
    """
    if random_seed is not None:
        torch.manual_seed(random_seed)

    # Handle case without batch dimension
    if without_batch_dim:
        if (
            isinstance(point_cloud_list, torch.Tensor)
            and len(point_cloud_list.shape) == 2
        ):
            pc = point_cloud_list
            device = pc.device

            if pc.numel() == 0:
                logger.warning("Empty point cloud provided")
                return torch.zeros((num_points, 3), device=device)

            n_points = pc.shape[0]

            if n_points <= num_points:
                logger.debug(f"Sampling with replacement ({n_points} → {num_points})")
                indices = torch.randint(0, n_points, (num_points,), device=device)
                return pc[indices]
            else:
                # If we have more points than required, sample randomly without replacement
                logger.debug(
                    f"Sampling without replacement ({n_points} → {num_points})"
                )
                indices = torch.randperm(n_points, device=device)[:num_points]
                return pc[indices]
        else:
            logger.error(
                f"Invalid input for without_batch_dim=True: {type(point_cloud_list)}"
            )
            raise ValueError(
                "Invalid input for without_batch_dim=True: expected tensor with shape (N, 3)"
            )

    batch_size = len(point_cloud_list)
    if batch_size == 0:
        raise ValueError("Empty point cloud list provided")

    # Initialize the output tensor
    device = point_cloud_list[0].device
    batched_pc = torch.zeros((batch_size, num_points, 3), device=device)

    for b, pc in enumerate(point_cloud_list):
        if pc.numel() == 0:
            logger.warning(f"Empty point cloud at batch index {b}")
            continue

        n_points = pc.shape[0]

        if n_points <= num_points:
            # If we have fewer points than required, duplicate points randomly
            logger.debug(
                f"Batch {b}: Sampling with replacement ({n_points} → {num_points})"
            )
            indices = torch.randint(0, n_points, (num_points,), device=device)
            batched_pc[b] = pc[indices]
        else:
            # If we have more points than required, sample randomly without replacement
            logger.debug(
                f"Batch {b}: Sampling without replacement ({n_points} → {num_points})"
            )
            indices = torch.randperm(n_points, device=device)[:num_points]
            batched_pc[b] = pc[indices]

    return batched_pc


def depth_image_to_point_cloud(
    depth_image: torch.Tensor,  # [1, H, W]
    rgb_image: torch.Tensor,  # [3, H, W]
    intrinsic_mat: torch.Tensor,  # [3, 3]
    extrinsic_mat: torch.Tensor = None,  # [3, 4]
) -> torch.Tensor:
    """
    Convert depth image and RGB image to a colored 3D point cloud.

    Parameters
    ----------
    depth_image : torch.Tensor
        Depth image with shape [1, H, W] where the values represent depth in meters.
    rgb_image : torch.Tensor
        RGB image with shape [3, H, W] with values in range [0, 1].
    intrinsic_mat : torch.Tensor
        Camera intrinsic matrix with shape [3, 3] containing focal lengths and principal point.
    extrinsic_mat : torch.Tensor, optional
        Camera extrinsic matrix with shape [3, 4] representing the transformation
        from world coordinates to camera coordinates. If None, points remain in camera coordinates.

    Returns
    -------
    torch.Tensor
        Colored point cloud with shape [N, 6], where N is the number of valid depth points,
        and each point contains [x, y, z, r, g, b] values.
    """
    if (
        not torch.is_tensor(depth_image)
        or depth_image.dim() != 3
        or depth_image.shape[0] != 1
    ):
        err_msg = f"depth_image must be a tensor with shape [1, H, W], got {depth_image.shape}"
        logger.error(err_msg)
        raise ValueError(err_msg)

    if (
        not torch.is_tensor(rgb_image)
        or rgb_image.dim() != 3
        or rgb_image.shape[0] != 3
    ):
        err_msg = (
            f"rgb_image must be a tensor with shape [3, H, W], got {rgb_image.shape}"
        )
        logger.error(err_msg)
        raise ValueError(err_msg)

    device = depth_image.device

    # Extract dimensions
    _, height, width = depth_image.shape

    # Check that RGB and depth images have matching dimensions
    if rgb_image.shape[1] != height or rgb_image.shape[2] != width:
        err_msg = f"RGB image shape {rgb_image.shape} doesn't match depth image shape {depth_image.shape}"
        logger.error(err_msg)
        raise ValueError(err_msg)

    # Create pixel coordinate grid
    v_indices, u_indices = torch.meshgrid(
        torch.arange(height, device=device),
        torch.arange(width, device=device),
        indexing="ij",
    )
    u_indices = u_indices.flatten()
    v_indices = v_indices.flatten()

    # Get corresponding depth values
    z = depth_image[0, v_indices, u_indices]

    # Filter out invalid depth points (zeros or NaNs)
    valid_mask = (z > 0) & torch.isfinite(z)
    u_indices = u_indices[valid_mask]
    v_indices = v_indices[valid_mask]
    z = z[valid_mask]

    # Number of valid points
    num_points = z.shape[0]

    if num_points == 0:
        logger.warning("No valid depth points found in the image")
        return torch.zeros((0, 6), device=device)

    # Get camera parameters
    fx = intrinsic_mat[0, 0]
    fy = intrinsic_mat[1, 1]
    cx = intrinsic_mat[0, 2]
    cy = intrinsic_mat[1, 2]

    # Back-project to camera space
    x = (u_indices - cx) * z / fx
    y = (v_indices - cy) * z / fy

    # Create points in camera coordinates
    points_camera = torch.stack([x, y, z], dim=1)  # [N, 3]

    # Use points in camera coordinates if extrinsic matrix is not provided
    if extrinsic_mat is None:
        # Get RGB colors for each point
        rgb_colors = rgb_image[:, v_indices, u_indices].t()  # [N, 3]

        # Combine position and color
        colored_point_cloud = torch.cat([points_camera, rgb_colors], dim=1)  # [N, 6]

        return colored_point_cloud

    # Transform to world coordinates if extrinsic matrix is provided
    # Create homogeneous coordinates
    ones = torch.ones(num_points, 1, device=device)
    points_camera_homogeneous = torch.cat([points_camera, ones], dim=1)  # [N, 4]

    # Compute the inverse of the extrinsic matrix
    # The extrinsic matrix transforms from world to camera: P_camera = E * P_world
    # So we need the inverse to go from camera to world: P_world = E^-1 * P_camera
    R = extrinsic_mat[:3, :3]
    t = extrinsic_mat[:3, 3].unsqueeze(1)

    # Calculate inverse (R^T, -R^T * t)
    R_inv = R.transpose(0, 1)
    t_inv = -R_inv @ t

    # Construct inverse extrinsic matrix
    extrinsic_inv = torch.zeros(4, 4, device=device)
    extrinsic_inv[:3, :3] = R_inv
    extrinsic_inv[:3, 3] = t_inv.squeeze()
    extrinsic_inv[3, 3] = 1.0

    # Transform points
    points_world_homogeneous = extrinsic_inv @ points_camera_homogeneous.t()  # [4, N]
    points_world = points_world_homogeneous[:3].t()  # [N, 3]

    # Get RGB colors for each point
    rgb_colors = rgb_image[:, v_indices, u_indices].t()  # [N, 3]

    # Combine position and color
    colored_point_cloud = torch.cat([points_world, rgb_colors], dim=1)  # [N, 6]

    return colored_point_cloud


def lidar_to_camera_3d(
    point_cloud: torch.Tensor,
    extrinsic_mat: torch.Tensor,
) -> torch.Tensor:
    """
    LiDAR座標系の3D点群を、カメラ座標系の3D点群に変換する。
    """
    if point_cloud.shape[-1] == 3:
        points_homo = torch.cat(
            [point_cloud, torch.ones_like(point_cloud[..., :1])], dim=-1
        )
    elif point_cloud.shape[-1] == 4:
        points_homo = point_cloud.clone()
        points_homo[..., 3] = 1
    else:
        raise ValueError(f"Invalid point cloud shape: {point_cloud.shape}")

    camera_points_3d = extrinsic_mat[None, :, :] @ points_homo.transpose(1, 2)
    return camera_points_3d.transpose(1, 2)


def camera_3d_to_image_coordinates(
    camera_points_3d: torch.Tensor,
    intrinsic_mat: torch.Tensor,
    depth_type: str = "planar",
) -> torch.Tensor:
    """
    カメラ座標系の3D点群を、2D画像座標 (u, v) と深度 (d) に投影する。
    """
    projected_homo = intrinsic_mat[None, :, :] @ camera_points_3d.transpose(1, 2)

    planar_depth = projected_homo[:, 2:3, :]
    planar_depth = torch.clamp(planar_depth, min=1e-8)

    u = projected_homo[:, 0:1, :] / planar_depth
    v = projected_homo[:, 1:2, :] / planar_depth
    uv_coords = torch.cat([u, v], dim=1).transpose(1, 2)

    if depth_type == "planar":
        depth = planar_depth.transpose(1, 2)
    elif depth_type == "radial":
        depth = torch.linalg.norm(camera_points_3d, ord=2, dim=-1, keepdim=True)
    else:
        raise ValueError(
            f"Invalid depth_type: '{depth_type}'. Must be 'planar' or 'radial'."
        )

    return torch.cat([uv_coords, depth], dim=-1)


# --- Rerunを使った可視化プログラム ---


def create_wall_point_cloud(width=50, height=30, z_distance=20, num_points=20000):
    """カメラのイメージ平面と平行な巨大な壁の点群を生成する"""
    points = np.random.rand(num_points, 3)
    points[:, 0] = (points[:, 0] - 0.5) * width  # X座標
    points[:, 1] = (points[:, 1] - 0.5) * height  # Y座標
    points[:, 2] = z_distance  # Z座標は固定
    return points


if __name__ == "__main__":
    # --- Rerunの初期化 ---
    rr.init("lidar_camera_projection", spawn=True)

    # --- 可視化用データの準備 ---
    DEVICE = "cpu"
    IMAGE_WIDTH, IMAGE_HEIGHT = 1280, 720

    # [改善] 巨大な壁の点群を生成
    logger.info("Creating a giant wall point cloud...")
    lidar_points_np = create_wall_point_cloud(
        width=50, height=30, z_distance=20, num_points=20000
    )
    lidar_points = torch.from_numpy(lidar_points_np).float().to(DEVICE)

    focal_length = IMAGE_WIDTH / 2
    intrinsic = torch.tensor(
        [
            [focal_length, 0.0, IMAGE_WIDTH / 2],
            [0.0, focal_length, IMAGE_HEIGHT / 2],
            [0.0, 0.0, 1.0],
        ],
        device=DEVICE,
    )
    extrinsic = torch.tensor(
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]],
        device=DEVICE,
    )

    # --- Rerunへのログ記録 ---

    rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Y_DOWN)
    rr.log(
        "world/image",
        rr.Pinhole(
            image_from_camera=intrinsic.cpu().numpy(),
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
        ),
    )

    z_coords = lidar_points_np[:, 2]
    colors_3d_normalized = (z_coords - z_coords.min()) / (
        z_coords.max() - z_coords.min() + 1e-6
    )
    colors_3d_rgba = cm.viridis(colors_3d_normalized)
    rr.log(
        "world/lidar_points",
        rr.Points3D(positions=lidar_points_np, colors=colors_3d_rgba),
    )

    dummy_image = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH, 3), dtype=np.uint8)
    rr.log("world/image", rr.Image(dummy_image))

    logger.info("Projecting points and logging to Rerun...")

    # --- 投影計算とログ記録 ---
    projected_planar_coords = (
        lidar_to_image_coordinates(
            lidar_points,
            intrinsic,
            extrinsic,
            depth_type="planar",
            without_batch_dim=True,
        )
        .cpu()
        .numpy()
    )

    u, v, d = (
        projected_planar_coords[:, 0],
        projected_planar_coords[:, 1],
        projected_planar_coords[:, 2],
    )
    valid_mask = (u >= 0) & (u < IMAGE_WIDTH) & (v >= 0) & (v < IMAGE_HEIGHT) & (d > 0)

    projected_radial_coords = (
        lidar_to_image_coordinates(
            lidar_points,
            intrinsic,
            extrinsic,
            depth_type="radial",
            without_batch_dim=True,
        )
        .cpu()
        .numpy()
    )

    valid_planar_points = projected_planar_coords[valid_mask]
    valid_radial_points = projected_radial_coords[valid_mask]

    if len(valid_planar_points) == 0:
        logger.warning("No valid points found in projection. Exiting.")
        sys.exit()

    all_valid_depths = np.concatenate(
        [valid_planar_points[:, 2], valid_radial_points[:, 2]]
    )
    global_min_depth, global_max_depth = (
        np.min(all_valid_depths),
        np.max(all_valid_depths),
    )
    logger.info(
        f"Global depth range for color normalization: [{global_min_depth:.2f}, {global_max_depth:.2f}]"
    )

    projection_data = {"planar": valid_planar_points, "radial": valid_radial_points}

    for depth_type, valid_points in projection_data.items():
        logger.info(
            f"Logging '{depth_type}' projection with hover-over depth values..."
        )
        uv = valid_points[:, :2]
        depths = valid_points[:, 2]

        # 色の計算
        norm_depths = (depths - global_min_depth) / (
            global_max_depth - global_min_depth + 1e-8
        )
        colors = cm.viridis(norm_depths)[:, :3] * 255

        # [改善] ホバー時に表示するアノテーションを作成
        annotations = [
            rr.AnnotationInfo(id=i, label=f"Depth: {d:.2f}m")
            for i, d in enumerate(depths)
        ]
        class_ids = np.arange(len(depths))

        entity_path = f"world/image/{depth_type}_projection"
        rr.log(entity_path, rr.AnnotationContext(annotations))
        rr.log(
            entity_path,
            rr.Points2D(positions=uv, colors=colors, radii=2, class_ids=class_ids),
        )

    logger.info("Done. Check the Rerun viewer.")
    logger.info("--> Hover over points in the 2D view to see their depth values.")
    logger.info(
        "--> Note the color difference between 'planar' (uniform) and 'radial' (gradient)."
    )
