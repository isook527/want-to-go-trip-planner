"""Apply and verify a Windows DACL without modifying the file owner or group."""
import ctypes
from ctypes import wintypes
from pathlib import Path

DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SE_DACL_PROTECTED = 0x1000
SE_FILE_OBJECT = 1


def apply_private_acl(path, sid, directory=False):
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    pp = ctypes.POINTER(ptr)
    dword, boolean = wintypes.DWORD, wintypes.BOOL

    def api(module, name, result, *parameters):
        function = getattr(module, name)
        function.restype = result
        function.argtypes = parameters
        return function

    convert = api(security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                  boolean, wintypes.LPCWSTR, dword, pp, ctypes.POINTER(dword))
    dacl = api(security, "GetSecurityDescriptorDacl", boolean, ptr, ctypes.POINTER(boolean),
               pp, ctypes.POINTER(boolean))
    set_info = api(security, "SetNamedSecurityInfoW", dword, wintypes.LPWSTR,
                   dword, dword, ptr, ptr, ptr, ptr)
    get_info = api(security, "GetNamedSecurityInfoW", dword, wintypes.LPCWSTR,
                   dword, dword, pp, pp, pp, pp, pp)
    control = api(security, "GetSecurityDescriptorControl", boolean, ptr,
                  ctypes.POINTER(wintypes.WORD), ctypes.POINTER(dword))
    get_ace = api(security, "GetAce", boolean, ptr, dword, pp)
    local_free = api(kernel, "LocalFree", ptr, ptr)

    def fail(operation, code=None):
        raise PermissionError(
            f"PRIVATE_PERMISSION_ENFORCEMENT_FAILED: {operation}_{code if code is not None else ctypes.get_last_error()}"
        )

    def single_ace(acl):
        if not acl or int.from_bytes(ctypes.string_at(acl.value + 4, 2), "little") != 1:
            fail("WINDOWS_DACL_RULE_COUNT", 0)
        entry = ptr()
        if not get_ace(acl, 0, ctypes.byref(entry)):
            fail("WINDOWS_DACL_ACE")
        size = int.from_bytes(ctypes.string_at(entry.value + 2, 2), "little")
        return ctypes.string_at(entry, size)

    descriptor, expected, actual_descriptor, actual = ptr(), ptr(), ptr(), ptr()
    path = str(Path(path).absolute())
    # Grant the caller full access with no inherited entries.
    inheritance = "OICI" if directory else ""
    try:
        if not convert(f"D:P(A;{inheritance};FA;;;{sid})", 1, ctypes.byref(descriptor), None):
            fail("WINDOWS_DACL_PARSE")
        present, defaulted = boolean(), boolean()
        if not dacl(descriptor, ctypes.byref(present), ctypes.byref(expected), ctypes.byref(defaulted)):
            fail("WINDOWS_DACL_EXTRACT")
        expected_ace = single_ace(expected)
        result = set_info(path, SE_FILE_OBJECT,
                          DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                          None, None, expected, None)
        if result:
            fail("WINDOWS_DACL_SET", result)
        result = get_info(path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                          None, None, ctypes.byref(actual), None, ctypes.byref(actual_descriptor))
        if result:
            fail("WINDOWS_DACL_READ", result)
        flags, revision = wintypes.WORD(), dword()
        if not control(actual_descriptor, ctypes.byref(flags), ctypes.byref(revision)):
            fail("WINDOWS_DACL_CONTROL")
        if not (flags.value & SE_DACL_PROTECTED) or single_ace(actual) != expected_ace:
            fail("WINDOWS_DACL_VERIFY", 0)
        return True
    finally:
        if actual_descriptor:
            local_free(actual_descriptor)
        if descriptor:
            local_free(descriptor)
