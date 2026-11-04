"""OS-owned per-workbook mutex; no candle/state journal and no time-based stealing.

Coordinates participating processes on ONE host. Google editors and old remote
writers cannot be fenced by a local mutex and must be excluded at acceptance.
"""
from contextlib import contextmanager
import hashlib
import os
import threading
from birzha.application.canonical_market_mirror import MirrorConflict

_registry = {}
_registry_lock = threading.Lock()

class _Lease:
    def __init__(self, key):
        self.key = hashlib.sha256(key.encode()).hexdigest()
        self.local = threading.RLock()
        self.depth = 0
        self.handle = None

    @contextmanager
    def hold(self):
        if not self.local.acquire(blocking=False):
            raise MirrorConflict('WORKBOOK_WRITER_BUSY')
        owned = False
        try:
            if self.depth == 0:
                if os.name == 'nt':
                    import ctypes
                    from ctypes import wintypes
                    api = ctypes.WinDLL('kernel32', use_last_error=True)
                    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                    api.CreateMutexW.restype = wintypes.HANDLE
                    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                    api.WaitForSingleObject.restype = wintypes.DWORD
                    api.ReleaseMutex.argtypes = [wintypes.HANDLE]
                    api.CloseHandle.argtypes = [wintypes.HANDLE]
                    h = api.CreateMutexW(None, False, 'Global\\BirzhaGoogleWrite_' + self.key)
                    if not h: raise MirrorConflict('WORKBOOK_LOCK_UNAVAILABLE')
                    code = api.WaitForSingleObject(h, 0)
                    if code not in (0, 128):
                        api.CloseHandle(h)
                        raise MirrorConflict('WORKBOOK_WRITER_BUSY')
                    self.handle = (api, h)
                else:
                    import fcntl
                    fd = os.open('/tmp/birzha-google-write-' + self.key, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        os.close(fd)
                        raise MirrorConflict('WORKBOOK_WRITER_BUSY') from None
                    self.handle = fd
            self.depth += 1
            owned = True
            yield
        finally:
            if owned:
                self.depth -= 1
                if self.depth == 0:
                    if os.name == 'nt':
                        api, h = self.handle
                        api.ReleaseMutex(h); api.CloseHandle(h)
                    else:
                        import fcntl
                        fcntl.flock(self.handle, fcntl.LOCK_UN); os.close(self.handle)
                    self.handle = None
            self.local.release()

def workbook_guard(book_id):
    with _registry_lock:
        lease = _registry.setdefault(book_id, _Lease(book_id))
    return lease.hold()
