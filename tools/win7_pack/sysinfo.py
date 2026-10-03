# -*- coding: utf-8 -*-
"""Проверка sysinfo: версия Windows и обновления, кодовые страницы, экран и масштаб, прокси, место, DLL."""
import ctypes
import locale
import os
import platform
import shutil
import subprocess
import sys

DLLS = ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll"]


def _decode(b):
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def _cmd(args, timeout=30):
    try:
        out = subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=timeout, shell=False)
        return _decode(out).strip()
    except Exception as e:  # noqa
        return "ошибка: %s: %s" % (type(e).__name__, e)


def _section(data, key, fn):
    try:
        data[key] = fn()
    except Exception as e:  # noqa — одна секция не должна ломать остальные
        data[key] = "ошибка: %s: %s" % (type(e).__name__, e)


def file_version(path):
    """Версия файла (major.minor.build.rev) через version.dll; None, если не получилось."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    ln = ctypes.c_uint()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
        return None
    info = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
    ms, ls = info[4], info[5]
    return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def _windows():
    d = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version}
    if os.name == "nt":
        v = sys.getwindowsversion()
        d["windows_version"] = "%d.%d build %d %s" % (v.major, v.minor, v.build, v.service_pack)
        d["ver"] = _cmd(["cmd", "/c", "ver"])
        d["hotfixes"] = _cmd(["wmic", "qfe", "get", "HotFixID,InstalledOn", "/format:csv"], 60)
    return d


def _codepages():
    d = {"preferred_encoding": locale.getpreferredencoding(False), "filesystem_encoding": sys.getfilesystemencoding(),
         "stdout_encoding": getattr(sys.stdout, "encoding", None), "locale": str(locale.getdefaultlocale())}
    if os.name == "nt":
        k = ctypes.windll.kernel32
        d["ANSI_cp"], d["OEM_cp"] = k.GetACP(), k.GetOEMCP()
    return d


def _screen():
    if os.name != "nt":
        return "не Windows"
    u = ctypes.windll.user32
    try:
        u.SetProcessDPIAware()
    except Exception:  # noqa
        pass
    d = {"width": u.GetSystemMetrics(0), "height": u.GetSystemMetrics(1)}
    dc = u.GetDC(0)
    try:
        g = ctypes.windll.gdi32
        d["dpi_x"], d["dpi_y"] = g.GetDeviceCaps(dc, 88), g.GetDeviceCaps(dc, 90)
        d["scale_percent"] = round(d["dpi_x"] * 100 / 96)
    finally:
        u.ReleaseDC(0, dc)
    return d


def _proxy():
    import urllib.request
    d = {"env": {k: v for k, v in os.environ.items() if k.lower() in ("http_proxy", "https_proxy", "no_proxy", "all_proxy")},
         "urllib_getproxies": urllib.request.getproxies()}
    if os.name == "nt":
        d["netsh_winhttp"] = _cmd(["netsh", "winhttp", "show", "proxy"])
    return d


def _disk(app_dir):
    u = shutil.disk_usage(app_dir)
    return {"path": app_dir, "free_mb": u.free // 2 ** 20, "total_mb": u.total // 2 ** 20}


def _dlls(app_dir):
    d = {}
    folders = [os.path.join(app_dir, "python")]
    if os.name == "nt":
        folders.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    for name in DLLS:
        for folder in folders:
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                try:
                    v = file_version(p) if os.name == "nt" else None
                except Exception as e:  # noqa
                    v = "ошибка: %s" % e
                d["%s (%s)" % (name, os.path.basename(folder))] = {"size": os.path.getsize(p), "version": v}
            else:
                d["%s (%s)" % (name, os.path.basename(folder))] = "нет"
    return d


def run(ctx):
    data = {}
    _section(data, "windows", _windows)
    _section(data, "codepages", _codepages)
    _section(data, "screen", _screen)
    _section(data, "proxy", _proxy)
    _section(data, "disk", lambda: _disk(ctx.app_dir))
    _section(data, "dlls", lambda: _dlls(ctx.app_dir))
    _section(data, "app_dir", lambda: {"path": ctx.app_dir, "cwd": os.getcwd(), "unc": ctx.app_dir.startswith("\\\\")})
    data["status"] = "ok"
    return data
