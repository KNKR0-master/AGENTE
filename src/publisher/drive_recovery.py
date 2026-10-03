from __future__ import annotations

from src.drive_inventory import DriveInventoryScanner
from src.publisher.drive_sync import configured_scope, project_drive_inventory_to_publisher_media


def reconcile_drive_inventory_and_project_media(conn, provider, config) -> dict:
    """Controlled recovery on the caller's database; never invoked by startup."""
    result = DriveInventoryScanner(provider, config).scan_inventory(conn, force=True)
    if result.get("status") != "COMPLETE" or result.get("folders_failed", 0) or result.get("in_progress"):
        return {"status": "RECOVERY_INCOMPLETE", "scan": result, "projection_executed": False}
    scope = configured_scope(config)
    with conn:
        updates = project_drive_inventory_to_publisher_media(conn, root_folder_id=scope.root_folder_id)
    return {"status": "COMPLETE", "scan": result, "projection_executed": True,
            "projection_updates": updates}
