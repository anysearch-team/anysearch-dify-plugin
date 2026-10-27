"""独立原生 HTTP 服务端；避免 Windows gevent listener 生命周期影响客户端验收。"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import select
import sys
import time

mode = sys.argv[1]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.send_response(200)
        self.send_header('Content-Length', '200000')
        self.end_headers()
        self.wfile.flush()
        try:
            while True:
                if select.select([self.connection], [], [], 0.01)[0]:
                    if not self.connection.recv(4096):
                        break
                if mode == 'slow':
                    self.wfile.write(b' ')
                    self.wfile.flush()
        except OSError:
            pass
        finally:
            print(json.dumps({'accepted':True,'peer_closed':True}), flush=True)

    def log_message(self, *args):
        pass

    do_GET = do_POST


with HTTPServer(('127.0.0.1', 0), Handler) as server:
    print(server.server_port, flush=True)
    server.handle_request()
