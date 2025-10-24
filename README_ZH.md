# QtWebView2

| [English](https://github.com/xiaosuyyds/QtWebView2/blob/master/README.md) | 简体中文 |


## 📖介绍
QtWebView2 是 Python 的 Qt 与 WebView2 的包装器，并提供了JS桥，它基于 QtPy 和 Python.NET。

**目前项目处于beta阶段，在未来的更新中可能更改接口，但也欢迎尝鲜**

## ✨ 特性

- 🎸 **轻量集成**: 基于 Python.NET 直接包装原生 WebView2 控件，不会大幅增加打包体积。
- 🎻 **JS桥接**: 提供一个JS桥接方案，以实现 Python 与 JavaScript 的双向通信。
- 🎷 **线程安全**: 大部分API调用都经过精心设计，确保在多线程环境下的稳定性和安全性。
- 🎺 **开箱即用**: 提供了丰富的配置选项和健壮的错误处理，无需复杂配置即可快速上手。
- 🎼 **QtPy**: 基于 QtPy，同时兼容 PyQt6 和 PySide6。

## ⬇️安装
**⚠️ 注意：本库目前仅支持 Windows 平台。**

```bash
python -m pip install qtwebview2
```

当然你也可以从源码安装

```bash
git clone https://github.com/xiaosuyyds/QtWebView2.git
python -m pip install .
```

#### 注意！不会安装对应的qt后端，你需要自己安装你想用的qt后端如 PySide6或PyQt6。

## 🧑‍💻食用方法
### 示例代码
```python
import sys
from qtpy.QtWidgets import QApplication, QVBoxLayout, QWidget
from qtpy.QtCore import Slot
from qtwebview2 import QtWebView2Widget, DictJsBridge

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
    """这个 Python 函数可以从 JavaScript 调用。"""
    print(f"Python function 'get_user_os' was called from JavaScript!")
    return sys.platform


# 5. 定义一个HTML，其中包含调用Python的JS代码
html_content = """
<!DOCTYPE html>
<html>
<head><title>JS Bridge Demo</title></head>
<body style="font-family: sans-serif; text-align: center;">
    <h1>QtWebView2 JS Bridge Demo</h1>
    <button onclick="callPython()">Click me to call Python!</button>
    <p>Result from Python: <b id="result">...</b></p>
    <script>
        async function callPython() {
            try {
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


# 6. (Python -> JS) 连接到信号并在发出信号时执行 JavaScript
@Slot()
def on_dom_loaded():
    """当网页的 DOM 完全加载时，将调用此函数。"""
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

## 许可证

版权所有 2025 Xiaosu。

根据 [Mozilla Public License Version 2.0 许可证](https://github.com/xiaosuyyds/QtWebView2/blob/master/LICENSE) 的条款分发。
