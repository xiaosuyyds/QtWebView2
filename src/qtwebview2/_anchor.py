# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
``_AnchorWindow`` — host ``QWindow`` for the WebView child HWND.

On Windows the host is backed by a Qt RHI Direct3D 11 swapchain that clears to a
fully transparent colour, giving a ``transparent=True`` WebView a real surface to
composite over and absorbing the black-edge flash wry produces while resizing.
See the class docstring for the full rationale.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys

from qtpy.QtCore import QSize, QEvent, Qt
from qtpy.QtGui import QWindow, QColor, QSurfaceFormat, QPlatformSurfaceEvent

# QRhi is Qt's rendering-hardware-interface — the backend for the transparent
# host on Windows.  It is Qt6-only and, among the Qt6 bindings, currently exposed
# only by PySide6.  Import it defensively so ``import qtwebview2`` still succeeds
# under PyQt5/PyQt6/PySide2: without it the host silently falls back to an opaque
# plain QWindow (a warning is logged at construction, see ``_AnchorWindow``).
try:
    from qtpy.QtGui import (
        QRhi, QRhiSwapChain, QRhiRenderBuffer,
        QRhiD3D11InitParams, QRhiDepthStencilClearValue,
    )
    _HAS_QRHI = True
except ImportError:
    _HAS_QRHI = False

logger = logging.getLogger(__name__)

# Undocumented Qt env var.  The Windows QPA plugin reads it at native-window
# ``create()`` time and, when set, creates the HWND with WS_EX_NOREDIRECTIONBITMAP
# — the flag that lets Qt build a DirectComposition transparent swapchain.
_ENV_DISABLE_REDIRECTION = "QT_QPA_DISABLE_REDIRECTION_SURFACE"

# Win32 constants — used only to verify the ex-style flag after creation.
_GWL_EXSTYLE = -20
_WS_EX_NOREDIRECTIONBITMAP = 0x00200000


