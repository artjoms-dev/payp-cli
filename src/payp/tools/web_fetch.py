"""Web fetch tool — fetch URLs and REST APIs for import into a database.

Use cases:
- Pull data from a public JSON API, parse it, bulk_insert into DB
- Download a CSV from a URL, stage it, import
- Query a REST endpoint (with auth headers) and transform the payload

Gated on the `web-scraper` skill being active.
Blocks localhost / internal IPs by default (allow_internal=True to override).
"""

from __future__ import annotations

import base64
import ipaddress
import json as json_lib
from typing import Any
from urllib.parse import urlparse

import httpx

from payp.tools.base import BaseTool, ToolResult


DEFAULT_TIMEOUT = 30
MAX_BODY_BYTES = 50_000  # first 50KB of text
MAX_BINARY_BYTES = 10_000  # first 10KB of binary (base64-encoded)
USER_AGENT = "payp-cli/0.1"

# Block internal/loopback hostnames by default
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",  # GCP metadata
}


def _is_internal_host(host: str) -> tuple[bool, str]:
    """Return (is_internal, reason) for a hostname/IP."""
    if not host:
        return True, "empty host"
    h = host.lower().strip()
    if h in _BLOCKED_HOSTNAMES:
        return True, f"blocked hostname: {h}"
    # Try parsing as IP
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback:
            return True, f"loopback IP: {h}"
        if ip.is_private:
            return True, f"private IP: {h}"
        if ip.is_link_local:
            return True, f"link-local IP: {h}"
        if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True, f"reserved/multicast IP: {h}"
    except ValueError:
        # Not an IP — hostname. Check for common internal TLDs.
        if h.endswith(".local") or h.endswith(".internal") or h.endswith(".localhost"):
            return True, f"internal TLD: {h}"
    return False, ""


class WebFetchTool(BaseTool):
    name = "fetch_url"
    description = (
        "Fetch data from a URL or REST API over HTTP(S). Use for: pulling data "
        "from public datasets, REST API endpoints, JSON feeds, or CSV files "
        "hosted online — to then import/transform into the database. "
        "Supports GET/POST with custom headers and body. Returns status, headers, "
        "and body (first 50KB for text, 10KB base64 for binary). "
        "JSON responses are auto-parsed. 30s timeout default. "
        "Blocks localhost/internal IPs unless allow_internal=True. "
        "Gated on the 'web-scraper' skill — activate via /skills first."
    )
    is_read_only = True
    is_destructive = False  # network READ, but still opt-in via skill

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The absolute URL to fetch (http:// or https://).",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST"],
                    "description": "HTTP method (default GET).",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers (dict of name→value).",
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "description": (
                        "Optional request body. String is sent as-is; "
                        "dict/object is JSON-encoded with Content-Type: application/json."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30, max 120).",
                },
                "allow_internal": {
                    "type": "boolean",
                    "description": (
                        "If true, allows fetching localhost/private IPs. "
                        "Default false — use only when explicitly requested by the user."
                    ),
                },
            },
            "required": ["url"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        # Gate on web-scraper skill being active
        skills = context.get("skills")
        if skills is not None and hasattr(skills, "is_active"):
            if not skills.is_active("web-scraper"):
                return ToolResult(
                    success=False,
                    error=(
                        "Web fetching is disabled. Activate the 'web-scraper' "
                        "skill via /skills (or invoke_skill) to enable it."
                    ),
                    summary="fetch_url blocked: skill not active",
                )

        url = (args.get("url") or "").strip()
        if not url:
            return ToolResult(success=False, error="No URL provided")

        # Validate URL scheme and host
        try:
            parsed = urlparse(url)
        except Exception as e:
            return ToolResult(success=False, error=f"Invalid URL: {e}")

        if parsed.scheme not in ("http", "https"):
            return ToolResult(
                success=False,
                error=f"Only http/https supported, got: {parsed.scheme!r}",
                summary="fetch_url blocked: bad scheme",
            )

        host = parsed.hostname or ""
        allow_internal = bool(args.get("allow_internal", False))
        if not allow_internal:
            is_internal, reason = _is_internal_host(host)
            if is_internal:
                return ToolResult(
                    success=False,
                    error=(
                        f"Refusing to fetch internal/private address ({reason}). "
                        f"Pass allow_internal=true to override."
                    ),
                    summary=f"fetch_url blocked: {reason}",
                )

        method = (args.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            return ToolResult(
                success=False, error=f"Unsupported method: {method}"
            )

        timeout = min(int(args.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT), 120)

        # Build headers (always inject User-Agent if not set)
        user_headers = args.get("headers") or {}
        if not isinstance(user_headers, dict):
            return ToolResult(success=False, error="headers must be a dict")
        headers = {str(k): str(v) for k, v in user_headers.items()}
        if not any(k.lower() == "user-agent" for k in headers):
            headers["User-Agent"] = USER_AGENT

        # Build body
        body = args.get("body")
        request_kwargs: dict[str, Any] = {"headers": headers}
        if body is not None and method == "POST":
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = str(body)

        # Execute request
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.request(method, url, **request_kwargs)
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error=f"Request timed out after {timeout}s",
                summary=f"fetch_url: timeout ({timeout}s)",
            )
        except httpx.HTTPError as e:
            return ToolResult(
                success=False,
                error=f"HTTP error: {type(e).__name__}: {e}",
                summary="fetch_url: network error",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                summary="fetch_url: failed",
            )

        # Classify response body
        content_type = resp.headers.get("content-type", "").lower()
        raw = resp.content or b""
        total_bytes = len(raw)

        body_out: Any = None
        parsed_json: Any = None
        body_encoding = "text"
        truncated = False

        is_json = "json" in content_type or (
            raw[:1] in (b"{", b"[") and "text" not in content_type
        )
        is_text = (
            content_type.startswith("text/")
            or "xml" in content_type
            or "html" in content_type
            or "javascript" in content_type
            or "csv" in content_type
            or is_json
        )

        if is_json:
            try:
                parsed_json = json_lib.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                parsed_json = None
            # Also include raw text (truncated)
            text = raw.decode("utf-8", errors="replace")
            if len(text) > MAX_BODY_BYTES:
                body_out = text[:MAX_BODY_BYTES]
                truncated = True
            else:
                body_out = text
            body_encoding = "json"
        elif is_text:
            text = raw.decode("utf-8", errors="replace")
            if len(text) > MAX_BODY_BYTES:
                body_out = text[:MAX_BODY_BYTES]
                truncated = True
            else:
                body_out = text
            body_encoding = "text"
        else:
            # binary: base64 the first 10KB
            snippet = raw[:MAX_BINARY_BYTES]
            body_out = base64.b64encode(snippet).decode("ascii")
            body_encoding = "base64"
            truncated = total_bytes > MAX_BINARY_BYTES

        # Flatten headers (httpx Headers is multidict-ish)
        resp_headers = {k: v for k, v in resp.headers.items()}

        data: dict[str, Any] = {
            "status_code": resp.status_code,
            "url": str(resp.url),
            "method": method,
            "headers": resp_headers,
            "content_type": content_type,
            "body": body_out,
            "body_encoding": body_encoding,
            "total_bytes": total_bytes,
            "truncated": truncated,
        }
        if parsed_json is not None:
            data["parsed_json"] = parsed_json

        ok = 200 <= resp.status_code < 400
        summary = (
            f"{method} {resp.status_code} "
            f"{content_type.split(';')[0] or '?'} "
            f"({total_bytes} bytes)"
        )
        return ToolResult(
            success=ok,
            data=data,
            error=None if ok else f"HTTP {resp.status_code}",
            summary=summary,
        )
