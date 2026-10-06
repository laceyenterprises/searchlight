from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import subprocess
import threading
import time

import pytest

from sew.gap.calibrate import _capture_source


@pytest.fixture
def capture_worker(monkeypatch, tmp_path):
    """Inject offline transport in the real child, keeping the deadline runner."""
    original_run = subprocess.run
    pid_file = tmp_path / "capture.pid"

    def configure(prelude, timeout=30):
        def run(command, **kwargs):
            assert kwargs["timeout"] == 30 and kwargs["check"] is True
            command = list(command)
            command[2] = (
                "import sys, os; from pathlib import Path; "
                "sys.path.insert(0, sys.argv[1]); "
                f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                "import sew.gap.calibrate as capture; " + prelude + "; " + command[2]
            )
            return original_run(command, **{**kwargs, "timeout": timeout})

        monkeypatch.setattr("sew.gap.calibrate.subprocess.run", run)

    return configure, pid_file


@pytest.mark.parametrize("phase", ["dns", "headers", "body", "chunked"])
def test_absolute_deadline_kills_and_reaps_trickling_capture(capture_worker, phase):
    configure, pid_file = capture_worker
    started = threading.Event()

    class Trickle(BaseHTTPRequestHandler):
        def do_GET(self):
            started.set()
            try:
                if phase == "headers":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                else:
                    self.send_response(200)
                    self.send_header(
                        "Transfer-Encoding" if phase == "chunked" else "Content-Length",
                        "chunked" if phase == "chunked" else "100",
                    )
                    self.end_headers()
                    if phase == "chunked":
                        self.wfile.write(b"64\r\n")
                # Each byte arrives well inside the 0.2-second socket timeout;
                # headers, fixed bodies and chunk bodies all outlive the deadline.
                for _ in range(100):
                    self.wfile.write(b"x")
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Trickle) as server:
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        serving.start()
        try:
            if phase == "dns":
                prelude = "import socket, time; socket.getaddrinfo = lambda *a, **k: time.sleep(10)"
            else:
                local_url = f"http://127.0.0.1:{server.server_port}/"
                prelude = (
                    "import urllib.request; "
                    "capture._safe_reachability_target = lambda url: object(); "
                    "capture._open_reachability_request = lambda *a: "
                    f"urllib.request.urlopen({local_url!r}, timeout=0.2)"
                )
            configure(prelude, timeout=1)
            begin = time.monotonic()
            # Source capture also runs from evaluator threads, where signal
            # alarms cannot enforce a deadline.
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pytest.raises(TimeoutError, match="30-second deadline"):
                    executor.submit(_capture_source, "https://example.org").result(timeout=3)
            assert time.monotonic() - begin < 3
            assert pid_file.exists(), "the real capture child must have started"
            if phase != "dns":
                assert started.is_set(), "the response must have reached the slow phase"
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
        finally:
            server.shutdown()
            serving.join(timeout=3)
            assert not serving.is_alive()


@pytest.mark.parametrize("size", [2_000_000, 2_000_001])
def test_capture_worker_preserves_size_and_encoding_limits(capture_worker, size):
    configure, _ = capture_worker
    configure(
        "from io import BytesIO; from email.message import Message; "
        f"response = BytesIO(b'\\xe9' * {size}); response.headers = Message(); "
        "response.headers['Content-Type'] = 'text/plain; charset=iso-8859-1'; "
        "capture._safe_reachability_target = lambda url: object(); "
        "capture._open_reachability_request = lambda *a: response"
    )
    if size > 2_000_000:
        with pytest.raises(ValueError, match="source exceeds capture limit"):
            _capture_source("https://example.org")
    else:
        assert _capture_source("https://example.org") == "é" * size


def test_capture_worker_preserves_url_rejection():
    with pytest.raises(ValueError, match="HTTPS URL without credentials"):
        _capture_source("http://example.org")


@pytest.mark.parametrize(
    "stderr",
    [
        b"",
        b"  ",
        b"Traceback:\n  capture worker frame\nValueError: invalid source\n",
        b"context\ninvalid byte: \xff\n",
    ],
)
def test_capture_failure_preserves_complete_diagnostics(monkeypatch, stderr):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=stderr)

    monkeypatch.setattr("sew.gap.calibrate.subprocess.run", fail)
    with pytest.raises(ValueError) as failure:
        _capture_source("https://example.org")
    detail = stderr.decode("utf-8", errors="replace").strip()
    assert str(failure.value) == f"source capture failed: {detail or 'worker failed'}"
    assert isinstance(failure.value.__cause__, subprocess.CalledProcessError)


class _Response:
    def __init__(self, body, content_type):
        from email.message import Message

        self.body = body.encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _redirect(url, location, code=302):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    headers = Message()
    if location is not None:
        headers["Location"] = location
    return HTTPError(url, code, "redirect", headers, BytesIO())


