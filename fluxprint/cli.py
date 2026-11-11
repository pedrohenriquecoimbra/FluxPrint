"""The ``fluxprint`` command.

    fluxprint gui               # open the web tool from this install
    fluxprint gui --port 8000 --no-browser

The page's files ship inside the package, in fluxprint/web. The site data and the
wheels the page runs in the browser are built daily by scripts/build_site.py, so
the local server fetches them from the published site on first use and keeps a
copy for the rest of the session.
"""
import argparse
import http.server
import shutil
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from functools import partial
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
REMOTE = "https://pedrohenriquecoimbra.github.io/FluxPrint/"
FETCHED = ("data/", "wheels/")      # paths the build makes, not in the package


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the page from the package, and what the build makes from a cache filled from the site."""

    def __init__(self, *args, remote, cache, **kwargs):
        self.remote, self.cache = remote, cache
        super().__init__(*args, directory=str(WEB), **kwargs)

    def translate_path(self, path):
        relative = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        if relative.startswith(FETCHED) and ".." not in relative:
            target = self.cache / relative
            if not target.exists():
                self.fetch(relative, target)
            return str(target)
        return super().translate_path(path)

    def fetch(self, relative, target):
        try:
            with urllib.request.urlopen(self.remote + relative, timeout=120) as response:
                target.parent.mkdir(parents=True, exist_ok=True)
                partial_file = target.with_suffix(target.suffix + ".part")
                with open(partial_file, "wb") as out:
                    shutil.copyfileobj(response, out)
                partial_file.replace(target)
        except (urllib.error.URLError, OSError) as error:
            self.log_message("could not fetch %s: %s", relative, error)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        if self.verbose:
            super().log_message(format, *args)

    verbose = False


def gui(args):
    import tempfile
    Handler.verbose = args.verbose
    with tempfile.TemporaryDirectory(prefix="fluxprint-gui-") as cache:
        handler = partial(Handler, remote=args.remote.rstrip("/") + "/", cache=Path(cache))
        server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
        url = f"http://{args.host}:{server.server_address[1]}/"
        print(f"FluxPrint web tool at {url} (Ctrl+C to stop)", flush=True)
        if not args.no_browser:
            threading.Timer(0.5, webbrowser.open, (url,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fluxprint", description="Flux footprint models for eddy covariance data.")
    commands = parser.add_subparsers(dest="command", required=True)
    page = commands.add_parser("gui", help="open the web tool in your browser, served from this computer")
    page.add_argument("--host", default="127.0.0.1")
    page.add_argument("--port", type=int, default=0, help="0 picks a free port (default)")
    page.add_argument("--no-browser", action="store_true", help="only print the address")
    page.add_argument("--remote", default=REMOTE, help="where the site data and wheels are fetched from")
    page.add_argument("--verbose", action="store_true", help="log every request")
    page.set_defaults(run=gui)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    sys.exit(main())
