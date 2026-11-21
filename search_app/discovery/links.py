"""Read-only enumeration of NTFS hardlink names (no module import IO)."""
import os

from .filters import io_path


def hardlink_paths(path):
    import ctypes
    from ctypes import wintypes as w
    if os.name != 'nt':
        raise OSError('hardlink enumeration requires Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    first = kernel.FindFirstFileNameW
    first.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(w.DWORD), w.LPWSTR]
    first.restype = w.HANDLE
    following = kernel.FindNextFileNameW
    following.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD), w.LPWSTR]
    following.restype = w.BOOL
    close = kernel.FindClose
    close.argtypes, close.restype = [w.HANDLE], w.BOOL
    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    length = w.DWORD(capacity)
    handle = first(io_path(path), 0, ctypes.byref(length), buffer)
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    try:
        while True:
            yield drive + buffer.value
            length.value = capacity
            if following(handle, ctypes.byref(length), buffer):
                continue
            error = ctypes.get_last_error()
            if error == 38:  # ERROR_HANDLE_EOF
                break
            raise ctypes.WinError(error)
    finally:
        close(handle)


def path_for_file_id(volume, file_id):
    """Resolve an omitted parent by ID, without opening file content."""
    import ctypes
    from ctypes import wintypes as w
    if os.name!='nt':
        raise OSError('file ID resolution requires Windows')
    class Identity(ctypes.Union):
        _fields_=[('number',ctypes.c_longlong),('extended',ctypes.c_ubyte*16)]
    class Descriptor(ctypes.Structure):
        _fields_=[('size',w.DWORD),('kind',ctypes.c_int),('identity',Identity)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    open_id=kernel.OpenFileById
    open_id.argtypes=[w.HANDLE,ctypes.POINTER(Descriptor),w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD]
    open_id.restype=w.HANDLE
    get_path=kernel.GetFinalPathNameByHandleW
    get_path.argtypes=[w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD]
    get_path.restype=w.DWORD
    close=kernel.CloseHandle
    close.argtypes,close.restype=[w.HANDLE],w.BOOL
    descriptor=Descriptor()
    descriptor.size=ctypes.sizeof(descriptor)
    descriptor.kind=0 if file_id<1<<64 else 2
    for i,value in enumerate(int(file_id).to_bytes(16,'little')):
        descriptor.identity.extended[i]=value
    handle=open_id(volume,ctypes.byref(descriptor),0,7,None,0x02000000|0x00200000)
    if handle==w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size=1024
        while size<=32768:
            buffer=ctypes.create_unicode_buffer(size)
            length=get_path(handle,buffer,size,0)
            if not length:
                raise ctypes.WinError(ctypes.get_last_error())
            if length<size:
                value=buffer.value
                if value.startswith('\\\\?\\UNC\\'):
                    return '\\\\'+value[8:]
                return value[4:] if value.startswith('\\\\?\\') else value
            size=length+1
        raise OSError('resolved file path exceeds supported length')
    finally:
        close(handle)
