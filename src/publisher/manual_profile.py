"""Exclusive profile ownership; manual Chrome is never controlled by Playwright."""
import ctypes
import json
import os
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from time import time, sleep


def process_identity(pid):
    """Windows creation timestamp distinguishes a live PID from PID reuse."""
    if not pid: return None
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; if($p){{$p.StartTime.ToUniversalTime().Ticks}}"],
            capture_output=True,text=True,check=False,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def windows_arguments(command):
    argc = ctypes.c_int()
    api = ctypes.windll.shell32.CommandLineToArgvW
    api.restype = ctypes.POINTER(ctypes.c_wchar_p)
    api.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    argv = api(command, ctypes.byref(argc))
    if not argv:
        raise RuntimeError('PROCESS_ARGUMENTS_UNAVAILABLE')
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def profile_processes(profile):
    if os.name != 'nt':
        raise RuntimeError('WINDOWS_REQUIRED')
    command = "@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction Stop | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        # Some locked-down Windows environments deny process command-line
        # inventory. Chrome's profile singleton is the conservative fallback:
        # an existing marker blocks acquisition, while a clean profile can be
        # reconciled without inventing a second browser instance.
        markers = ('SingletonLock', 'SingletonCookie', 'SingletonSocket')
        return [-1] if any((profile / marker).exists() for marker in markers) else []
    records = json.loads(result.stdout or '[]')
    if isinstance(records, dict): records = [records]
    matches = []
    for record in records:
        if not record.get('CommandLine'):
            raise RuntimeError('PROCESS_INVENTORY_INCOMPLETE')
        args = windows_arguments(record['CommandLine'])
        for index, arg in enumerate(args):
            value = arg.split('=', 1)[1] if arg.startswith('--user-data-dir=') else (
                args[index+1] if arg == '--user-data-dir' and index+1 < len(args) else None)
            if value and os.path.normcase(str(Path(value).resolve())) == os.path.normcase(str(profile.resolve())):
                matches.append(record['ProcessId'])
    return matches


