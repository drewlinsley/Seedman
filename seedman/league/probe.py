"""A diagnostic crawler for a league site, to be run from your own machine.

Why this is a probe and not a finished scraper: the league site
(https://rocco-siffredi.onrender.com) is unreachable from the environment this
package was written in -- the network egress policy blocks it -- so its page
structure, field names, and any JSON endpoints are unknown.  Writing a scraper
against a guessed HTML layout would produce confident-looking code that silently
returns wrong lineups, which is the worst possible failure mode for a tool whose
whole job is deciding who to start.

So: run this from a machine that *can* reach the site.  It walks the login flow,
records every form, link and JSON payload it encounters, and writes the lot to a
directory.  From that evidence a real adapter is a short, testable piece of work.

Credentials come from the environment, never from a file in the repo:

    export SEEDMAN_LEAGUE_URL='https://rocco-siffredi.onrender.com'
    export SEEDMAN_LEAGUE_PASSWORD='...'      # the shared league password
    export SEEDMAN_LEAGUE_NAME='Drew Linsley'
    export SEEDMAN_LEAGUE_PIN='...'
    seedman league probe --out ./probe-output
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

ENV_URL = "SEEDMAN_LEAGUE_URL"
ENV_PASSWORD = "SEEDMAN_LEAGUE_PASSWORD"
ENV_NAME = "SEEDMAN_LEAGUE_NAME"
ENV_PIN = "SEEDMAN_LEAGUE_PIN"

# Redacted before anything is written to disk, so probe output can be shared.
_SECRET_ENV = (ENV_PASSWORD, ENV_PIN)


class _FormParser(HTMLParser):
    """Extracts forms, inputs, selects, links and script sources from a page."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self.links: list[str] = []
        self.scripts: list[str] = []
        self._current: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._current = {
                "action": attr.get("action", ""),
                "method": attr.get("method", "get").lower(),
                "fields": [],
            }
            self.forms.append(self._current)
        elif tag in {"input", "select", "textarea", "button"}:
            entry = {
                "tag": tag,
                "name": attr.get("name", ""),
                "type": attr.get("type", ""),
                "id": attr.get("id", ""),
                "value": attr.get("value", ""),
            }
            if self._current is not None:
                self._current["fields"].append(entry)
            else:
                # Single-page apps often render inputs outside any <form>.
                self.forms.append({"action": "", "method": "orphan", "fields": [entry]})
        elif tag == "a" and attr.get("href"):
            self.links.append(attr["href"])
        elif tag == "script" and attr.get("src"):
            self.scripts.append(attr["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


@dataclass
class ProbeResult:
    steps: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class LeagueProbe:
    base_url: str
    password: str | None = None
    display_name: str | None = None
    pin: str | None = None
    out_dir: Path = Path("probe-output")
    timeout: int = 45

    @classmethod
    def from_env(cls, out_dir: Path | str = "probe-output") -> LeagueProbe:
        url = os.environ.get(ENV_URL)
        if not url:
            raise RuntimeError(
                f"set {ENV_URL} (and {ENV_PASSWORD}/{ENV_NAME}/{ENV_PIN}) before probing"
            )
        return cls(
            base_url=url.rstrip("/"),
            password=os.environ.get(ENV_PASSWORD),
            display_name=os.environ.get(ENV_NAME),
            pin=os.environ.get(ENV_PIN),
            out_dir=Path(out_dir),
        )

    # ------------------------------------------------------------------
    def run(self) -> ProbeResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.headers.update({"User-Agent": "seedman-probe/0.1"})
        result = ProbeResult()

        landing = self._record(session, "GET", self.base_url, result, step="landing")
        if landing is None:
            return result

        # Walk whatever forms the landing page offers, filling any field whose
        # name hints at password / name / pin. Guessing field names is fine here
        # because we report exactly what happened rather than trusting it.
        for index, form in enumerate(landing["forms"]):
            payload = self._guess_payload(form)
            if not payload:
                continue
            action = urljoin(self.base_url + "/", form.get("action") or "")
            method = "POST" if form.get("method") in {"post", "orphan"} else "GET"
            self._record(
                session,
                method,
                action,
                result,
                step=f"form{index}",
                data=payload,
            )

        # Common API shapes worth checking for directly.
        for path in ("/api/league", "/api/state", "/api/players", "/api/me", "/state", "/data"):
            self._record(session, "GET", urljoin(self.base_url + "/", path.lstrip("/")),
                         result, step=f"probe{path.replace('/', '_')}", tolerate_errors=True)

        result.notes.append(
            "Review the saved HTML/JSON, then implement fetch_state() for this site "
            "in seedman/league/. The optimizer needs: current week, players already "
            "used, teams still alive, and (ideally) every team's weekly scores."
        )
        (self.out_dir / "probe-summary.json").write_text(
            json.dumps({"steps": result.steps, "notes": result.notes}, indent=2)
        )
        return result

    # ------------------------------------------------------------------
    def _guess_payload(self, form: dict) -> dict[str, str]:
        payload: dict[str, str] = {}
        for item in form.get("fields", []):
            name = (item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            hint = f"{name} {item.get('type', '')} {item.get('id', '')}".lower()
            if "pass" in hint and self.password:
                payload[name] = self.password
            elif "pin" in hint or "code" in hint:
                if self.pin:
                    payload[name] = self.pin
            elif any(k in hint for k in ("name", "user", "player", "member", "team")):
                if self.display_name:
                    payload[name] = self.display_name
        return payload

    def _record(
        self,
        session: requests.Session,
        method: str,
        url: str,
        result: ProbeResult,
        *,
        step: str,
        data: dict | None = None,
        tolerate_errors: bool = False,
    ) -> dict | None:
        try:
            resp = session.request(method, url, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            result.steps.append({"step": step, "url": url, "error": str(exc)})
            return None

        body = resp.text
        content_type = resp.headers.get("content-type", "")
        suffix = "json" if "json" in content_type else "html"
        path = self.out_dir / f"{step}.{suffix}"
        path.write_text(_redact(body))

        entry = {
            "step": step,
            "method": method,
            "url": url,
            "status": resp.status_code,
            "content_type": content_type,
            "saved_to": str(path),
            "sent_fields": sorted(data or {}),
        }

        if suffix == "html":
            parser = _FormParser()
            parser.feed(body)
            entry["forms"] = parser.forms
            entry["links"] = parser.links[:60]
            entry["scripts"] = parser.scripts[:30]
        else:
            try:
                entry["json_keys"] = sorted(json.loads(body).keys())
            except (ValueError, AttributeError):
                entry["json_keys"] = []

        if resp.status_code >= 400 and not tolerate_errors:
            entry["warning"] = "non-success status"

        result.steps.append(entry)
        return entry


def _redact(text: str) -> str:
    """Remove credential values so probe output is safe to share."""
    out = text
    for env_name in _SECRET_ENV:
        secret = os.environ.get(env_name)
        if secret:
            out = out.replace(secret, f"<{env_name}_REDACTED>")
    return out
