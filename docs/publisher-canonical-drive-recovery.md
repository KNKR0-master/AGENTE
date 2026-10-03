# Controlled canonical Drive recovery

The recovery entrypoint is
`src.publisher.drive_recovery.reconcile_drive_inventory_and_project_media`.
The caller supplies an already-open SQLite connection, a read-only inventory
provider and `DriveInventoryConfig`. Recovery is not registered with startup,
polling or an HTTP mutation route.

The pipeline is:

1. `DriveInventoryScanner.scan_inventory(force=True)` reconciles canonical
   `drive_folders` and `drive_files` from the configured operational root.
2. Only a `COMPLETE` scan with no folder failures allows projection.
3. `project_drive_inventory_to_publisher_media` projects from SQLite, scoped
   to that root, without another Drive request.

The inventory scan writes Drive rows, `drive_scan_runs` and
`creator_drive_profiles`. The last table records the configured account's
active/reserve/archive topology and is required scan metadata. It preserves
existing product mappings and human folder roles. It does not synchronize
affiliate products, product names, prompts, content links or supply alerts.
The legacy `scan()` entrypoint retains those existing orchestration steps;
its callers and normal full-scan/bootstrap behavior are unchanged.

Projection models remote availability, independently of product/caption
readiness. A present video without a product can be Drive-available while the
Library still explains the product blocker. Human VALID/IGNORED decisions and
product overrides remain in their existing tables. Projection never changes
them, draft relations, reservations, reconciliation evidence or consumed
lifecycle state. Confirmed remote deletion prevents restoration to AVAILABLE.

Existing MediaAsset IDs are retained. Derived metadata changes update
`updated_at`; a new canonical observation may update `last_seen_at` without
touching `updated_at`. An unchanged second projection performs no writes.
Unavailable inventory marks only free AVAILABLE assets MISSING. In-flight,
reserved and consumed assets retain their lifecycle states.

Recovery preserves cursor and polling configuration. A full inventory scan is
not a Changes cursor reset. A future operational recovery must separately
choose the existing bootstrap contract (start token before scan, consume
changes after scan) or retain and catch up the existing valid cursor. It must
not replace the cursor with a token fetched only after the scan, which could
skip changes occurring during reconciliation.

The forensic copy is evidence, not an operational replacement database.
Recovery must be validated on a current database copy before any authorized
operational application. No schema migration is required.
