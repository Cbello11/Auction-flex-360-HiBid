import csv
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from af360.__main__ import main
from af360.config import load_config
from af360.ebay import import_items, load_items
from af360.fetch import fetch
from af360.mcp_client import McpAuthError, McpClient, McpError

TOKEN = "test-token"


def listing(iid, title, category, n_pics=2):
    return {
        "ItemID": int(iid), "Title": title, "Description": "<p>Nice item. Free shipping!</p>",
        "ConditionDisplayName": "Used", "PrimaryCategory": {"CategoryName": category},
        "StartPrice": {"#text": 40, "@_currencyID": "USD"}, "Quantity": 1,
        "Seller": {"Email": "private@example.com", "UserID": "someone"},
        "PictureDetails": {"PictureURL": [
            f"https://i.ebayimg.com/00/s/x/z/PIC{iid}{k}abcdef/$_1.JPG" for k in range(n_pics)]},
    }


LISTINGS = {
    "111111111111": listing("111111111111", "Vintage Denim Jacket Mens L", "Clothing:Men:Coats, Jackets & Vests"),
    "222222222222": listing("222222222222", "Sony TV Remote RM-1", "Consumer Electronics:Remote Controls"),
    "333333333333": listing("333333333333", "Nike Dunk Low Mens 9", "Clothing:Men:Shoes:Athletic Shoes"),
    "444444444444": None,  # this one errors
}


class FakeMcp(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, status, body, sse=False, headers=None):
        self.send_response(status)
        if sse:
            payload = f"event: message\ndata: {json.dumps(body)}\n\n".encode()
            self.send_header("Content-Type", "text/event-stream")
        else:
            payload = json.dumps(body).encode() if body is not None else b""
            self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "unauthorized"})
        method = msg.get("method")
        FakeMcp.calls.append(method if method != "tools/call" else msg["params"]["name"])
        if method == "initialize":
            return self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "fake"}}},
                              headers={"Mcp-Session-Id": "sess-1"})
        if self.headers.get("Mcp-Session-Id") != "sess-1":
            return self._send(400, {"error": "no session"})
        if method == "notifications/initialized":
            return self._send(202, None)
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == "ebay_get_active_listings":
            ids = list(LISTINGS)
            per, page = args["entriesPerPage"], args["page"]
            chunk = ids[(page - 1) * per: page * per]
            data = {"ActiveList": {"ItemArray": {"Item": [{"ItemID": int(i)} for i in chunk]},
                                   "PaginationResult": {"TotalNumberOfPages": (len(ids) + per - 1) // per,
                                                        "TotalNumberOfEntries": len(ids)}}}
        elif name == "ebay_get_listing":
            data = LISTINGS.get(args["itemId"])
            if data is None:
                return self._send(200, {"jsonrpc": "2.0", "id": msg["id"],
                                        "result": {"isError": True, "content": [{"type": "text", "text": "eBay said no"}]}},
                                  sse=True)
        else:
            data = {}
        return self._send(200, {"jsonrpc": "2.0", "id": msg["id"],
                                "result": {"content": [{"type": "text", "text": json.dumps(data)}]}}, sse=True)


@pytest.fixture
def server():
    FakeMcp.calls = []
    httpd = HTTPServer(("127.0.0.1", 0), FakeMcp)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}/mcp"
    httpd.shutdown()


def test_fetch_pages_excludes_and_strips_private_fields(server, tmp_path):
    client = McpClient(server, token=TOKEN)
    result = fetch(tmp_path, client, exclude_categories=["Remote Controls"], pause=0, log=lambda *a: None)
    assert result["items"] == 2
    assert result["excluded"] == ["222222222222"]
    assert [f[0] for f in result["failed"]] == ["444444444444"]
    assert FakeMcp.calls.count("ebay_get_active_listings") == 1  # 4 ids fit one page of 200
    cached = (tmp_path / "ebay_cache" / "111111111111.json").read_text()
    assert "private@example.com" not in cached and "Seller" not in cached
    items = load_items(tmp_path / "ebay_items.json")
    assert {i["item_id"] for i in items} == {"111111111111", "333333333333"}


def test_rerun_uses_cache(server, tmp_path):
    client = McpClient(server, token=TOKEN)
    fetch(tmp_path, client, pause=0, log=lambda *a: None)
    FakeMcp.calls = []
    fetch(tmp_path, McpClient(server, token=TOKEN), pause=0, log=lambda *a: None)
    # only the one that failed before is asked for again
    assert FakeMcp.calls.count("ebay_get_listing") == 1


def test_paging(server, tmp_path):
    from af360.fetch import list_active_ids
    client = McpClient(server, token=TOKEN)
    client.connect()
    assert len(list_active_ids(client, page_size=3, log=lambda *a: None)) == 4
    assert FakeMcp.calls.count("ebay_get_active_listings") == 2


def test_wrong_token_is_clear(server):
    with pytest.raises(McpAuthError, match="refused the token"):
        McpClient(server, token="nope").connect()


def test_server_down_is_clear():
    with pytest.raises(McpError, match="Can't reach the eBay server"):
        McpClient("http://127.0.0.1:9/mcp", token=TOKEN, timeout=2).connect()


def test_cli_test_mode(server, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    assert main(["fetch-ebay", str(tmp_path), "--url", server, "--test"]) == 0
    assert "Active listings: 4" in capsys.readouterr().out


def test_from_ebay_saves_progress_each_item(tmp_path):
    def jpeg(url):
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1200)).save(buf, "JPEG")
        return buf.getvalue()

    items = load_items_from(LISTINGS["111111111111"], LISTINGS["333333333333"])
    seen = []

    def progress(done, total, lot, iid, photos):
        with open(tmp_path / "s" / "lots.csv", newline="", encoding="utf-8") as fh:
            seen.append(len(list(csv.DictReader(fh))))

    import_items(tmp_path / "s", items, load_config(), downloader=jpeg, pause=0, progress=progress)
    assert seen == [1, 2]  # lots.csv already had each lot right after it was added


def load_items_from(*raws):
    from af360.ebay import normalize_item
    return [normalize_item(r) for r in raws]


def test_build_no_zip(tmp_path):
    from af360.export import build
    from af360.intake import write_lots
    d = tmp_path / "z"
    (d / "photos" / "1").mkdir(parents=True)
    Image.new("RGB", (100, 80)).save(d / "photos" / "1" / "a.jpg")
    write_lots(d, [{"lot": "1", "title": "T", "description": "D", "quantity": "1", "seller_code": "S"}])
    report, _ = build(d, load_config(), make_zip=False)
    assert report.ok
    assert not (d / "output" / "images.zip").exists()
    assert (d / "output" / "images" / "1-1.jpg").exists()
