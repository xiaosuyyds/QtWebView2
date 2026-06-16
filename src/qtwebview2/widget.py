# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
QtWebView — Qt webview widget powered by wryview (wry).

Embeds a wry WebView as a native child window inside any Qt widget.

**Supported platforms**: Windows, macOS.

**Linux** is **not** currently supported.  The underlying wryview library
compiles and runs on Linux (WebKitGTK), but the integration layer between
Qt's xcb backend and wry's Xlib/GDK code has unresolved issues — PRs
welcome from Linux contributors!  In the meantime, Linux desktop users
can run the app under Wine.
"""

from __future__ import annotations

__lazy_modules__ = ["wryview"]

import concurrent.futures
import functools
import json
import logging
import sys
import time
import webbrowser
from io import BytesIO
from typing import Callable, Any, Optional, Union
from typing_extensions import deprecated
from qtpy.QtCore import Qt, QTimer, QStandardPaths
from qtpy.QtWidgets import QWidget
from qtpy.QtGui import QWindow
from qtpy.QtWidgets import QVBoxLayout
from wryview import WebView, NewWindowResponse, DragDropEvent

from ._bridge import (
    QtWebViewSignals, QtWebViewJsBridge, DictJsBridge,
    BRIDGE_SCRIPT, FULLSCREEN_SCRIPT,
)
from ._anchor import _AnchorWindow

logger = logging.getLogger(__name__)


def _require_webview(error_if_not_ready: bool = False):
    """Decorator: if webview not ready, queue call or raise error.

    Args:
        error_if_not_ready: If True, raise RuntimeError instead of queuing.
            Use for methods that return values (cookies, url, etc.).
    """

    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if self.is_ready:
                return method(self, *args, **kwargs)
            if error_if_not_ready:
                raise RuntimeError(f"{method.__name__}(): WebView not initialized")
            self._pending_calls.append((method.__name__, args, kwargs))
            return None

        return wrapper

    return decorator


def default_new_window_handler(url: str):
    # Open in the default browser
    webbrowser.open(url)
    return NewWindowResponse.Deny


_NOT_GIVEN: Any = object()


# ═══════════════════════════════════════════════════════════════════════════════
# The Widget
# ═══════════════════════════════════════════════════════════════════════════════

class QtWebViewWidget(QWidget):
    def __init__(
            self,
            url: Optional[str] = None,
            html: Optional[str] = None,
            headers: Optional[Union[dict[str, str], list[tuple[str, str]]]] = None,
            user_agent: Optional[str] = None,
            debug: bool = False,
            transparent: bool = False,
            background_color: Optional[str] = None,
            navigation_handler: Optional[Callable[[str], bool]] = None,
            new_window_handler: Optional[Callable[[str], NewWindowResponse]] = default_new_window_handler,
            lazyload: bool = True,
            js_apis: Union[dict[str, Callable[..., Any]], QtWebViewJsBridge, None] = None,
            user_data_folder: Union[str, None] = _NOT_GIVEN,
            incognito: bool = False,
            wsgi_app: Optional[Callable[..., Any]] = None,
            wsgi_scheme: Optional[str] = None,
            wsgi_executor: Union[concurrent.futures.Executor, int] = 8,
            wsgi_port: Optional[int] = None,
            proxy: Optional[dict[str, str]] = None,
            back_forward_gestures: bool = False,
            clipboard: bool = True,
            https_scheme: bool = True,
            context_menus: bool = True,
            download_started_handler: Optional[Callable[[str, str], Union[bool, str]]] = None,
            download_completed_handler: Optional[Callable[[str, Optional[str], bool], None]] = None,
            autoplay: bool = False,
            javascript_enabled: bool = True,
            hotkeys_zoom: bool = True,
            initialization_script: Union[str, None] = _NOT_GIVEN,
            drag_drop_handler: Optional[Callable[[DragDropEvent, list, tuple], bool]] = None,
            fullscreen_handler: Optional[Callable[[bool], None]] = _NOT_GIVEN,
            native_child: bool = False,
            parent: Optional[QWidget] = None,
    ):
        """
        Cross-platform webview widget for Qt, powered by wryview (wry).

        :param url: The initial URL to load.
        :param user_agent: Custom User-Agent string.
        :param debug: Enable DevTools (right-click → Inspect, F12).
        :param transparent: Enable transparent background mode.
        :param background_color: Background color as hex string (e.g. "#1e1e1e").
        :param navigation_handler: Callable(url) → bool. Return False to block navigation.
        :param new_window_handler: Callable(url) → :class:`NewWindowResponse`.
        :param lazyload: Defer WebView creation to showEvent. Window appears instantly,
            WebView loads after. Enabled by default.
        :param js_apis: Dict or DictJsBridge exposing Python functions to JavaScript
            via ``window.qtwebview.api``.
        :param user_data_folder: Path for persistent WebView2 user data (cache, cookies).
            Defaults to ``QStandardPaths.AppLocalDataLocation/QtWebView/``.
            Pass ``None`` to skip setting a data directory — the browser engine may
            still use its own default storage location.
        :param incognito: Enable browser incognito / private mode.
            Windows: requires WebView2 Runtime ≥ 101.0.1210.39.
        :param wsgi_app: A WSGI-compatible app (Flask, Bottle, Django, etc.). Requests
            are served via custom protocol (default scheme ``qtwebview://``) or localhost
            TCP if ``wsgi_scheme="localhost"``.
        :param wsgi_scheme: Custom protocol scheme for WSGI. Default ``"qtwebview"``.
            Use ``"localhost"`` to switch to a TCP server on 127.0.0.1 with auto port.
            **Windows:** custom protocol URL conversion only applies at construction
            time (via ``with_url``); subsequent ``load_url()`` calls do **not**
            automatically convert custom scheme URLs.  Use *url* or *html* instead.
        :param wsgi_executor: If provided a thread pool executor,
            the WSGI App will be executed in the provided executor.
            If provided a number, the WSGI App will be executed in a thread pool executor
            with the specified number of threads. defaults to 8
        :param wsgi_port: TCP port for localhost WSGI mode.  Default ``None``
            (auto-assign).  Only used when ``wsgi_scheme="localhost"``.
        :param proxy: Proxy configuration dict: ``{"type": "http"|"socks5",
            "host": "...", "port": "..."}``.
        :param back_forward_gestures: Enable two-finger swipe for back/forward
            navigation.  Default False.
        :param clipboard: Enable clipboard access (Windows/Linux).
            macOS always enabled, regardless of this setting.  Default True.
        :param https_scheme: Use ``https://`` instead of ``http://`` for custom
            protocol pages (Windows only).  Makes the page a secure context,
            enabling Service Workers, Geolocation, Web Crypto, etc.
            Default ``True``.
        :param context_menus: Enable native right-click context menu.
            Windows only.  Default ``True``.
        :param download_started_handler: Callable(url, suggested_path) → bool|str.
            Return ``False`` to cancel download, or a new path to redirect.
        :param download_completed_handler: Callable(url, saved_path, success: bool).
            *saved_path* is ``None`` if cancelled.
        :param autoplay: Allow autoplay of media. Default False.
        :param javascript_enabled: Enable JavaScript. Default True.
        :param hotkeys_zoom: Enable Ctrl+/- zoom.  Windows only.
        :param initialization_script: JavaScript injected on every page load
            (before any page scripts run).  Default: injects the JS bridge and
            optionally fullscreen support.  Pass ``None`` to skip entirely, or
            a string to inject custom JavaScript.  Setting this to ``None`` or a
            custom string will break the JS bridge and fullscreen handling; you
            must re-implement them manually if needed.
        :param drag_drop_handler: Callable(:class:`DragDropEvent`, paths, position) → bool.
        :param native_child: If True, embed the WebView directly as a native child
            window instead of using an independent anchor window. Simpler,
            but the WebView dies with the parent HWND — avoid for system-tray apps.
            Default False.
        :param fullscreen_handler: Callable(enter: bool) → None. Called when the page
            requests fullscreen (enter=True) or exit fullscreen (enter=False) via the
            JavaScript Fullscreen API.  The default implementation (anchor mode only)
            reparents the webview container into a dedicated fullscreen ``QWidget``.
            In native-child mode the default is a no-op.  Pass ``None`` to disable
            fullscreen interception entirely.
        :param parent: Parent Qt widget.
        """
        super().__init__(parent)

        # ── Config ──
        self._url = url
        self._user_agent = user_agent
        self._debug = debug
        self._transparent = transparent
        self._bg_color = background_color
        self._wsgi_app = wsgi_app
        self._wsgi_scheme = wsgi_scheme

        if isinstance(wsgi_executor, int):
            self._wsgi_executor = concurrent.futures.ThreadPoolExecutor(max_workers=wsgi_executor)
        elif isinstance(wsgi_executor, concurrent.futures.Executor):
            self._wsgi_executor = wsgi_executor
        else:
            raise TypeError("The wsgi_executor parameter must be a thread pool executor or an integer")

        self._wsgi_port = wsgi_port
        self._html = html
        self._headers = headers
        self._lazyload = lazyload
        self._user_data_folder = user_data_folder
        self._incognito = incognito
        self._proxy = proxy
        self._back_forward = back_forward_gestures
        self._clipboard = clipboard
        self._https_scheme = https_scheme
        self._context_menus = context_menus
        self._download_started_handler = download_started_handler
        self._download_completed_handler = download_completed_handler
        self._autoplay = autoplay
        self._javascript_enabled = javascript_enabled
        self._hotkeys_zoom = hotkeys_zoom
        self._init_script = initialization_script
        self._drag_drop_handler = drag_drop_handler
        self._native_child = native_child
        self._fullscreen_handler = (
            self._default_fullscreen_handler
            if fullscreen_handler is _NOT_GIVEN
            else fullscreen_handler
        )
        self.navigation_handler = navigation_handler
        self.new_window_handler = new_window_handler

        # JS bridge
        if isinstance(js_apis, dict) or js_apis is None:
            self.js_api: QtWebViewJsBridge = DictJsBridge(js_apis)
        elif isinstance(js_apis, QtWebViewJsBridge):
            self.js_api: QtWebViewJsBridge = js_apis
        else:
            raise TypeError("js_apis must be a dict or DictJsBridge")

        self.bridge = self.signals = QtWebViewSignals(self)

        # ── Create webview (deferred to thread for fast startup) ──
        self._webview: Optional[WebView] = None
        self._anchor: Optional[_AnchorWindow] = None
        self._fs_window: Optional[QWidget] = None
        self._pending_calls: list[tuple[str, tuple, dict]] = []

        if not self._lazyload:
            self._start_webview()

    # ── WebView creation ────────────────────────────────────────────────────

    def _start_webview(self):
        if sys.platform == "linux":
            raise RuntimeError(
                "QtWebView is not yet supported on Linux — PRs welcome! "
                "The underlying wryview library compiles on Linux, but the "
                "Qt xcb ↔ wry Xlib bridge has unresolved issues. "
                "Linux users can run the app under Wine instead."
            )
        logger.info("[%s] creating WebView mode=%s platform=%s",
                    self, "native_child" if self._native_child else "anchor", sys.platform)
        if self._native_child:
            self._start_webview_native()
        else:
            self._start_webview_anchor()

    def _start_webview_anchor(self):
        """Create independent transparent QWidget, embed via fromWinId + container."""
        self._anchor = _AnchorWindow()
        hwnd = int(self._anchor.winId())
        view = QWindow.fromWinId(hwnd)
        self._container = QWidget.createWindowContainer(view, self)
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._container)

        if self.isVisible():
            self._anchor.show()

        self._webview = self._make_webview(hwnd)
        self._flush_pending()

    def _start_webview_native(self):
        """Embed WebView directly as a native child of this widget."""
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        hwnd = int(self.winId())
        self._webview = self._make_webview(hwnd)
        self._flush_pending()

    def _resize_webview(self):
        """Resize the webview to fill its container (anchor or widget)."""
        if not self.is_ready:
            return
        if self._native_child:
            w = self.width()
            h = self.height()
        else:
            w = self._container.width()
            h = self._container.height()
        if w > 0 and h > 0:
            self._webview.set_bounds(0, 0, w, h)

    # ── Fullscreen ────────────────────────────────────────────────────────

    def _default_fullscreen_handler(self, enter: bool):
        """Default fullscreen: reparent container into a dedicated fullscreen window."""
        if self._native_child or self._anchor is None or self._container is None:
            return

        if enter:
            layout = self.layout()
            if layout and self._container:
                layout.removeWidget(self._container)

            self._fs_window = QWidget(
                None, Qt.WindowType.FramelessWindowHint
            )
            self._fs_window.setAutoFillBackground(True)
            fs_layout = QVBoxLayout(self._fs_window)
            fs_layout.setContentsMargins(0, 0, 0, 0)
            fs_layout.addWidget(self._container)
            self._fs_window.showFullScreen()
            self._resize_webview()
        else:
            self._fs_window.hide()
            layout = self.layout()
            if layout and self._container:
                layout.addWidget(self._container)
                self._container.show()

            self._fs_window.close()
            self._fs_window = None
            self._resize_webview()

    def _make_webview(self, hwnd: int) -> WebView:
        """Build the kwargs dict and create a WebView."""
        _t0 = time.perf_counter()
        # Build initialization script
        if self._init_script is _NOT_GIVEN:
            # Default: inject JS bridge + fullscreen (if handler is set)
            init_script = BRIDGE_SCRIPT
            if self._fullscreen_handler is not None:
                init_script += FULLSCREEN_SCRIPT
        elif self._init_script is not None:
            init_script = self._init_script
        else:
            init_script = None

        kwargs: dict = {
            "initialization_script": init_script,
            "devtools": self._debug,
            "transparent": self._transparent,
            "incognito": self._incognito,
            "autoplay": self._autoplay,
            "javascript_enabled": self._javascript_enabled,
            "hotkeys_zoom": self._hotkeys_zoom,
            "back_forward_gestures": self._back_forward,
            "clipboard": self._clipboard,
            "https_scheme": self._https_scheme,
            "default_context_menus": self._context_menus,
        }

        if self._drag_drop_handler:
            kwargs["drag_drop_handler"] = self._drag_drop_handler
        if self._download_started_handler:
            kwargs["on_download_started"] = self._download_started_handler
        if self._download_completed_handler:
            kwargs["on_download_completed"] = self._download_completed_handler

        # data_directory: three states via sentinel
        #   _NOT_GIVEN  → auto-generate (default)
        #   None        → no data directory
        #   str         → user-specified path
        if self._user_data_folder is _NOT_GIVEN:
            data_dir = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
            if data_dir:
                data_dir = f"{data_dir}/QtWebView/"
            logger.debug("[%s] WebView DataFolder: %s", self, data_dir)
            kwargs["data_directory"] = data_dir
        else:
            kwargs["data_directory"] = self._user_data_folder

        logger.debug("[%s] WebView DataFolder: %s", self, kwargs.get('data_directory'))

        if self._user_agent:
            kwargs["user_agent"] = self._user_agent

        if self._bg_color:
            c = self._bg_color.lstrip("#")
            if len(c) == 6:
                kwargs["background_color"] = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)

        if self._wsgi_app:
            if self._wsgi_scheme == "localhost":
                _wsgi_executor = self._wsgi_executor
                from wsgiref.simple_server import make_server, WSGIServer
                from socketserver import ThreadingMixIn
                import threading

                class ThreadedServer(ThreadingMixIn, WSGIServer):
                    def process_request(self, request, client_address):
                        _wsgi_executor.submit(self.process_request_thread, request, client_address)

                server = make_server("127.0.0.1", self._wsgi_port or 0, self._wsgi_app, server_class=ThreadedServer)
                self._wsgi_port = server.server_port
                threading.Thread(target=server.serve_forever, daemon=True).start()
                logger.info("[%s] WSGI on http://127.0.0.1:%d", self, self._wsgi_port)
            else:
                scheme = self._wsgi_scheme or "qtwebview"
                kwargs["custom_protocols"] = {scheme: self._wsgi_handler}
                self._wsgi_scheme = scheme
                logger.info("[%s] WSGI custom protocol: %s://", self, scheme)

        kwargs["ipc_handler"] = self._on_ipc
        kwargs["on_page_load"] = lambda evt, url: self.bridge.page_loaded.emit(evt, url)
        kwargs["on_title_changed"] = lambda title: self.bridge.title_changed.emit(title)
        kwargs["on_navigation"] = lambda url: (
            self.bridge.navigation_requested.emit(url),
            self.navigation_handler(url) if self.navigation_handler else True
        )[1]
        kwargs["on_new_window"] = lambda url: (
            self.bridge.new_window_requested.emit(url),
            self.new_window_handler(url) if self.new_window_handler else NewWindowResponse.Allow
        )[1]

        if self._proxy:
            kwargs["proxy"] = self._proxy
        if self._html:
            kwargs["html"] = self._html
        if self._url:
            kwargs["url"] = self._url
        elif self._wsgi_port:
            kwargs["url"] = f"http://127.0.0.1:{self._wsgi_port}/"
        elif self._wsgi_scheme:
            kwargs["url"] = f"{self._wsgi_scheme}://localhost/"
        if self._headers:
            kwargs["headers"] = self._headers

        # Initial size — fill the container (or widget for native mode)
        if self.width() > 0 and self.height() > 0:
            kwargs["width"] = self.width()
            kwargs["height"] = self.height()

        QTimer.singleShot(0, self._resize_webview)

        wv = WebView(hwnd, **kwargs)
        logger.debug("[%s] WebView created in %.0fms",
                     self, (time.perf_counter() - _t0) * 1000)
        return wv

    def _flush_pending(self):
        if self._pending_calls:
            logger.debug("[%s] flushing %d pending calls", self, len(self._pending_calls))
        for name, args, kwargs in self._pending_calls:
            getattr(self, name)(*args, **kwargs)
        self._pending_calls.clear()

        self.signals.initialization_done.emit()

    # ── IPC ──────────────────────────────────────────────────────────────────

    def _on_ipc(self, msg: str):
        self.signals.web_message_received.emit(msg)
        try:
            data = json.loads(msg)
            # Only process internal bridge calls
            if data.get("type") != "qtwebview":
                return

            func_name = data.get("name", "")
            params = data.get("params", [])
            call_id = data.get("id", "")

            # Fullscreen API interception
            if func_name == "__fullscreen__":
                if self._fullscreen_handler:
                    enter = bool(params[0]) if params else True
                    self._fullscreen_handler(enter)
                return

            if not func_name or not call_id:
                return

            logger.debug("[%s] JS API call: %s(%s)", self, func_name, params)
            try:
                res = self.js_api(func_name, *params)
                if callable(res):
                    res(lambda result: self._return_to_js(result, call_id))
                else:
                    self._return_to_js(res, call_id)
            except Exception as e:
                logger.error("[%s] JS API '%s' error: %s", self, func_name, e, exc_info=True)
                self._return_js_error(repr(e), call_id)
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.debug("Raw IPC: %.200s", msg)

    def _return_to_js(self, result: Any, call_id: str):
        payload = json.dumps({"id": call_id, "result": result})
        self._webview.eval_js(
            f"window.dispatchEvent(new CustomEvent('qtwebview-response', {{detail: {payload}}}));"
        )

    def _return_js_error(self, error: str, call_id: str):
        payload = json.dumps({"id": call_id, "error": error})
        self._webview.eval_js(
            f"window.dispatchEvent(new CustomEvent('qtwebview-response', {{detail: {payload}}}));"
        )

    # ── WSGI ────────────────────────────────────────────────────────────────

    def _wsgi_handler(
            self, method: str, uri: str, headers: list, body: bytes, respond: Callable,
    ):
        """wryview custom protocol → WSGI adapter (async: calls respond when done)."""
        logger.debug("[%s] WSGI %s %s", self, method, uri)
        from urllib.parse import urlparse
        parsed = urlparse(uri)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": parsed.path or "/",
            "QUERY_STRING": parsed.query or "",
            "SERVER_NAME": self._wsgi_scheme or "",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": self._wsgi_scheme or "",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": BytesIO(body),
            "wsgi.errors": BytesIO(),
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

        for k, v in headers:
            key = "HTTP_" + k.upper().replace("-", "_")
            if key not in ("HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"):
                environ[key] = v
        for k_lower, wsgi_key in (("content-type", "CONTENT_TYPE"), ("content-length", "CONTENT_LENGTH")):
            for k, v in headers:
                if k.lower() == k_lower:
                    environ[wsgi_key] = v
                    break

        def _run():
            status_info = {}

            def start_response(status, response_headers, exc_info=None):
                status_info["status"] = status
                status_info["headers"] = response_headers

            try:
                result = self._wsgi_app(environ, start_response)
            except Exception as e:
                logger.error("[%s] WSGI error: %s", self, e, exc_info=True)
                respond(500, [], b"Internal Server Error")
                return

            if "status" not in status_info:
                respond(500, [], b"WSGI app did not call start_response")
                return

            status_code = int(status_info["status"].split(" ", 1)[0])
            body_chunks = []
            for chunk in result:
                body_chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
            respond(status_code, status_info.get("headers", []), b"".join(body_chunks))

        self._wsgi_executor.submit(_run)

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """True once the underlying WebView has been created."""
        return self._webview is not None

    def load_url(self, url: str, headers: Optional[Union[dict, list]] = None):
        """Navigate to a URL."""
        if headers is None:
            headers = []
        if self.is_ready:
            if headers:
                self._webview.load_url_with_headers(url, headers)
            else:
                self._webview.load_url(url)
        else:
            self._url = url
            self._pending_calls.append(('load_url', (url, headers), {}))

    @_require_webview()
    def load_url_with_headers(self, url: str, headers: Union[dict, list]):
        """Navigate to URL with custom HTTP headers."""
        self._webview.load_url_with_headers(url, headers)

    @_require_webview()
    def load_html(self, html: str):
        """Load HTML content directly."""
        self._webview.load_html(html)

    def url(self) -> str | None:
        if self.is_ready:
            return self._webview.url()
        else:
            return self._url

    @_require_webview()
    def reload(self):
        """Reload the current page."""
        self._webview.reload()

    @_require_webview()
    def evaluate_js(self, script: str, callback: Optional[Callable[[str], None]] = None):
        """Execute JavaScript. If *callback* is given, receives the result string."""
        if callback:
            self._webview.eval_js_with_callback(script, callback)
        else:
            self._webview.eval_js(script)

    def eval_js(self, script: str, callback: Optional[Callable[[str], None]] = None):
        """
        Execute JavaScript. If *callback* is given, receives the result string.
        (convenience alias for evaluate_js)
        """
        self.evaluate_js(script, callback)

    @_require_webview()
    def open_devtools(self):
        """Open the browser DevTools window."""
        self._webview.open_devtools()

    @_require_webview()
    def close_devtools(self):
        """Close the browser DevTools window."""
        self._webview.close_devtools()

    @_require_webview(error_if_not_ready=True)
    def is_devtools_open(self) -> bool:
        """Return True if DevTools is currently open."""
        return self._webview.is_devtools_open()

    @_require_webview()
    def zoom(self, scale: float):
        """Set zoom level (1.0 = 100%)."""
        self._webview.zoom(scale)

    def showEvent(self, event):
        super().showEvent(event)
        if self._lazyload and not self.is_ready:
            QTimer.singleShot(0, self._start_webview)
        if self._fs_window:
            self._fs_window.show()
        if self._anchor:
            self._anchor.show()
        if self._webview:
            self._webview.set_visible(True)
        # If fullscreen is active and the user brings up the main window,
        # raise the fullscreen window so it doesn't get lost behind.
        if self._fs_window and self._fs_window.isVisible():
            self._fs_window.raise_()
            self._fs_window.activateWindow()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._fs_window:
            self._fs_window.hide()
        if self._anchor:
            self._anchor.hide()
        if self._webview:
            self._webview.set_visible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_webview()

    def closeEvent(self, event):
        logger.debug("[%s] closing", self)
        # Drop webview first (child HWND must outlive parent),
        # then tear down the anchor window that hosted it.
        self._webview = None
        if self._anchor:
            self._anchor.deleteLater()
            self._anchor = None
        super().closeEvent(event)

    # ── Cookie ────────────────────────────────────────────────────────

    @_require_webview()
    def set_cookie(self, name: str, value: str, domain: Optional[str] = None, path: Optional[str] = None):
        """Set a cookie."""
        self._webview.set_cookie(name, value, domain, path)

    @_require_webview(error_if_not_ready=True)
    def cookies(self) -> list:
        """Get all cookies."""
        return self._webview.cookies()

    @_require_webview(error_if_not_ready=True)
    def cookies_for_url(self, url: str) -> list:
        """Get cookies for a specific URL."""
        return self._webview.cookies_for_url(url)

    @_require_webview()
    def delete_cookie(self, name: str, url: str):
        """Delete a cookie."""
        self._webview.delete_cookie(name, url)

    # ── Misc ───────────────────────────────────────────────────────────

    @_require_webview()
    def set_background_color(self, r: int, g: int, b: int, a: int = 255):
        """Set background color after creation."""
        self._webview.set_background_color(r, g, b, a)

    @_require_webview()
    def focus(self):
        """Focus the webview."""
        self._webview.focus()

    @_require_webview()
    def print(self):
        """Print the current page."""
        self._webview.print()

    @_require_webview()
    def clear_all_browsing_data(self):
        """Clear all browsing data (cache, cookies, storage)."""
        self._webview.clear_all_browsing_data()


@deprecated("Use QtWebViewWidget instead")
class QtWebView2Widget(QtWebViewWidget):
    ...
