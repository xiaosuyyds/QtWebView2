# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""

``_AnchorWindow`` — top-level transparent host for the WebView child HWND.

On Windows the window is made layered (per-pixel alpha via
``WS_EX_LAYERED``) to fill newly-exposed pixels with alpha=1 during
resize — eliminating the black-edge flicker the DWM would otherwise
produce when the layered surface is extended.

See the class docstring for a detailed rationale.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QWidget


# ── ctypes structures ──
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_int32), ("cy", ctypes.c_int32)]


_RECT = ctypes.c_uint32 * 4  # left, top, right, bottom

# Pre-allocated constant structures
_BLEND = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, use per-pixel alpha
_ZERO_POINT = _POINT(0, 0)


class _AnchorWindow(QWidget):
    """
    Top-level window that hosts the WebView child HWND.

    **Why this class exists**

    Windows WebView2 resizing produces a black-edge flicker.  The flicker
    comes from the DWM layered surface being extended before any new
    content is submitted — the DWM fills the new area with transparent
    black, which the user sees as a brief black flash at the window edge.

    The known fix is per-pixel alpha: fill every pixel of the layered
    surface with alpha=1 (visually transparent, but alpha>0 tells the
    DWM the pixel belongs to this window → hit-test passes).  Qt provides
    this via ``WA_TranslucentBackground`` + ``paintEvent``, but
    ``createWindowContainer`` suppresses *paintEvent* on the embedded
    window, so newly-exposed areas never receive the alpha=1 fill during
    resize.

    **How this class works around it**

    1. ``WA_TranslucentBackground`` — Qt creates the window with
       ``WS_EX_LAYERED`` and handles the *initial* layered surface setup.

    2. ``nativeEvent`` catches ``WM_SIZE`` — during a resize the window's
       layered surface is extended by the DWM (new area = alpha=0).  We
       schedule a deferred fill via a single-shot throttling timer at
       ~20 fps (50 ms).  The timer is restarted on every ``WM_SIZE``,
       so during an active resize gesture (60–120 WM_SIZE/sec) the fill
       is deferred indefinitely — the layered surface is NOT updated
       mid-resize.  This avoids DWM re-composition flicker from competing
       with the resize itself.  ``WM_EXITSIZEMOVE`` (0x0232) triggers an
       immediate fill for the final frame.

    3. A periodic 500 ms refresh timer runs whenever the window is visible
       as a safety net for edge cases where a needed repaint is not
       triggered by any window message (e.g. DWM composition events).

    4. The bitmap is filled with BGRA ``(0, 0, 0, 1)`` — premultiplied
       alpha where every pixel has alpha=1.  The DWM does per-pixel
       hit-testing: alpha>0 means the pixel belongs to this window, so
       clicks are registered even though the pixel is visually transparent
       (1/255 opacity).

    **Why not use ``WA_TranslucentBackground`` + ``repaint()``?**

    ``createWindowContainer`` stops ``paintEvent`` (and ``WM_PAINT``)
    from reaching the embedded window for newly-exposed areas.  Calling
    ``repaint()``, ``update()``, or even direct QPainter painting has no
    effect — Qt's internal foreign-window integration simply does not
    route those paint operations to the layered surface.  The only
    reliable path is to call ``UpdateLayeredWindow`` ourselves.
    """

    def __init__(self):
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        # Single-shot timer for WM_SIZE throttling
        self._fill_timer = QTimer(self)
        self._fill_timer.setSingleShot(True)
        self._fill_timer.timeout.connect(self._fill_layered)
        # Periodic refresh — safety net for missed/dropped repaint events
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._fill_layered)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._refresh_timer.stop()

    # ── Win32 message handling ──────────────────────────────────────────

    def nativeEvent(self, eventType, message):
        if sys.platform != "win32":
            return False, 0
        if eventType != b"windows_generic_MSG":
            return False, 0

        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents

        if msg.message == 0x0005:  # WM_SIZE
            # Throttle: restart the 50 ms timer.  If the user keeps
            # dragging, the timer never fires and we don't flood the
            # DWM with UpdateLayeredWindow calls.
            self._fill_timer.start(50)
        elif msg.message == 0x0232:  # WM_EXITSIZEMOVE
            # Resize gesture ended — stop the timer and paint immediately
            # so the final frame is guaranteed correct.
            self._fill_timer.stop()
            self._fill_layered()
            self._fill_timer.start(50)
        elif msg.message == 0x031E:  # WM_DWMCOMPOSITIONCHANGED
            # DWM composition state changed (RDP connect/disconnect,
            # theme toggle, DWM restart).  The layered surface may
            # have been discarded — repaint immediately.
            self._fill_layered()
        elif msg.message == 0x0018:  # WM_SHOWWINDOW
            # Re-show after hide: the DWM discards the layered surface
            # on hide.  The DWM runs in a separate process and processes
            # show commands asynchronously — there is no Win32 message
            # that signals completion.  We reuse the existing 50 ms
            # throttled fill timer (also used by WM_SIZE) to defer
            # UpdateLayeredWindow until the DWM has settled.
            if msg.wParam == 1:
                self._fill_timer.stop()
                self._fill_layered()
                self._fill_timer.start(50)

        return False, 0

    # ── Per-pixel alpha surface update ───────────────────────────────────

    def _fill_layered(self):
        """Create a 32-bit BGRA DIB section, fill every pixel
        with ``(B=0, G=0, R=0, A=1)``, and submit it via
        ``UpdateLayeredWindow`` with ``ULW_ALPHA``."""
        hwnd = int(self.winId())
        rect = _RECT()
        ctypes.windll.user32.GetClientRect(hwnd, rect)
        w, h = rect[2], rect[3]  # right, bottom
        if w <= 0 or h <= 0:
            return

        bih = _BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bih.biWidth = w
        bih.biHeight = -h  # negative → top-down DIB (origin at top-left)
        bih.biPlanes = 1
        bih.biBitCount = 32  # BGRA, 8 bits per channel

        scrdc = ctypes.windll.user32.GetDC(0)
        mdc = ctypes.windll.gdi32.CreateCompatibleDC(scrdc)
        pBits = ctypes.c_void_p()
        hbmp = ctypes.windll.gdi32.CreateDIBSection(
            mdc, ctypes.byref(bih), 0, ctypes.byref(pBits), None, 0)
        old_bmp = ctypes.windll.gdi32.SelectObject(mdc, hbmp)

        # Fill: premultiplied alpha BGRA (B=0, G=0, R=0, A=1).
        ctypes.memmove(pBits, b'\x00\x00\x00\x01' * (w * h), w * h * 4)

        ctypes.windll.user32.UpdateLayeredWindow(
            hwnd, scrdc, None, ctypes.byref(_SIZE(w, h)),
            mdc, ctypes.byref(_ZERO_POINT),
            0, ctypes.byref(_BLEND), 0x00000002)  # 0x00000002 = ULW_ALPHA

        ctypes.windll.gdi32.SelectObject(mdc, old_bmp)
        ctypes.windll.gdi32.DeleteObject(hbmp)
        ctypes.windll.user32.ReleaseDC(0, scrdc)
        ctypes.windll.gdi32.DeleteDC(mdc)
