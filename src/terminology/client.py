"""Minimal client for the Termbox MCP server (Health Samurai's FHIR terminology server).

Speaks MCP JSON-RPC over Streamable HTTP with the standard library only — no MCP
SDK dependency. The hosted server (`https://tx.health-samurai.io/mcp/sse`) answers
POSTs with plain `application/json`; an `text/event-stream` reply is also parsed,
so the client keeps working if the server switches transport style.

Exposed tools (see `tools/list`):
  find-code        fuzzy text search in one code system, fixed top-20, no paging
  lookup-code      display / designations / properties of one code
  expand-valueset  list the codes of a value set (accepts SNOMED implicit/ECL URLs)
  search-rxnorm    structured RxNorm search by properties/relationships

Every tool returns one text content block holding a FHIR resource as JSON:
ValueSet (find-code, expand-valueset), Parameters (lookup-code), or an
OperationOutcome when the code/system is unknown. This client parses that JSON
and turns "not found" OperationOutcomes into `None` / `[]`, never an exception.
Transport failures raise `TermboxError`.

Results are cached in memory per client instance (terminology content is static
during a run), and the client is thread-safe so callers can fan out queries.
"""

from __future__ import annotations

import itertools
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import config


class TermboxError(RuntimeError):
    """Transport or protocol failure talking to the Termbox MCP server."""


@dataclass
class TermboxConcept:
    """One concept as returned by find-code / expand-valueset / lookup-code."""

    system: str
    code: str
    display: str | None
    inactive: bool = False
    version: str | None = None
    designations: list[str] = field(default_factory=list)
    properties: dict[str, list] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"system": self.system, "code": self.code, "display": self.display,
             "inactive": self.inactive}
        if self.version:
            d["version"] = self.version
        return d


class TermboxClient:
    def __init__(self, url: str | None = None, timeout: float | None = None):
        self.url = url or config.TERMBOX_MCP_URL
        self.timeout = timeout or config.TERMBOX_TIMEOUT
        self._ids = itertools.count(1)
        self._cache: dict[tuple, object] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ tools
    def find_code(self, system: str, text: str) -> list[TermboxConcept]:
        """Fuzzy text search (word match on display). Up to 20 hits, unranked."""
        res = self._tool("find-code", {"system": system, "filter": text})
        return _parse_expansion(res)

    def lookup_code(self, system: str, code: str) -> TermboxConcept | None:
        """Exact lookup. Returns None when the code (or system) is unknown."""
        res = self._tool("lookup-code", {"system": system, "code": code})
        return _parse_lookup(res, system, code)

    def expand_valueset(self, url: str) -> list[TermboxConcept]:
        res = self._tool("expand-valueset", {"url": url})
        return _parse_expansion(res)

    def search_rxnorm(self, search: str | None = None, include: list | None = None,
                      exclude: list | None = None) -> list[TermboxConcept]:
        args: dict = {}
        if search:
            args["search"] = search
        if include:
            args["include"] = include
        if exclude:
            args["exclude"] = exclude
        return _parse_expansion(self._tool("search-rxnorm", args))

    # -------------------------------------------------------------- transport
    def _tool(self, name: str, arguments: dict):
        key = (name, json.dumps(arguments, sort_keys=True))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise TermboxError(f"{name} failed: {_content_text(result)[:300]}")
        text = _content_text(result)
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = {"_text": text}
        with self._lock:
            self._cache[key] = parsed
        return parsed

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            body["params"] = params
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "structure-map-agent/termbox-client",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TermboxError(f"Termbox MCP request failed ({self.url}): {exc}") from exc

        msg = _parse_sse(raw) if "text/event-stream" in ctype else json.loads(raw)
        if "error" in msg:
            raise TermboxError(f"Termbox MCP error: {msg['error']}")
        return msg.get("result", {})


# ---------------------------------------------------------------- parsing
def _parse_sse(raw: str) -> dict:
    """Return the last JSON-RPC message in an SSE stream."""
    last = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                try:
                    last = json.loads(payload)
                except json.JSONDecodeError:
                    continue
    if last is None:
        raise TermboxError("empty SSE response from Termbox MCP")
    return last


def _content_text(result: dict) -> str:
    return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def _is_outcome(res) -> bool:
    return isinstance(res, dict) and res.get("resourceType") == "OperationOutcome"


def _parse_expansion(res) -> list[TermboxConcept]:
    if not isinstance(res, dict) or _is_outcome(res):
        return []
    out = []
    for c in (res.get("expansion") or {}).get("contains", []) or []:
        if not c.get("code"):
            continue
        out.append(TermboxConcept(
            system=c.get("system", ""),
            code=c["code"],
            display=c.get("display"),
            inactive=bool(c.get("inactive")),
            version=c.get("version"),
        ))
    return out


def _parse_lookup(res, system: str, code: str) -> TermboxConcept | None:
    if not isinstance(res, dict) or res.get("resourceType") != "Parameters":
        return None
    concept = TermboxConcept(system=system, code=code, display=None)
    for p in res.get("parameter", []):
        name = p.get("name")
        if name == "display":
            concept.display = p.get("valueString")
        elif name == "system":
            concept.system = p.get("valueUri") or system
        elif name == "code":
            concept.code = p.get("valueCode") or code
        elif name == "version":
            concept.version = p.get("valueString")
        elif name == "designation":
            for part in p.get("part", []):
                if part.get("name") == "value" and part.get("valueString"):
                    concept.designations.append(part["valueString"])
        elif name == "property":
            pcode, pval = None, None
            for part in p.get("part", []):
                if part.get("name") == "code":
                    pcode = part.get("valueCode")
                elif part.get("name") == "value":
                    pval = next((v for k, v in part.items() if k.startswith("value")), None)
            if pcode:
                concept.properties.setdefault(pcode, []).append(pval)
    inactive = concept.properties.get("inactive")
    status = concept.properties.get("status")
    concept.inactive = bool(inactive and inactive[0] is True) or bool(
        status and str(status[0]).lower() in {"inactive", "retired", "deprecated"})
    return concept
