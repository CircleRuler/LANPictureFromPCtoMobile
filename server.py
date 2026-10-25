# -*- coding: utf-8 -*-
"""
局域网传图 (LanShare)
--------------------------------------------------
电脑上把图片拖进网页 -> 手机浏览器立刻就能看到、保存。
纯 Python 标准库实现，不需要 pip 安装任何东西。
"""
import http.server
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser

PORT_START = 8848
PORT_MAX = 8900

# 打包成 exe 后：资源在临时解包目录，可写数据（uploads）留在 exe 旁边
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = APP_DIR

BASE = APP_DIR
STATIC = os.path.join(RES_DIR, "static")
UPLOAD = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOAD, exist_ok=True)


# ----------------------------------------------------------------- 工具函数
def lan_ips():
    """尽量找出本机所有可被手机访问的局域网 IPv4。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        try:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    ips = [i for i in ips if not i.startswith("127.") and not i.startswith("169.254.")]
    return ips or ["127.0.0.1"]


BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')


def safe_name(name):
    name = os.path.basename((name or "").replace("\\", "/"))
    name = BAD_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "file"
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        name = stem[:100] + ext[:19]
    return name


def unique_name(name):
    stem, ext = os.path.splitext(name)
    cand, i = name, 1
    while os.path.exists(os.path.join(UPLOAD, cand)):
        i += 1
        cand = "%s (%d)%s" % (stem, i, ext)
    return cand


def list_files():
    out = []
    try:
        for n in os.listdir(UPLOAD):
            if n.startswith("."):
                continue
            p = os.path.join(UPLOAD, n)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({
                "name": n,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "url": "/f/" + urllib.parse.quote(n),
            })
    except Exception:
        pass
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


# ----------------------------------------------------------------- HTTP 处理
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "LanShare/1.0"
    protocol_version = "HTTP/1.1"
    # keep-alive 空闲超时：手机端关页面后连接可能半开着，避免线程一直挂着
    timeout = 60

    NET_ERRORS = (ConnectionResetError, ConnectionAbortedError,
                  BrokenPipeError, TimeoutError)

    def handle_one_request(self):
        """手机浏览器经常在页面加载完就重置连接，这不是错误，静默关掉即可。"""
        try:
            super().handle_one_request()
        except OSError:          # ConnectionReset / BrokenPipe 都是 OSError 子类
            self.close_connection = True

    def handle_error(self, request, client_address):
        """兜底：网络层的断连不打印 traceback，其他异常照常暴露。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, self.NET_ERRORS):
            self.close_connection = True
            return
        super().handle_error(request, client_address)

    def log_message(self, fmt, *args):
        # 手机端每 2 秒轮询一次列表，不刷屏；其余请求记一行，方便确认手机连上了
        try:
            msg = fmt % args
        except Exception:
            return
        if "/api/list" in msg:
            return
        try:
            print("  [%s] %s" % (self.client_address[0], msg), flush=True)
        except Exception:
            pass

    # ---------- 基础响应
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _serve_static(self, filename, ctype=None):
        path = os.path.join(STATIC, filename)
        if not os.path.isfile(path):
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        with open(path, "rb") as f:
            data = f.read()
        ctype = ctype or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    # ---------- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._serve_static("desktop.html")
        if path in ("/m", "/m.html"):
            return self._serve_static("mobile.html")
        if path.startswith("/static/"):
            return self._serve_static(safe_name(path[8:]))

        if path == "/api/info":
            return self._json({
                "ips": lan_ips(),
                "port": self.server.server_address[1],
                "count": len(list_files()),
                "dir": UPLOAD,
            })
        if path == "/api/list":
            return self._json({"files": list_files()})

        if path.startswith("/f/"):
            raw = urllib.parse.unquote(path[3:])
            name = safe_name(raw)
            fpath = os.path.join(UPLOAD, name)
            if not os.path.isfile(fpath):
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            return self._serve_file(fpath)

        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")

        self._send(404, "Not found", "text/plain; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def _serve_file(self, path):
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            m = re.match(r"(\d*)-(\d*)", rng[6:].strip())
            if m:
                g1, g2 = m.group(1), m.group(2)
                if g1:
                    start = int(g1)
                    if g2:
                        end = min(int(g2), size - 1)
                elif g2:
                    start = max(0, size - int(g2))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206

        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- PUT（上传）
    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/upload":
            return self._json({"ok": False, "error": "bad path"}, 404)

        qs = urllib.parse.parse_qs(parsed.query)
        raw = qs.get("name", ["file"])[0]
        name = safe_name(raw)
        final = unique_name(name)
        dest = os.path.join(UPLOAD, final)
        tmp = os.path.join(UPLOAD, ".part-" + final)

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0

        if length <= 0:
            return self._json({"ok": False, "error": "empty body"}, 400)

        got = 0
        try:
            with open(tmp, "wb") as f:
                while got < length:
                    chunk = self.rfile.read(min(262144, length - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
            if got != length:
                raise IOError("incomplete upload %d/%d" % (got, length))
            os.replace(tmp, dest)
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return self._json({"ok": False, "error": str(e)}, 500)

        self._json({"ok": True, "name": final, "size": got})

    # ---------- DELETE
    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/delete":
            return self._json({"ok": False, "error": "bad path"}, 404)
        qs = urllib.parse.parse_qs(parsed.query)
        name = safe_name(qs.get("name", [""])[0])
        fpath = os.path.join(UPLOAD, name)
        if os.path.isfile(fpath):
            try:
                os.remove(fpath)
            except OSError as e:
                return self._json({"ok": False, "error": str(e)}, 500)
            return self._json({"ok": True})
        self._json({"ok": False, "error": "not found"}, 404)

    # ---------- POST（清空）
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                self.rfile.read(length)
            except Exception:
                pass
        if parsed.path == "/api/clear":
            n = 0
            for f in list_files():
                try:
                    os.remove(os.path.join(UPLOAD, f["name"]))
                    n += 1
                except OSError:
                    pass
            return self._json({"ok": True, "removed": n})
        self._json({"ok": False, "error": "bad path"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Allow", "GET, HEAD, PUT, POST, DELETE, OPTIONS")
        self.end_headers()


# ----------------------------------------------------------------- 主程序
class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def pick_port():
    for p in range(PORT_START, PORT_MAX):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", p))
            s.close()
            return p
        except OSError:
            continue
    return PORT_START


def set_console_title(text):
    """通过 Windows API 设置窗口标题，绕开 bat 文件的代码页限制。"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception:
        pass


def already_running(port):
    """探测该端口上是不是已经跑着一个本程序（避免双击两次起两个实例）。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/api/info" % port, timeout=0.6) as r:
            data = json.loads(r.read().decode("utf-8"))
        return isinstance(data, dict) and "ips" in data and "port" in data
    except Exception:
        return False


def main():
    set_console_title("局域网传图")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if already_running(PORT_START):
        print("  程序已经在运行了，正在打开浏览器…")
        webbrowser.open("http://localhost:%d" % PORT_START)
        time.sleep(1.2)
        return

    port = pick_port()
    httpd = Server(("0.0.0.0", port), Handler)
    ips = lan_ips()

    line = "=" * 56
    print(line)
    print("  局域网传图  ·  已启动")
    print(line)
    print("  电脑端（拖拽上传）: http://localhost:%d" % port)
    print("  手机端（接收图片）:")
    for ip in ips:
        print("      http://%s:%d/m" % (ip, port))
    print("  保存目录: %s" % UPLOAD)
    print(line)
    print("  提示: 手机需连同一个 WiFi；首次运行若弹出 Windows 防火墙")
    print("        提示，请勾选「专用网络」并允许访问。")
    print("  按 Ctrl+C 停止服务。")
    print(line)

    threading.Timer(0.8, lambda: webbrowser.open("http://localhost:%d" % port)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
