import torch
from loguru import logger


def spatially_uniform_sample(pc: torch.Tensor, n_samples: int) -> torch.Tensor:
    """
    Perform spatially uniform sampling on the xy-plane of a point cloud.

    This algorithm divides the xy-plane into a grid and samples points evenly from
    occupied grid cells to ensure better spatial distribution of points.

    Parameters
    ----------
    pc : torch.Tensor
        Point cloud tensor with shape (N, 3) where N is the number of points.
        The first two columns are the xy-coordinates, and the third column is the z-coordinate (depth).
    n_samples : int
        Number of points to sample.

    Returns
    -------
    torch.Tensor
        Sampled point cloud with shape (n_samples, 3).
    """
    device = pc.device
    if pc.numel() == 0:
        return torch.zeros((n_samples, 3), device=device)

    n_points = pc.shape[0]

    if n_points <= n_samples:
        # If we have fewer points than required, just duplicate
        logger.debug(f"Spatial sampling with replacement ({n_points} → {n_samples})")
        indices = torch.randint(0, n_points, (n_samples,), device=device)
        return pc[indices]

    # Compute xy bounding box
    min_xy = torch.min(pc[:, :2], dim=0)[0]
    max_xy = torch.max(pc[:, :2], dim=0)[0]

    # Determine grid size (aim for average of ~10 points per cell)
    grid_size = int(torch.sqrt(torch.tensor(n_points / 10)))
    grid_size = max(4, min(grid_size, 32))  # Constrain between 4x4 and 32x32

    # Compute cell sizes
    cell_size_x = (max_xy[0] - min_xy[0]) / grid_size
    cell_size_y = (max_xy[1] - min_xy[1]) / grid_size

    # Zero-division check
    if torch.isclose(cell_size_x, torch.tensor(0.0)) or torch.isclose(
        cell_size_y, torch.tensor(0.0)
    ):
        logger.error(
            "Point cloud has near-zero extent in xy plane, using regular sampling"
        )
        indices = torch.randperm(n_points, device=device)[:n_samples]
        return pc[indices]

    # Assign points to grid cells
    cell_x = torch.clamp(
        ((pc[:, 0] - min_xy[0]) / cell_size_x).long(), 0, grid_size - 1
    )
    cell_y = torch.clamp(
        ((pc[:, 1] - min_xy[1]) / cell_size_y).long(), 0, grid_size - 1
    )
    cell_indices = cell_y * grid_size + cell_x

    # Count points per cell and find occupied cells
    unique_cells, counts = torch.unique(cell_indices, return_counts=True)
    num_occupied_cells = len(unique_cells)

    if num_occupied_cells == 0:
        logger.error("No occupied cells found in point cloud")
        return torch.zeros((n_samples, 3), device=device)

    # Determine points to sample per occupied cell
    points_per_cell = n_samples // num_occupied_cells
    extra_points = n_samples % num_occupied_cells

    selected_indices = []

    # Sample from each occupied cell
    for i, cell_idx in enumerate(unique_cells):
        # Points in this cell
        cell_points_mask = cell_indices == cell_idx
        cell_point_indices = torch.where(cell_points_mask)[0]

        # Number of points to sample from this cell
        cell_samples = points_per_cell + (1 if i < extra_points else 0)

        if len(cell_point_indices) <= cell_samples:
            # If cell has fewer points than required, sample with replacement
            cell_selected = cell_point_indices[
                torch.randint(
                    0, len(cell_point_indices), (cell_samples,), device=device
                )
            ]
        else:
            # Sample without replacement
            perm = torch.randperm(len(cell_point_indices), device=device)
            cell_selected = cell_point_indices[perm[:cell_samples]]

        selected_indices.append(cell_selected)

    # Combine all selected indices
    all_selected = torch.cat(selected_indices)

    # Verify we have the right number of points
    if len(all_selected) != n_samples:
        logger.error(
            f"Spatial sampling produced {len(all_selected)} points instead of {n_samples}, "
            "using regular sampling as fallback"
        )
        indices = torch.randperm(n_points, device=device)[:n_samples]
        return pc[indices]

    return pc[all_selected]
