"""Minimal headless Edge driver over the Chrome DevTools Protocol (test-only; no extra dependencies)."""
import base64
import itertools
import json
import subprocess
import time
from pathlib import Path

import httpx
import websocket

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class Browser:
    def __init__(self, profile_dir: str, port: int = 9333):
        self.port = port
        self.proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={port}",
                                      f"--user-data-dir={profile_dir}", "--no-first-run", "--disable-gpu",
                                      "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)

    def tab(self, width: int, height: int, mobile: bool = False) -> "Tab":
        info = httpx.put(f"http://127.0.0.1:{self.port}/json/new?about:blank", timeout=5).json()
        t = Tab(info["webSocketDebuggerUrl"])
        t.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
               deviceScaleFactor=2 if mobile else 1, mobile=mobile)
        if mobile:
            t.call("Emulation.setTouchEmulationEnabled", enabled=True)
            t.call("Emulation.setUserAgentOverride", userAgent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"))
        t.call("Page.enable")
        t.call("Page.addScriptToEvaluateOnNewDocument", source=ASK_TRAP_JS)
        return t

    def close(self):
        self.proc.kill()


class Tab:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.ids = itertools.count(1)

    def call(self, method: str, **params):
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def goto(self, url: str, wait: float = 3.0):
        self.call("Page.navigate", url=url)
        time.sleep(wait)

    def eval(self, js: str):
        r = self.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def shot(self, path: str):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        Path(path).write_bytes(base64.b64decode(data))


PROBE_JS = """(() => {
  const cards = [...document.querySelectorAll('.paragraph-card')];
  return {
    url: location.pathname,
    cards: cards.length,
    tails: [...document.querySelectorAll('.paragraph-card .live-tail')].map(e => e.textContent.trim()),
    bottomStrip: !!document.querySelector('.live-provisional'),
    finals: cards.map(c => {
      const t = c.querySelector('.paragraph-text').cloneNode(true);
      t.querySelectorAll('.live-tail').forEach(x => x.remove());
      return t.textContent.trim();
    }),
    sendDisabled: cards.map(c => c.querySelector('.paragraph-actions button').disabled),
  };
})()"""


# Test-only trap: record (and block) any /ask request with the JS stack that issued it.
ASK_TRAP_JS = """(() => {
  window.__asks = [];
  const of = window.fetch.bind(window);
  window.fetch = (u, o) => {
    if (String(u).includes('/ask')) {
      window.__asks.push({url: String(u), body: (o && o.body) || '', stack: new Error().stack});
      return Promise.reject(new Error('blocked by test'));
    }
    return of(u, o);
  };
})()"""
