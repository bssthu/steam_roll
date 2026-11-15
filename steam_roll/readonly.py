"""Fail closed: the serving process may not mutate ANY filesystem location.

Installed before loading Steam data. Even future accidental open(..., 'w'),
rename/delete, registry writes or subprocess launches fail with PermissionError.
This is an application guard, not an OS sandbox against malicious native code.
"""
import os
import sys

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
DENIED = {
    'os.remove', 'os.rename', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown',
    'os.utime', 'os.truncate', 'os.link', 'os.symlink', 'os.system', 'os.startfile',
    'os.startfile/2', 'os.posix_spawn', 'subprocess.Popen', 'shutil.copyfile',
    'shutil.copymode', 'shutil.copystat', 'winreg.SetValue', 'winreg.SetValueEx',
    'winreg.CreateKey', 'winreg.DeleteKey', 'winreg.DeleteValue',
}


def audit(event, args):
    if event == 'open':
        _, mode, flags = args
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (flags & WRITE_FLAGS):
            raise PermissionError('Steam Roll 运行时禁止任何文件写入')
    if event in DENIED:
        raise PermissionError(f'Steam Roll 只读保护阻止了 {event}')


def install():
    sys.dont_write_bytecode = True
    sys.addaudithook(audit)
