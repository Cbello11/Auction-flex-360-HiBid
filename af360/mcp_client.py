"""Minimal client for an MCP server over HTTP ("Streamable HTTP"), standard library only.

Used to call the eBay MCP server that runs on the phone (default
http://127.0.0.1:3333/mcp) so listing data never passes through the cloud.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2025-03-26"


class McpError(Exception):
    pass


class McpAuthError(McpError):
    pass


def _parse_body(raw, content_type):
    """The server answers either with plain JSON or with an event stream
    ("data: {...}" lines). Return the JSON-RPC message that carries a result."""
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in (content_type or "") or text.lstrip().startswith(("event:", "data:")):
        messages = []
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        messages.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
        for msg in reversed(messages):
            if "result" in msg or "error" in msg:
                return msg
        return messages[-1] if messages else {}
    return json.loads(text) if text.strip() else {}


class McpClient:
    def __init__(self, url, token=None, timeout=60, opener=None):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id = None
        self._next_id = 1
        if opener is None:
            host = urllib.parse.urlparse(url).hostname or ""
            if host in ("127.0.0.1", "localhost", "::1"):
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # local: never via a proxy
            else:
                opener = urllib.request.urlopen
        self._open = opener

    def _post(self, message):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with self._open(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                return _parse_body(resp.read(), resp.headers.get("Content-Type"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code in (401, 403):
                raise McpAuthError(
                    f"The eBay server refused the token (HTTP {exc.code}). "
                    "Check the token file, or ask Claude how this server version checks tokens.") from exc
            raise McpError(f"HTTP {exc.code} from the eBay server: {body}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"Can't reach the eBay server at {self.url} ({exc.reason}). "
                           "Is it running? Start it with: ebay-start") from exc

    def _request(self, method, params=None):
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            msg["params"] = params
        reply = self._post(msg)
        if "error" in reply:
            err = reply["error"]
            raise McpError(f"{method} failed: {err.get('message', err)}")
        return reply.get("result", {})

    def connect(self):
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "af360-lot-builder", "version": "1.0"},
        })
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            pass  # some servers answer notifications with 202/empty; not fatal
        return result

    def call_tool(self, name, arguments=None):
        """Call a tool and return its result, decoded from JSON when the text is JSON."""
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        if result.get("isError"):
            raise McpError(f"{name} returned an error: {' '.join(texts)[:300]}")
        if "structuredContent" in result and result["structuredContent"]:
            return result["structuredContent"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined
