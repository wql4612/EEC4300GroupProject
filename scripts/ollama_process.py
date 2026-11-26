"""Read the selected settings of the running 64-bit Windows Ollama process."""

import ctypes
from ctypes import wintypes


def read_service_environment(pid):
    # The registered Windows environment uses native 64-bit Python and Ollama.
    # PEB and RTL_USER_PROCESS_PARAMETERS layouts below are for native x64.
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Ollama process verification requires 64-bit Python")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.IsWow64Process2.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort),
    )
    kernel.ReadProcessMemory.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    )
    nt.NtQueryInformationProcess.argtypes = (
        wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p,
    )
    handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)  # query limited information + read memory
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    def read(address, size):
        data = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not kernel.ReadProcessMemory(handle, address, data, size, ctypes.byref(count)) or count.value != size:
            raise RuntimeError("Unable to read the live Ollama process environment")
        return data.raw

    try:
        guest, native = ctypes.c_ushort(), ctypes.c_ushort()
        if not kernel.IsWow64Process2(handle, ctypes.byref(guest), ctypes.byref(native)) or (guest.value, native.value) != (0, 0x8664):
            raise RuntimeError("Expected the registered native x64 Ollama executable")
        info = (ctypes.c_void_p * 6)()
        if nt.NtQueryInformationProcess(handle, 0, info, ctypes.sizeof(info), None) or not info[1]:
            raise RuntimeError("Unable to find the Ollama process environment block")
        params = int.from_bytes(read(info[1] + 0x20, 8), "little")
        if not params:
            raise RuntimeError("Ollama process parameters are not available")
        address = int.from_bytes(read(params + 0x80, 8), "little")
        if not address or address % 2:
            raise RuntimeError("Invalid address for Ollama's process environment")
        data = bytearray()
        while len(data) < 1024 * 1024:
            size = 4096 - address % 4096
            data.extend(read(address, size))
            address += size
            text = data.decode("utf-16-le", errors="surrogatepass")
            if "\0\0" in text:
                fields = {}
                for item in text.split("\0\0", 1)[0].split("\0"):
                    name, _, value = item.partition("=")
                    if name.upper() in {"OLLAMA_HOST", "OLLAMA_MODELS"}:
                        if name.upper() in fields:
                            raise RuntimeError(f"Duplicate service environment setting: {name}")
                        fields[name.upper()] = value
                return fields
        raise RuntimeError("Ollama process environment is too large to verify")
    finally:
        kernel.CloseHandle(handle)