@pytest.fixture
def transport(monkeypatch):
    """Serve canned responses in-process; record every hop and its address check."""
    import sew.gap.calibrate as capture

    routes, opened, checked = {}, [], []

    def target(url):
        checked.append(url)
        return None if "private" in url else object()

    def open_request(request, target, timeout):
        opened.append(request.full_url)
        result = routes[request.full_url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(capture, "_safe_reachability_target", target)
    monkeypatch.setattr(capture, "_open_reachability_request", open_request)
    return routes, opened, checked


def test_html_source_is_captured_as_readable_text(transport):
    from sew.gap.calibrate import _capture_source_direct

    routes, _, _ = transport
    routes["https://docs.example.org/a"] = _Response(
        "<!DOCTYPE html><html><head><title>t</title><style>.x{}</style>"
        "<script>var token = 1;</script></head><body><nav>Home | Docs</nav>"
        "<h1>Transfer</h1><p>Owners can&nbsp;transfer <b>repositories</b>.</p>"
        '<footer role="contentinfo">Legal</footer></body></html>',
        "text/html; charset=utf-8",
    )
    assert _capture_source_direct("https://docs.example.org/a") == (
        "Transfer\nOwners can transfer repositories."
    )


def test_plain_text_source_is_unchanged(transport):
    from sew.gap.calibrate import _capture_source_direct

    routes, _, _ = transport
    routes["https://example.org/notes.txt"] = _Response("<p>literal</p>\n", "text/plain")
    assert _capture_source_direct("https://example.org/notes.txt") == "<p>literal</p>\n"


def test_redirects_are_followed_and_every_hop_is_rechecked(transport):
    from sew.gap.calibrate import _capture_source_direct

    routes, opened, checked = transport
    start = "https://docs.example.org/enterprise@latest/page"
    routes[start] = _redirect(start, "/enterprise@3.22/page", 302)
    routes["https://docs.example.org/enterprise@3.22/page"] = _Response("moved here", "text/plain")
    assert _capture_source_direct(start) == "moved here"
    assert opened == checked == [start, "https://docs.example.org/enterprise@3.22/page"]


@pytest.mark.parametrize(
    "location,match",
    [
        ("http://docs.example.org/plain", "HTTPS URL without credentials"),
        ("https://user:pw@docs.example.org/x", "HTTPS URL without credentials"),
        ("https://private.example.org/x", "public addresses"),
    ],
)
def test_redirect_hops_keep_the_url_and_address_rules(transport, location, match):
    from sew.gap.calibrate import _capture_source_direct

    routes, opened, _ = transport
    routes["https://docs.example.org/a"] = _redirect("https://docs.example.org/a", location)
    with pytest.raises(ValueError, match=match):
        _capture_source_direct("https://docs.example.org/a")
    assert opened == ["https://docs.example.org/a"]


def test_redirect_chains_stop_at_the_limit(transport):
    from sew.gap.calibrate import MAX_SOURCE_REDIRECTS, _capture_source_direct

    routes, opened, _ = transport
    for i in range(MAX_SOURCE_REDIRECTS + 2):
        url = f"https://example.org/{i}"
        routes[url] = _redirect(url, f"/{i + 1}", 301)
    with pytest.raises(ValueError, match="redirects"):
        _capture_source_direct("https://example.org/0")
    assert len(opened) == MAX_SOURCE_REDIRECTS + 1


@pytest.mark.parametrize("code,location", [(404, None), (302, None), (500, "/elsewhere")])
def test_errors_and_redirects_without_location_still_fail(transport, code, location):
    from urllib.error import HTTPError

    from sew.gap.calibrate import _capture_source_direct

    routes, opened, _ = transport
    routes["https://example.org/a"] = _redirect("https://example.org/a", location, code)
    with pytest.raises(HTTPError):
        _capture_source_direct("https://example.org/a")
    assert opened == ["https://example.org/a"]


@pytest.mark.parametrize(
    "content_type,body,converted",
    [
        ("text/plain", "<!DOCTYPE html><html><body><p>raw</p></body></html>", False),
        ("text/html", "<p>tagged</p>", True),
        (None, "<!doctype html><html><body><p>sniffed</p></body></html>", True),
        (None, "plain words", False),
    ],
)
def test_a_declared_content_type_decides_and_only_a_missing_one_is_sniffed(
    transport, content_type, body, converted
):
    from sew.gap.calibrate import _capture_source_direct

    routes, _, _ = transport
    response = _Response(body, content_type or "text/plain")
    if content_type is None:
        del response.headers["Content-Type"]
    routes["https://example.org/page"] = response
    text = _capture_source_direct("https://example.org/page")
    assert (text != body) is converted
    if converted:
        assert "<" not in text


class _EncodedResponse(_Response):
    def __init__(self, raw, encoding, content_type="text/html; charset=utf-8"):
        super().__init__("", content_type)
        self.body = raw
        self.headers["Content-Encoding"] = encoding


@pytest.mark.parametrize("encoding", ["gzip", "x-gzip", "deflate", "raw-deflate"])
def test_compressed_sources_are_decoded_before_reading(transport, encoding):
    # www.python.org sends gzip even to "Accept-Encoding: identity".
    import gzip
    import zlib

    from sew.gap.calibrate import _capture_source_direct

    page = b"<!doctype html><html><body><p>Python 3.10.21 fixes tarfile.</p></body></html>"
    if encoding == "raw-deflate":
        compressor = zlib.compressobj(wbits=-15)
        raw, header = compressor.compress(page) + compressor.flush(), "deflate"
    elif encoding == "deflate":
        raw, header = zlib.compress(page), "deflate"
    else:
        raw, header = gzip.compress(page), encoding
    routes, _, _ = transport
    routes["https://www.python.org/r"] = _EncodedResponse(raw, header)
    assert _capture_source_direct("https://www.python.org/r") == "Python 3.10.21 fixes tarfile."


def test_decoded_sources_keep_the_capture_limit(transport):
    import gzip

    from sew.gap.calibrate import SOURCE_CAPTURE_LIMIT, _capture_source_direct

    routes, _, _ = transport
    bomb = gzip.compress(b"a" * (SOURCE_CAPTURE_LIMIT + 1))
    routes["https://example.org/bomb"] = _EncodedResponse(bomb, "gzip", "text/plain")
    with pytest.raises(ValueError, match="capture limit"):
        _capture_source_direct("https://example.org/bomb")


@pytest.mark.parametrize("encoding,match", [("br", "unsupported"), ("gzip", "corrupt")])
def test_unknown_or_corrupt_encodings_fail_capture(transport, encoding, match):
    from sew.gap.calibrate import _capture_source_direct

    routes, _, _ = transport
    routes["https://example.org/x"] = _EncodedResponse(b"not compressed", encoding)
    with pytest.raises(ValueError, match=match):
        _capture_source_direct("https://example.org/x")


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw-deflate"])
@pytest.mark.parametrize("suffix", ["truncated", "garbage", "member"])
def test_incomplete_or_additional_compressed_data_is_rejected(transport, encoding, suffix):
    import gzip
    import zlib

    from sew.gap.calibrate import _capture_source_direct

    page = b"Plan costs $7. Only with annual billing."
    if encoding == "gzip":
        raw = gzip.compress(page)
    elif encoding == "deflate":
        raw = zlib.compress(page)
    else:
        compressor = zlib.compressobj(wbits=-15)
        raw = compressor.compress(page) + compressor.flush()
    if suffix == "truncated":
        raw = raw[:-1]
        match = "incomplete"
    else:
        raw += gzip.compress(b"Monthly costs $10.") if suffix == "member" else b"garbage"
        match = "trailing data"
    routes, _, _ = transport
    routes["https://example.org/price"] = _EncodedResponse(
        raw, "gzip" if encoding == "gzip" else "deflate", "text/plain"
    )
    with pytest.raises(ValueError, match=match):
        _capture_source_direct("https://example.org/price")


@pytest.mark.parametrize("container", ["article", "main", "section"])
def test_footer_qualifications_survive_capture(transport, container):
    from sew.gap.calibrate import _capture_source_direct

    routes, _, _ = transport
    routes["https://example.org/price"] = _Response(
        f"<{container}><p>Plan costs $7.</p>"
        "<footer>Only with annual billing; monthly costs $10.</footer>"
        f"</{container}>"
        '<footer role="contentinfo"><nav>Home | About</nav>Copyright Example</footer>',
        "text/html",
    )
    assert _capture_source_direct("https://example.org/price") == (
        "Plan costs $7.\nOnly with annual billing; monthly costs $10."
    )


@pytest.mark.parametrize("container", ["article", "main"])
def test_content_footer_is_retained_even_with_contentinfo_role(container):
    from sew.gap.source_text import readable_text

    assert (
        readable_text(
            f'<{container}><footer role="contentinfo">Only with annual billing.</footer>'
            f"</{container}>"
        )
        == "Only with annual billing."
    )


@pytest.mark.parametrize(
    "nested",
    [
        "<footer>inner</footer>",
        '<footer role="contentinfo">inner</footer>',
        "<nav><footer>inner</footer>Navigation</nav>",
    ],
)
def test_nested_footer_does_not_end_site_chrome_skip(transport, nested):
    from sew.gap.calibrate import _capture_source_direct

    routes, _, _ = transport
    routes["https://example.org/price"] = _Response(
        '<p>Plan costs $7.</p><footer role="contentinfo">A'
        f"{nested}Privacy | Cookies | Sitemap</footer>"
        "<footer>Only with annual billing.</footer>",
        "text/html",
    )
    assert _capture_source_direct("https://example.org/price") == (
        "Plan costs $7.\nOnly with annual billing."
    )
