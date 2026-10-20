from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from src.drive_inventory import DriveInventoryConfig, GoogleApiDriveInventoryProvider, is_stock_video_name


CECILIA = "ceciliafigueiredo.shop@gmail.com"


class MediaProvider(Protocol):
    def list_files(self): ...
    def get_metadata(self, file_id): ...
    def get_remote_metadata(self, file_id): ...
    def download_temporary(self, asset, directory): ...
    def exists(self, file_id): ...
    def delete_remote(self, asset): ...


def media_provider(asset=None) -> MediaProvider:
    if asset and (asset.get('provider') != 'google_drive' or asset.get('source_account') != 'cecilia'):
        raise ValueError('Origem de midia nao habilitada no Publisher')
    return GoogleDriveMediaProvider()


class GoogleDriveMediaProvider:
    """Reuse inventory authentication; mutations are scoped to Cecilia's active tree."""
    def __init__(self, config=None):
        self.config = config or DriveInventoryConfig.from_env()
        if (self.config.account_email or "").lower() != CECILIA:
            raise ValueError("Publisher exige CECILIA_DRIVE; outra conta nao sera utilizada")
        self.root_id = self.config.active_profile_folder_id
        if not self.root_id:
            raise ValueError("Pasta ativa da Cecilia nao configurada")
        self.inventory = GoogleApiDriveInventoryProvider.from_config(
            self.config, scopes=("https://www.googleapis.com/auth/drive",))
        self.service = self.inventory._service
        root = self._metadata(self.config.root_folder_id)
        if CECILIA not in {o.get("emailAddress", "").lower() for o in root.get("owners", [])}:
            raise ValueError("Proprietaria da pasta raiz nao confirmada como Cecilia")
        if not self._within(self.root_id, self.config.root_folder_id):
            raise ValueError("Pasta ativa fora do Drive configurado da Cecilia")

    def _metadata(self, file_id):
        return self.service.files().get(fileId=file_id, supportsAllDrives=True,
            fields="id,name,mimeType,size,md5Checksum,parents,createdTime,modifiedTime,webViewLink,thumbnailLink,trashed,ownedByMe,driveId,owners(emailAddress),capabilities(canDelete,canTrash,canDownload)").execute()

    def _within(self, file_id, root_id):
        pending, seen = [file_id], set()
        while pending:
            current = pending.pop()
            if current == root_id:
                return True
            if current in seen or len(seen) > 100:
                continue
            seen.add(current)
            item = self._metadata(current)
            if item.get("trashed"):
                continue
            if current in self.config.ignored_folder_ids or item.get("name", "").lower() in self.config.administrative_names:
                continue
            pending.extend(item.get("parents", []))
        return False

    def get_metadata(self, file_id):
        item = self._metadata(file_id)
        if item.get("trashed") or item.get("mimeType") != "video/mp4" or not is_stock_video_name(item["name"]):
            raise ValueError("Midia nao elegivel: exige MP4 FINAL ou padrao Gera Viral")
        if not self._within(file_id, self.root_id):
            raise ValueError("Midia fora da pasta ativa permitida")
        return item

    def get_remote_metadata(self, file_id):
        """Return raw metadata, including trashed files; return None only for HTTP 404."""
        from googleapiclient.errors import HttpError
        try:
            return self._metadata(file_id)
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise

    def list_files(self):
        pending, seen, result = [(self.root_id, "")], set(), []
        while pending:
            folder, prefix = pending.pop()
            if folder in seen:
                continue
            seen.add(folder)
            for entry in self.inventory.list_children(folder):
                if entry.is_folder:
                    if entry.file_id not in self.config.ignored_folder_ids and entry.name.lower() not in self.config.administrative_names:
                        pending.append((entry.file_id, f"{prefix}/{entry.name}"))
                elif entry.mime_type == "video/mp4" and is_stock_video_name(entry.name):
                    result.append({"id": entry.file_id, "name": entry.name, "mimeType": entry.mime_type,
                        "size": entry.size, "md5Checksum": entry.md5_checksum, "parents": [folder],
                        "createdTime": entry.created_time, "modifiedTime": entry.modified_time,
                        "webViewLink": entry.web_view_link, "folder_path": prefix or "/"})
        return result

    def exists(self, file_id):
        item = self.get_remote_metadata(file_id)
        return bool(item and not item.get("trashed", False))

    def download_temporary(self, asset, directory, *, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError('LOCAL_PREPARATION_CANCELLED')
        from googleapiclient.http import MediaIoBaseDownload
        item = self.get_metadata(asset["remote_file_id"])
        verify_identity(asset, item)
        path = Path(directory) / "video.mp4"
        request = self.service.files().get_media(fileId=asset["remote_file_id"], supportsAllDrives=True)
        with path.open("wb") as output:
            downloader = MediaIoBaseDownload(output, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                if cancel_event is not None and cancel_event.is_set():
                    raise ValueError('LOCAL_PREPARATION_CANCELLED')
                _, done = downloader.next_chunk()
                if output.tell() > int(asset["size_bytes"]):
                    raise ValueError("Download excedeu o tamanho indexado")
        if path.stat().st_size != int(asset["size_bytes"]):
            raise ValueError("Download incompleto")
        if asset.get("checksum"):
            digest = hashlib.md5()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != asset["checksum"]:
                raise ValueError("Checksum do download diverge")
        return path

    def delete_remote(self, asset):
        item = self.get_remote_metadata(asset["remote_file_id"])
        if item is None:
            return False
        # Recheck the active source tree immediately before the mutation.
        item = self.get_metadata(asset["remote_file_id"])
        verify_identity(asset, item)
        if not item.get("capabilities", {}).get("canDelete"):
            raise ValueError("Conta de servico sem permissao de exclusao; limpeza remota pendente")
        # The lifecycle owns post-delete verification and performs this call once.
        self.service.files().delete(fileId=asset["remote_file_id"], supportsAllDrives=True).execute(num_retries=0)
        return True


def verify_identity(asset, item):
    if item["id"] != asset["remote_file_id"] or item["name"] != asset["filename"] or int(item.get("size", 0)) != asset["size_bytes"]:
        raise ValueError("Arquivo remoto mudou desde a indexacao")
    if asset.get("checksum") and item.get("md5Checksum") != asset["checksum"]:
        raise ValueError("Checksum remoto mudou")


class TemporaryMediaCache:
    def __init__(self, root):
        self.root = (Path(root).resolve() / "data/publisher/temp")
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_size = int(os.getenv("PUBLISHER_TEMP_CACHE_MAX_SIZE", str(2 * 1024**3)))
        self.max_age = int(os.getenv("PUBLISHER_TEMP_FILE_MAX_AGE", "86400"))

    def clean_orphans(self):
        for folder in self.root.glob("job_*"):
            try:
                if (folder / '.preserve').exists():
                    continue
                if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(self.root):
                    continue
                marker = folder / ".lease"
                if not marker.exists():
                    if time.time() - folder.stat().st_mtime < self.max_age:
                        continue
                else:
                    with marker.open('r+b') as lease:
                        lock_lease(lease)
                        unlock_lease(lease)
            except OSError:
                # An inaccessible directory is preserved and retried later.
                continue
            try:
                shutil.rmtree(folder)
            except OSError:
                # Antivirus/open handles may delay cleanup; retry on the next collection.
                continue

    @contextmanager
    def job(self, draft_id, size, preserve=False):
        self.clean_orphans()
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if not size or int(size) + used > self.max_size:
            raise ValueError("Limite do cache temporario excedido")
        folder = Path(tempfile.mkdtemp(prefix=f"job_{draft_id}_", dir=self.root))
        if preserve:
            (folder / '.preserve').touch()
        lease = (folder / '.lease').open('w+b')
        lease.write(b'1'); lease.flush()
        lock_lease(lease)
        try:
            yield folder
        finally:
            unlock_lease(lease)
            lease.close()
            if not preserve and folder.resolve().is_relative_to(self.root) and not folder.is_symlink():
                shutil.rmtree(folder)


def lock_lease(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_lease(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_UN)
