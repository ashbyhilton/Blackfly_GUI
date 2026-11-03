"""Ask GitHub Releases whether a newer version of the app has been published.

No Qt here, so it can be tested without a GUI; ui/updates.py runs it on a
background thread. Only the latest *published* release counts: drafts and
releases marked "pre-release" are ignored by GitHub's /releases/latest.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__

REPO = "ashbyhilton/Blackfly_GUI"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"


class UpdateError(Exception):
    """The check could not be completed (offline, rate limited, bad reply)."""


@dataclass(frozen=True)
class Release:
    version: str               # "0.2.0" (tag without the leading "v")
    page_url: str              # release page on GitHub
    download_url: str | None   # the Windows zip, if one is attached
    notes: str                 # release description (Markdown)


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2). Anything after the numbers is ignored."""
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", text)
    if not m:
        raise ValueError(f"not a version number: {text!r}")
    return tuple(int(part) for part in m.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))  # so 0.2 == 0.2.0
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def parse_release(data: object) -> Release:
    """Pick out what we need from GitHub's JSON for one release."""
    try:
        tag = data["tag_name"]  # type: ignore[index]
        parse_version(tag)
        assets = data.get("assets") or []  # type: ignore[union-attr]
        zips = [a["browser_download_url"] for a in assets if a["name"].lower().endswith(".zip")]
        win = [u for u in zips if u.lower().endswith("win64.zip")]
        return Release(
            version=tag.strip().lstrip("vV"),
            page_url=data.get("html_url") or RELEASES_PAGE,  # type: ignore[union-attr]
            download_url=(win or zips or [None])[0],
            notes=(data.get("body") or "").strip(),  # type: ignore[union-attr]
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise UpdateError(f"Unexpected reply from GitHub ({exc})") from None


def fetch_latest(timeout: float = 10.0) -> Release:
    request = urllib.request.Request(API_LATEST, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"BlackflyGUI/{__version__}",  # GitHub rejects requests without one
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.load(reply)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No release has been published yet.") from None
        if exc.code in (403, 429):
            raise UpdateError("GitHub is limiting requests from this network. "
                              "Try again later.") from None
        raise UpdateError(f"GitHub replied with HTTP error {exc.code}.") from None
    except urllib.error.URLError as exc:
        raise UpdateError(f"Could not reach GitHub ({exc.reason}). "
                          "Is this computer online?") from None
    except (OSError, ValueError) as exc:  # timeout, connection reset, bad JSON
        raise UpdateError(f"Could not reach GitHub ({exc}).") from None
    return parse_release(data)
