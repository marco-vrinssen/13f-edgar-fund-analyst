"""HTTP client with a declared User-Agent, per-host rate limits and a file cache."""
from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit

# SEC allows at most 10 requests per second. 0.12 s keeps every run under that.
MIN_INTERVAL = {"www.sec.gov": 0.12, "data.sec.gov": 0.12, "efts.sec.gov": 0.12}
RETRY_CODES = (429, 500, 502, 503, 504)


class HttpError(Exception):
    pass


class Client:
    def __init__(self, user_agent: str, cache_dir: Path, intervals: Optional[Dict[str, float]] = None):
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.intervals = {**MIN_INTERVAL, **(intervals or {})}
        self._last: Dict[str, float] = {}

    def get(self, url: str, ttl: Optional[float] = None) -> bytes:
        """GET with the file cache. ttl None caches forever (filings never change), 0 skips the cache."""
        path = self.cache_dir / urlsplit(url).netloc / hashlib.sha256(url.encode()).hexdigest()
        if ttl != 0 and path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
            return path.read_bytes()
        body = self.request(url)
        if ttl != 0:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        return body

    def get_json(self, url: str, ttl: Optional[float] = None):
        return json.loads(self.get(url, ttl))

    def request(self, url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
                retries: int = 4) -> bytes:
        host = urlsplit(url).netloc
        sent = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate", **(headers or {})}
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=sent), timeout=60) as resp:
                    return _decode(resp.read(), resp.headers.get("Content-Encoding", ""))
            except urllib.error.HTTPError as err:
                if err.code in RETRY_CODES and attempt < retries:
                    hint = err.headers.get("Retry-After") or err.headers.get("ratelimit-reset") or 0
                    time.sleep(min(float(hint) or 2 ** attempt * 2, 120))
                    continue
                if err.code == 403 and host.endswith("sec.gov"):
                    raise HttpError(f"SEC refused the request (403). Check that user_agent names a contact email. {url}") from err
                raise HttpError(f"HTTP {err.code} for {url}") from err
            except urllib.error.URLError as err:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise HttpError(f"Network error for {url}: {err.reason}") from err
        raise HttpError(f"Gave up on {url}")

    def _wait(self, host: str) -> None:
        gap = self.intervals.get(host, 0)
        last = self._last.get(host)
        if last is not None and gap:
            delay = last + gap - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        self._last[host] = time.monotonic()


def _decode(body: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    return body