class _AnchorWindow(QWindow):
    """
    Host ``QWindow`` that the WebView child HWND is parented into.

    **Why this class exists**

    A ``transparent=True`` WebView needs whatever sits *behind* it (the Qt
    content) to actually show through, and wry's resize produces a black-edge
    flash at newly-exposed pixels.  Both are solved by giving the host a genuine
    transparent surface: an empty window has nothing for the DWM to composite and
    comes out black instead.

    **How this class works (Windows)**

    The host is a ``QWindow`` with ``surfaceType = Direct3DSurface`` and an
    alpha-enabled format.  A Qt RHI Direct3D 11 swapchain
    (``SurfaceHasPreMulAlpha``) is attached to it; Qt then builds the whole
    DirectComposition chain for us — ``DCompositionCreateDevice`` →
    ``CreateTargetForHwnd`` → ``CreateVisual`` →
    ``CreateSwapChainForComposition`` (``DXGI_ALPHA_MODE_PREMULTIPLIED``) →
    ``SetContent``/``SetRoot`` → ``Commit`` — none of which we write by hand.

    Each frame clears the swapchain to ``(0, 0, 0, 0)``.  The DWM composites that
    transparent surface, so uncovered areas show the Qt content behind the host
    instead of black, and there is no redirection surface to flash on resize.  The
    WebView child HWND renders on top (airspace) and receives input as usual.

    The render loop is driven by ``exposeEvent`` and only runs on expose/resize —
    the content never changes, so there is no per-frame spin.

    **Caveats**

    * Windows 8+ (DirectComposition / flip swapchain).  The project's WebView2
      baseline (Win10/11) covers this.
    * Requires a Qt binding exposing ``QRhi`` (**PySide6**).  Under any other
      binding the RHI path is skipped and the host is an opaque plain ``QWindow``
      (transparency + resize-flicker suppression disabled; a warning is logged).
    * On macOS the RHI path is skipped: the host stays a plain ``QWindow`` and
      WKWebView handles transparency itself.
    """

    def __init__(self, transparent: bool = False):
        super().__init__()
        self._use_rhi = sys.platform == "win32" and _HAS_QRHI
        if sys.platform == "win32" and not _HAS_QRHI:
            logger.warning(
                "[anchor] QRhi is unavailable in the current Qt binding — the "
                "transparent WebView host requires PySide6 on Windows.  Falling "
                "back to an opaque host; transparency and resize-flicker "
                "suppression are disabled.%s",
                "  (transparent=True was requested)" if transparent else "",
            )

        # RHI swapchain state (Windows only; all None/False until first expose).
        self._rhi = None
        self._sc = None
        self._ds = None
        self._rp = None
        self._has_sc = False
        self._initialized = False
        self._not_exposed = False
        self._newly_exposed = False

        # Must be set *before* create(): this QWindow is realised while still
        # top-level, then embedded via createWindowContainer as a WS_CHILD.  A
        # framed top-level window carries Win32 frame margins (title bar + border);
        # on reparent Qt subtracts those margins from the child position, shifting
        # the HWND up-and-left so the WebView intrudes over whatever sits above it
        # (e.g. a toolbar).  Frameless zeroes the margins → the child lands exactly
        # on its container.
        self.setFlags(self.flags() | Qt.WindowType.FramelessWindowHint)

        if self._use_rhi:
            # Alpha-enabled Direct3D surface + create() under
            # WS_EX_NOREDIRECTIONBITMAP → Qt gives us the DirectComposition
            # transparent swapchain described in the class docstring.
            self.setSurfaceType(QWindow.SurfaceType.Direct3DSurface)
            fmt = QSurfaceFormat()
            fmt.setAlphaBufferSize(8)
            self.setFormat(fmt)
            self._create_no_redirection()

    # ── native window creation ───────────────────────────────────────────────

    def _create_no_redirection(self):
        """Realise the native HWND now, with WS_EX_NOREDIRECTIONBITMAP.

        Done by toggling ``QT_QPA_DISABLE_REDIRECTION_SURFACE`` around
        ``create()``, then restoring it so no other window inherits the flag.
        """
        prev = os.environ.get(_ENV_DISABLE_REDIRECTION)
        os.environ[_ENV_DISABLE_REDIRECTION] = "1"
        try:
            self.create()
        finally:
            if prev is None:
                os.environ.pop(_ENV_DISABLE_REDIRECTION, None)
            else:
                os.environ[_ENV_DISABLE_REDIRECTION] = prev

        hwnd = int(self.winId())
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & 0xFFFFFFFF
        logger.info("[anchor] hwnd=0x%X NOREDIRECTIONBITMAP=%s",
                    hwnd, bool(ex & _WS_EX_NOREDIRECTIONBITMAP))

    # ── event-driven render loop (mirrors Qt's rhiwindow example) ─────────────

    def exposeEvent(self, event):
        if not self._use_rhi:
            return

        # Lazily bring up the RHI + swapchain on first real expose.
        if self.isExposed() and not self._initialized:
            self._init_rhi()
            self._resize_swapchain()
            self._initialized = True

        surface_size = self._sc.surfacePixelSize() if self._has_sc else QSize()

        # Track transitions into/out of the "not exposed" (minimised/zero-size)
        # state so the next real expose forces a swapchain resize.
        if ((not self.isExposed() or (self._has_sc and surface_size.isEmpty()))
                and self._initialized and not self._not_exposed):
            self._not_exposed = True

        if (self.isExposed() and self._initialized and self._not_exposed
                and not surface_size.isEmpty()):
            self._not_exposed = False
            self._newly_exposed = True

        if self.isExposed() and not surface_size.isEmpty():
            self._render()

    def event(self, e):
        if self._use_rhi:
            if e.type() == QEvent.Type.UpdateRequest:
                self._render()
            elif e.type() == QEvent.Type.PlatformSurface:
                if (e.surfaceEventType()
                        == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed):
                    self._release_swapchain()
        return super().event(e)

    # ── RHI setup / teardown ─────────────────────────────────────────────────

    def _init_rhi(self):
        params = QRhiD3D11InitParams()
        self._rhi = QRhi.create(QRhi.Implementation.D3D11, params)
        if self._rhi is None:
            logger.error("[anchor] QRhi D3D11 create failed — host will not be transparent")
            self._use_rhi = False
            return

        self._sc = self._rhi.newSwapChain()
        self._ds = self._rhi.newRenderBuffer(
            QRhiRenderBuffer.Type.DepthStencil, QSize(), 1,
            QRhiRenderBuffer.Flag.UsedWithSwapChainOnly)
        self._sc.setWindow(self)
        self._sc.setDepthStencil(self._ds)
        # SurfaceHasPreMulAlpha → Qt takes the DirectComposition transparent path.
        self._sc.setFlags(QRhiSwapChain.Flag.SurfaceHasPreMulAlpha)
        self._rp = self._sc.newCompatibleRenderPassDescriptor()
        self._sc.setRenderPassDescriptor(self._rp)
        logger.info("[anchor] QRhi D3D11 transparent swapchain initialised")

    def _resize_swapchain(self):
        self._has_sc = self._sc.createOrResize()

    def _release_swapchain(self):
        if self._has_sc:
            self._has_sc = False
            self._sc.destroy()

    # ── frame rendering ──────────────────────────────────────────────────────

    def _render(self):
        if not self._has_sc or self._not_exposed:
            return

        # Resize the swapchain if the surface changed or we just came back.
        if (self._sc.currentPixelSize() != self._sc.surfacePixelSize()
                or self._newly_exposed):
            self._resize_swapchain()
            if not self._has_sc:
                return
            self._newly_exposed = False

        result = self._rhi.beginFrame(self._sc)
        if result == QRhi.FrameOpResult.FrameOpSwapChainOutOfDate:
            self._resize_swapchain()
            if not self._has_sc:
                return
            result = self._rhi.beginFrame(self._sc)
        if result != QRhi.FrameOpResult.FrameOpSuccess:
            self.requestUpdate()
            return

        cb = self._sc.currentFrameCommandBuffer()
        rt = self._sc.currentFrameRenderTarget()
        # Clear to fully transparent; draw nothing.
        cb.beginPass(rt, QColor(0, 0, 0, 0), QRhiDepthStencilClearValue(1.0, 0))
        cb.endPass()
        self._rhi.endFrame(self._sc)
        # Static content: no unconditional requestUpdate(), so the GPU does not
        # spin at vsync — we only redraw on expose/resize.
