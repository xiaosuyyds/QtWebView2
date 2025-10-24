# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

class QtWebviewException(Exception):
    """Base class for exceptions in this module."""
    pass


class WebviewInitException(QtWebviewException):
    """Exception raised when the webview cannot be initialized."""
    pass


class WebView2RuntimeExceptionNotFound(WebviewInitException):
    """
    Exception raised specifically when the WebView2 Runtime is likely missing.
    """

    def __init__(self, message, user_message, download_url):
        """
        :param message: A detailed error message intended for developers or logs.
        :param user_message: A user-friendly message that the application can display.
        :param download_url: The official URL to download the WebView2 Runtime.
        """
        super().__init__(message)
        self.user_message = user_message
        self.download_url = download_url
