# QtWebView2

<div align="center">
<img src="https://img.shields.io/pypi/v/qtwebview2" alt="PyPI version">
<img src="https://img.shields.io/pypi/l/qtwebview2" alt="License">
<img src="https://img.shields.io/pypi/pyversions/qtwebview2" alt="Python versions">
<img src="https://img.shields.io/pypi/dm/qtwebview2" alt="Downloads">
</div>

| [English](https://github.com/xiaosuyyds/QtWebView2/blob/master/README.md) | 简体中文 |

## 📖 介绍

QtWebView2 是一个用于将微软的 WebView2 嵌入到 Qt 应用程序中的 Python 包装器，并配备了一个功能强大的 JS 桥。它基于 QtPy 和
Python.NET 构建。

**请注意：** 本项目目前处于 Beta 阶段。API 可能会在未来的更新中发生变化，但我们非常欢迎早期使用者和反馈！

## ✨ 特性

- 🎸 **轻量级集成**: 通过 Python.NET 直接包装原生的 WebView2 控件，与 `QWebEngineView` 等方案相比，只会少量增加您应用程序的打包体积。
- 🎻 **强大的 JS 桥**: 提供了一个健壮的 JS 桥接方案，使用 `Promise` 和 `async/await` 等现代 JS 特性，以实现 Python 和
  JavaScript 之间的无缝双向通信。
- 🎷 **线程安全**: 大部分 API 调用都经过精心设计，通过利用 Qt 的信号和槽机制来确保线程安全，保证在多线程环境下的稳定性和安全性。
- 🎺 **开箱即用**: 提供了丰富的配置选项和稳健的错误处理，让您可以用最少的配置快速上手。
- 🎼 **QtPy 支持**: 基于 QtPy 构建，使其同时兼容 PyQt6 和 PySide6。

## ⬇️ 安装

⚠️ **注意：** 本库目前**仅支持 Windows 平台**。

```bash
python -m pip install qtwebview2
```

或者，您也可以从源码安装：

```bash
git clone https://github.com/xiaosuyyds/QtWebView2.git
cd QtWebView2
python -m pip install .
```

**重要！** 相关的 Qt 后端不会作为依赖被安装。您需要自行安装您偏好的后端（例如 PySide6 或 PyQt6）。

## 🧑‍💻 使用方法

这里是一个完整的示例，演示了其核心功能。

```python
import sys
from qtpy.QtWidgets import QApplication, QVBoxLayout, QWidget
from qtpy.QtCore import Slot, QCoreApplication
from qtwebview2 import QtWebView2Widget, DictJsBridge

# 设置一个应用名称，以便用户数据文件夹路径保持稳定
QCoreApplication.setApplicationName("QtWebView2-Demo")

# 1. 初始化应用和窗口
app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle("QtWebView2 - JS Bridge Demo")
window.setGeometry(100, 100, 800, 600)
layout = QVBoxLayout(window)

# 2. 创建 JS 桥接实例
js_bridge = DictJsBridge()

# 3. 创建 WebView2 控件并注入 JS 桥
webview = QtWebView2Widget(parent=window, js_apis=js_bridge)
layout.addWidget(webview)


# 4. (JS -> Python) 定义一个Python函数，并暴露给JavaScript
@js_bridge.bind_js_api_func
def get_user_os():
    """这个Python函数将可以从JavaScript中调用。"""
    print(f"Python函数 'get_user_os' 被JavaScript调用了！")
    return sys.platform


# 5. 定义包含调用Python的JavaScript代码的HTML内容
html_content = """
<!DOCTYPE html>
<html>
<head><title>JS Bridge Test</title></head>
<body style="font-family: sans-serif; text-align: center; background-color: #f0f0f0;">
    <h1>QtWebView2 JS Bridge Demo</h1>
    <button onclick="callPython()">点我调用Python！</button>
    <p>来自Python的结果: <b id="result">...</b></p>
    <script>
        async function callPython() {
            try {
                // 使用 async/await 调用Python函数并获取结果
                const os = await window.qtwebview2.api.get_user_os();
                document.getElementById('result').textContent = os;
            } catch (e) {
                document.getElementById('result').textContent = '错误: ' + e;
            }
        }
    </script>
</body>
</html>
"""

webview.load_html(html_content)


# 6. (Python -> JS) 连接到一个信号，并在信号触发时执行JavaScript
@Slot()
def on_dom_loaded():
    """当网页的DOM完全加载后，此函数会被调用。"""
    print(f"DOM内容已加载。正在从Python执行JS...")
    # 你也可以从Python执行JavaScript
    webview.evaluate_js("""(function() {
        const new_element = document.createElement('h2');
        new_element.textContent = '来自Python的问候！';
        document.body.appendChild(new_element);
    })()""")


# 使用 `domContentLoaded` 信号在加载时进行交互
webview.bridge.domContentLoaded.connect(on_dom_loaded)

window.show()
sys.exit(app.exec())
```

## 许可证

版权所有 (c) 2025 Xiaosu。

根据 [Mozilla Public License Version 2.0](https://github.com/xiaosuyyds/QtWebView2/blob/master/LICENSE) 的条款分发。
