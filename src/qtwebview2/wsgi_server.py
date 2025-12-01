import itertools
import sys
import io
import threading
from typing import Callable
from urllib.parse import urlparse

from System.IO import Stream, MemoryStream
from System import Array, Byte
from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceRequestedEventArgs

from .logger import logger


class PythonGeneratorStream(Stream):
    __namespace__ = "qtwebview2.wsgi_server.PythonGeneratorStream"

    def __init__(self, generator):
        self._generator = generator
        self._buffer = b''
        self._finished = False
        self._lock = threading.Lock()

    def Read(self, buffer, offset, count):
        try:
            with self._lock:
                if self._finished and not self._buffer:
                    return 0

                while len(self._buffer) < count and not self._finished:
                    try:
                        chunk = next(self._generator)
                        if isinstance(chunk, str):
                            chunk = chunk.encode('utf-8')
                        if chunk:
                            self._buffer += chunk
                    except StopIteration:
                        self._finished = True
                        break

                bytes_to_read = min(count, len(self._buffer))

                if bytes_to_read > 0:
                    data_chunk = self._buffer[:bytes_to_read]
                    # Create .NET array from python bytes
                    net_source_array = Array[Byte](data_chunk)
                    Array.Copy(net_source_array, 0, buffer, offset, bytes_to_read)
                    self._buffer = self._buffer[bytes_to_read:]

                return bytes_to_read
        except Exception as e:
            logger.error(f"Error reading from PythonGeneratorStream: {repr(e)}", exc_info=True)
            return 0

    def Flush(self):
        pass

    def Seek(self, offset, origin):
        raise NotImplementedError("Stream is not seekable.")

    def SetLength(self, value):
        raise NotImplementedError("Stream is not writable.")

    @property
    def CanRead(self):
        return True

    @property
    def CanSeek(self):
        return False

    @property
    def CanWrite(self):
        return False

    @property
    def Length(self):
        raise NotImplementedError("Stream is not seekable.")

    @property
    def Position(self):
        raise NotImplementedError("Stream is not seekable.")

    @Position.setter
    def Position(self, value):
        raise NotImplementedError("Stream is not seekable.")

    def close(self):
        if hasattr(self._generator, 'close'):
            self._generator.close()
        super().Close()


def extract_request_data(args: CoreWebView2WebResourceRequestedEventArgs) -> dict:
    """
    [主线程运行] 从 WebView2 的 args 中提取所有必要信息转换为纯 Python 对象。
    """
    request = args.Request
    uri_str = request.Uri
    method = request.Method

    # 提取 Headers
    headers_dict = {}
    for header in request.Headers:
        headers_dict[header.Key] = header.Value

    # 提取 Body
    request_body = b''
    if request.Content is not None:
        source_stream = request.Content
        buffer = Array[Byte](4096)

        with MemoryStream() as ms:
            while True:
                bytes_read = source_stream.Read(buffer, 0, buffer.Length)
                if bytes_read == 0:
                    break
                ms.Write(buffer, 0, bytes_read)
            request_body = ms.ToArray().tobytes()

    return {
        'uri': uri_str,
        'method': method,
        'headers': headers_dict,
        'body': request_body
    }


def _build_environ(req_data: dict) -> dict:
    """ 辅助方法：构建 WSGI environ 字典 """
    uri = urlparse(req_data['uri'])
    body = req_data['body']

    environ = {
        'wsgi.version': (1, 0),
        'wsgi.url_scheme': uri.scheme,
        'wsgi.input': io.BytesIO(body),
        'wsgi.errors': sys.stderr,
        'wsgi.multithread': True,
        'wsgi.multiprocess': False,
        'wsgi.run_once': False,
        'REQUEST_METHOD': req_data['method'],
        'PATH_INFO': uri.path or '/',
        'QUERY_STRING': uri.query,
        'SERVER_NAME': uri.hostname or 'localhost',
        'SERVER_PORT': str(uri.port or (443 if uri.scheme == 'https' else 80)),
    }

    # 从请求数据填充 HTTP 头
    for key, value in req_data['headers'].items():
        key_upper = key.upper().replace('-', '_')
        if key_upper == 'CONTENT_TYPE':
            environ['CONTENT_TYPE'] = value
        elif key_upper == 'CONTENT_LENGTH':
            environ['CONTENT_LENGTH'] = value
        else:
            environ['HTTP_' + key_upper] = value

    # 确保 Content-Length 存在
    if 'CONTENT_LENGTH' not in environ:
        environ['CONTENT_LENGTH'] = str(len(body))
    if 'CONTENT_TYPE' not in environ:
        environ['CONTENT_TYPE'] = ''

    return environ


def closing_iterator_wrapper(iterable, to_close):
    """
    一个包装生成器，它在迭代完成后或被关闭时，确保 to_close.close() 被调用。
    """
    try:
        yield from iterable
    finally:
        if hasattr(to_close, 'close'):
            try:
                to_close.close()
            except Exception as e:
                logger.warning(f"Error closing WSGI app_iterator via wrapper: {e}")


class WebView2WSGIServer:
    def __init__(self, wsgi_app: Callable):
        self.wsgi_app = wsgi_app

    def process_wsgi_request(self, req_data: dict):
        environ = _build_environ(req_data)

        status = None
        headers = None
        headers_sent = False
        response_body_parts = []

        def write(data: bytes):
            if not isinstance(data, bytes):
                raise TypeError("WSGI write() argument must be bytes")
            if not headers_sent:
                raise AssertionError("write() called before start_response()")
            response_body_parts.append(data)

        def start_response(s, h, exc_info=None):
            nonlocal status, headers, headers_sent
            if exc_info:
                try:
                    if headers_sent:
                        raise exc_info[1].with_traceback(exc_info[2])
                finally:
                    exc_info = None
            elif headers is not None:
                raise AssertionError("Headers already set!")

            status = s
            headers = h
            return write

        app_iterator = None
        try:
            # 调用 WSGI 应用
            app_iterator = self.wsgi_app(environ, start_response)

            if status is None or headers is None:
                raise AssertionError("WSGI application did not call start_response()")

            headers_sent = True

            combined_iterator = itertools.chain(response_body_parts, app_iterator)

            final_iterator = closing_iterator_wrapper(combined_iterator, app_iterator)

            return status, headers, final_iterator

        except Exception as e:
            logger.error(f"WSGI application crashed: {e}", exc_info=True)
            error_status = "500 Internal Server Error"
            error_headers = [('Content-Type', 'text/plain')]
            error_body = [b"Internal Server Error"]

            if hasattr(app_iterator, 'close'):
                try:
                    app_iterator.close()
                except Exception:
                    pass

            return error_status, error_headers, iter(error_body)
