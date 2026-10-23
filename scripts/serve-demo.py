#!/usr/bin/env python3
"""Serve the built demo directory, never the repository or Git history."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse

class Handler(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, 'Directory listing is disabled')
        return None

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18473)
    parser.add_argument('--bind', default='0.0.0.0')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1] / 'dist'
    if not (directory / 'index.html').is_file():
        parser.error('dist/index.html missing; run npm run build first')
    server = ThreadingHTTPServer((args.bind, args.port), partial(Handler, directory=str(directory)))
    print('Yanxi demo serving on port', args.port, flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
