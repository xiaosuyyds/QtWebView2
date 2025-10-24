# QtWebView2

<div align="center">
<img src="https://img.shields.io/pypi/v/qtwebview2" alt="PyPI version">
<img src="https://img.shields.io/pypi/l/qtwebview2" alt="License">
<img src="https://img.shields.io/pypi/pyversions/qtwebview2" alt="Python versions">
<img src="https://img.shields.io/pypi/dm/qtwebview2" alt="Downloads">
</div>

| English | [简体中文](https://github.com/xiaosuyyds/QtWebView2/blob/master/README_ZH.md) |

## 📖 Introduction
QtWebView2 is a Python wrapper for embedding Microsoft's WebView2 into a Qt application, complete with a powerful JS bridge. It is built upon QtPy and Python.NET.

**Disclaimer:** This project is currently in a beta stage. The API may change in future updates, but early adopters and feedback are welcome!

## ✨ Features

- 🎸 **Lightweight Integration**: Directly wraps the native WebView2 control using Python.NET, resulting in a minimal increase in your application's package size compared to solutions like `QWebEngineView`.
- 🎻 **Powerful JS Bridge**: Provides a robust JS bridge solution for seamless two-way communication between Python and JavaScript, using modern JS features like `Promise` and `async/await`.
- 🎷 **Thread-Safe**: Most API calls are carefully designed to be thread-safe, ensuring stability and safety in multi-threaded environments by leveraging Qt's signal and slot mechanism.
- 🎺 **Out-of-the-Box**: Comes with rich configuration options and robust error handling, allowing you to get started quickly with minimal setup.
- 🎼 **QtPy Support**: Built on QtPy, making it compatible with both PyQt6 and PySide6.

## ⬇️ Installation
⚠️ **Note:** This library currently supports the **Windows platform only**.

```bash
python -m pip install qtwebview2
```

Alternatively, you can install from the source:

```bash
git clone https://github.com/xiaosuyyds/QtWebView2.git
cd QtWebView2
python -m pip install .
```

**Important!** The corresponding Qt backend is not installed as a dependency. You need to install your preferred backend (e.g., PySide6 or PyQt6) yourself.

## 🧑‍💻 Usage
Here is a complete example demonstrating the core features.

```python
import sys
from qtpy.QtWidgets import QApplication, QVBoxLayout, QWidget
from qtpy.QtCore import Slot, QCoreApplication
from qtwebview2 import QtWebView2Widget, DictJsBridge

# Set an application name for the user data folder
QCoreApplication.setApplicationName("QtWebView2-Demo")

# 1. Initialize the application and window
app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle("QtWebView2 - JS Bridge Demo")
window.setGeometry(100, 100, 800, 600)
layout = QVBoxLayout(window)

# 2. Create an instance of the JS bridge
js_bridge = DictJsBridge()

# 3. Create the WebView2 widget and inject the JS bridge
webview = QtWebView2Widget(parent=window, js_apis=js_bridge)
layout.addWidget(webview)


# 4. (JS -> Python) Define a Python function and expose it to JavaScript
@js_bridge.bind_js_api_func
def get_user_os():
    """This Python function will be callable from JavaScript."""
    print(f"Python function 'get_user_os' was called from JavaScript!")
    return sys.platform


# 5. Define HTML content that includes JavaScript to call the Python function
html_content = """
<!DOCTYPE html>
<html>
<head><title>JS Bridge Test</title></head>
<body style="font-family: sans-serif; text-align: center; background-color: #f0f0f0;">
    <h1>QtWebView2 JS Bridge Demo</h1>
    <button onclick="callPython()">Click me to call Python!</button>
    <p>Result from Python: <b id="result">...</b></p>
    <script>
        async function callPython() {
            try {
                // Use async/await to call the Python function and get the result
                const os = await window.qtwebview2.api.get_user_os();
                document.getElementById('result').textContent = os;
            } catch (e) {
                document.getElementById('result').textContent = 'Error: ' + e;
            }
        }
    </script>
</body>
</html>
"""

webview.load_html(html_content)


# 6. (Python -> JS) Connect to a signal and execute JavaScript when it's emitted
@Slot()
def on_dom_loaded():
    """This function is called when the web page's DOM is fully loaded."""
    print(f"DOM content loaded. Executing JS from Python...")
    # You can also execute JavaScript from Python
    webview.evaluate_js("""(function() {
        const new_element = document.createElement('h2');
        new_element.textContent = 'Hello from Python!';
        document.body.appendChild(new_element);
    })()""")


webview.bridge.domContentLoaded.connect(on_dom_loaded)

window.show()
sys.exit(app.exec())
```

## License

Copyright (c) 2025 Xiaosu.

Distributed under the terms of the [Mozilla Public License Version 2.0](https://github.com/xiaosuyyds/QtWebView2/blob/master/LICENSE).