def chrome_executable():
    candidates = [Path(os.environ.get(name, '')) / 'Google/Chrome/Application/chrome.exe'
                  for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
    for candidate in candidates:
        if candidate.is_file(): return candidate.resolve()
    raise ValueError('INSTALLED_CHROME_NOT_FOUND')


class ProfileOwnership:
    def __init__(self, db, *, inventory=profile_processes, launcher=subprocess.Popen, executable=chrome_executable, identity=process_identity):
        self.db, self.inventory, self.launcher, self.executable = db, inventory, launcher, executable
        self.identity = identity
        self.instance_id = str(uuid4())
        with self.connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS publisher_profile_ownership (
                profile TEXT PRIMARY KEY, state TEXT NOT NULL, owner TEXT, browser_pid INTEGER)''')
            columns={r[1] for r in conn.execute('PRAGMA table_info(publisher_profile_ownership)')}
            for name,kind in {'owner_pid':'INTEGER','owner_instance_id':'TEXT','owner_process_identity':'TEXT',
                              'acquired_at':'REAL','heartbeat_at':'REAL','mode':'TEXT',
                              'cdp_port':'INTEGER','browser_owned':'INTEGER NOT NULL DEFAULT 0'}.items():
                if name not in columns: conn.execute(f'ALTER TABLE publisher_profile_ownership ADD COLUMN {name} {kind}')
            conn.execute('CREATE TABLE IF NOT EXISTS publisher_profile_events (timestamp REAL,event TEXT,profile TEXT,owner TEXT)')

    def event(self, conn, event, profile, owner=None):
        conn.execute('INSERT INTO publisher_profile_events VALUES (?,?,?,?)',(time(),event,self.key(profile),owner))

    def reconcile(self, profile):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT state,owner,owner_pid,owner_process_identity FROM publisher_profile_ownership WHERE profile=?',(self.key(profile),)).fetchone()
            if not row or row[0] in {'IDLE','SESSION_REQUIRES_VERIFICATION'}: return False
            if row[2]:
                actual=self.identity(row[2])
                if actual and (not row[3] or actual==row[3]): return False
            if self.inventory(profile): return False
            self.event(conn,'PROFILE_PROCESS_RELEASE_CONFIRMED',profile,row[1])
            conn.execute("UPDATE publisher_profile_ownership SET state='SESSION_REQUIRES_VERIFICATION',owner=NULL,browser_pid=NULL,owner_pid=NULL,owner_instance_id=NULL WHERE profile=?",(self.key(profile),))
            self.event(conn,'STALE_LOCK_RECONCILED',profile,row[1])
            return True

    def reconcile_all(self):
        with self.connection() as conn:
            profiles=[Path(r[0]) for r in conn.execute('SELECT profile FROM publisher_profile_ownership')]
        return [str(p) for p in profiles if self.reconcile(p)]

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db, timeout=20)
        try:
            with conn: yield conn
        finally: conn.close()

    @staticmethod
    def key(profile):
        return os.path.normcase(str(profile.resolve()))

    def acquire(self, profile, mode):
        if mode not in {'MANUAL', 'PLAYWRIGHT'} or not profile.is_dir():
            raise ValueError('EXISTING_PROFILE_REQUIRED')
        token = str(uuid4())
        self.reconcile(profile)
        identity = self.identity(os.getpid())
        if not identity: raise RuntimeError('OWNER_IDENTITY_UNAVAILABLE')
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT state FROM publisher_profile_ownership WHERE profile=?', (self.key(profile),)).fetchone()
            if row and row[0] not in {'IDLE', 'SESSION_REQUIRES_VERIFICATION'}:
                raise ValueError('BROWSER_PROFILE_BUSY')
            if self.inventory(profile):
                raise ValueError('BROWSER_PROFILE_IN_USE_BY_CHROME')
            conn.execute('INSERT OR REPLACE INTO publisher_profile_ownership (profile,state,owner,browser_pid,owner_pid,owner_instance_id,owner_process_identity,acquired_at,heartbeat_at,mode) VALUES (?,?,?,NULL,?,?,?,?,?,?)',
                         (self.key(profile), mode + '_STARTING', token,os.getpid(),self.instance_id,identity,time(),time(),mode))
            self.event(conn,'PROFILE_LOCK_ACQUIRED',profile,token)
        return token

    def activate(self, profile, token, state, pid=None):
        with self.connection() as conn:
            changed = conn.execute('UPDATE publisher_profile_ownership SET state=?,browser_pid=? WHERE profile=? AND owner=? AND state IN (?,?)',
                (state, pid, self.key(profile), token, 'MANUAL_STARTING', 'PLAYWRIGHT_STARTING')).rowcount
            if changed != 1: raise ValueError('PROFILE_OWNERSHIP_LOST')

    def activate_runtime(self, profile, token, state, *, pid, cdp_port, browser_owned):
        with self.connection() as conn:
            changed = conn.execute(
                '''UPDATE publisher_profile_ownership
                   SET state=?,browser_pid=?,cdp_port=?,browser_owned=?
                   WHERE profile=? AND owner=? AND state IN (?,?,?)''',
                (state, pid, cdp_port, int(bool(browser_owned)), self.key(profile), token,
                 'MANUAL_STARTING', 'PLAYWRIGHT_STARTING', 'CDP_STARTING'),
            ).rowcount
            if changed != 1:
                raise ValueError('PROFILE_OWNERSHIP_LOST')

    def adopt_existing(self, profile, *, pid, cdp_port):
        """Claim mutex metadata for a live external Chrome without owning shutdown."""
        if not profile.is_dir():
            raise ValueError('EXISTING_PROFILE_REQUIRED')
        token = str(uuid4())
        process_ids = self.inventory(profile)
        if pid not in process_ids:
            # WMI may return an empty list when command-line inventory is
            # denied. In that case, accept only the exact persisted PID when
            # its live process identity can still be read; never accept the
            # conservative [-1] singleton fallback.
            if process_ids or not self.identity(pid):
                raise ValueError('PROFILE_PROCESS_NOT_FOUND')
        identity = self.identity(os.getpid())
        if not identity:
            raise RuntimeError('OWNER_IDENTITY_UNAVAILABLE')
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'SELECT state,owner,browser_pid,owner_pid,cdp_port FROM publisher_profile_ownership WHERE profile=?',
                (self.key(profile),),
            ).fetchone()
            if row and row[0] not in {'IDLE', 'SESSION_REQUIRES_VERIFICATION'}:
                if row[0] != 'CDP_ATTACHED' or row[2] != pid or row[4] != cdp_port:
                    raise ValueError('BROWSER_PROFILE_BUSY')
                if row[1] and row[3] == os.getpid():
                    return row[1]
            conn.execute(
                '''INSERT OR REPLACE INTO publisher_profile_ownership
                   (profile,state,owner,browser_pid,owner_pid,owner_instance_id,
                    owner_process_identity,acquired_at,heartbeat_at,mode,cdp_port,browser_owned)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (self.key(profile), 'CDP_STARTING', token, pid, os.getpid(), self.instance_id,
                 identity, time(), time(), 'CDP', cdp_port, 0),
            )
            self.event(conn, 'PROFILE_EXTERNAL_BROWSER_ADOPTED', profile, token)
        return token

    def detach(self, profile, token, *, keep_browser):
        with self.connection() as conn:
            self.event(conn, 'PROFILE_RUNTIME_DETACH_REQUESTED', profile, token)
            if keep_browser:
                conn.execute(
                    '''UPDATE publisher_profile_ownership
                       SET state='CDP_ATTACHED',owner=NULL,owner_pid=NULL,owner_instance_id=NULL,
                           owner_process_identity=NULL,browser_owned=0
                       WHERE profile=? AND owner=?''',
                    (self.key(profile), token),
                )
                self.event(conn, 'PROFILE_RUNTIME_DETACHED_BROWSER_PRESERVED', profile, token)
                return True
            if self.inventory(profile):
                return False
            conn.execute(
                '''UPDATE publisher_profile_ownership
                   SET state='SESSION_REQUIRES_VERIFICATION',owner=NULL,browser_pid=NULL,
                       owner_pid=NULL,owner_instance_id=NULL,owner_process_identity=NULL,
                       cdp_port=NULL,browser_owned=0
                   WHERE profile=? AND owner=?''',
                (self.key(profile), token),
            )
            self.event(conn, 'PROFILE_RUNTIME_RELEASED', profile, token)
            return True

    def release(self, profile, token):
        with self.connection() as conn:
            self.event(conn,'PROFILE_LOCK_RELEASE_REQUESTED',profile,token)
            if self.inventory(profile): return False
            self.event(conn,'PROFILE_PROCESS_RELEASE_CONFIRMED',profile,token)
            conn.execute("UPDATE publisher_profile_ownership SET state='SESSION_REQUIRES_VERIFICATION',owner=NULL,browser_pid=NULL WHERE profile=? AND owner=?",
                         (self.key(profile), token))
            self.event(conn,'PROFILE_LOCK_RELEASED',profile,token)
            return True

    def heartbeat(self, profile, token):
        with self.connection() as conn:
            conn.execute('UPDATE publisher_profile_ownership SET heartbeat_at=? WHERE profile=? AND owner=?',(time(),self.key(profile),token))

    def state(self, profile):
        with self.connection() as conn:
            row = conn.execute('SELECT state FROM publisher_profile_ownership WHERE profile=?', (self.key(profile),)).fetchone()
            return row[0] if row else 'IDLE'

    def runtime_metadata(self, profile):
        with self.connection() as conn:
            row = conn.execute(
                'SELECT state,browser_pid,cdp_port,browser_owned FROM publisher_profile_ownership WHERE profile=?',
                (self.key(profile),),
            ).fetchone()
        return (
            {
                'state': row[0],
                'browser_pid': row[1],
                'cdp_port': row[2],
                'browser_owned': row[3],
            }
            if row else None
        )

    def check_manual_closed(self, profile):
        # Explicit local check only. No URL, DOM, cookies or storage access.
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT state FROM publisher_profile_ownership WHERE profile=?', (self.key(profile),)).fetchone()
            if row and row[0] == 'MANUAL_BROWSER_ACTIVE':
                if not self.inventory(profile):
                    conn.execute("UPDATE publisher_profile_ownership SET state='SESSION_REQUIRES_VERIFICATION',owner=NULL,browser_pid=NULL WHERE profile=?", (self.key(profile),))
                    return 'SESSION_REQUIRES_VERIFICATION'
            return row[0] if row else 'IDLE'

    def open_manual(self, profile):
        token = self.acquire(profile, 'MANUAL')
        try:
            args = [str(self.executable()), '--user-data-dir=' + str(profile.resolve()), 'about:blank']
            process = self.launcher(args, shell=False)
            self.activate(profile, token, 'MANUAL_BROWSER_ACTIVE', process.pid)
            return {'state': 'MANUAL_BROWSER_ACTIVE', 'pid': process.pid}
        except Exception:
            # Retain the lease if process creation may have succeeded: fail closed.
            raise
