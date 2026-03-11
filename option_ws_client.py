from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import websocket

from dhan_ws_client import parse_header_8, parse_prev_close, parse_ticker, RESP_DISCONNECT, RESP_PREV_CLOSE, RESP_TICKER, REQ_SUB_TICKER


class OptionWSClient:
    """
    Dedicated websocket client for active option legs.

    It watches a small dynamic instrument list (typically short leg + hedge leg).
    Whenever the watchlist changes, the client reconnects and subscribes to the
    new list cleanly.
    """

    def __init__(
        self,
        *,
        client_id: str,
        access_token: str,
        on_tick: Callable[[str, float, int], None],
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client_id = str(client_id).strip()
        self.access_token = str(access_token).strip()
        self.on_tick_callback = on_tick
        self.on_status_callback = on_status

        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.reconnect_event = threading.Event()
        self.lock = threading.RLock()

        self.watchlist: List[Tuple[str, str]] = []
        self.last_ticker_keys: Dict[Tuple[str, str], Tuple[float, int]] = {}
        self.last_packet_time: Optional[float] = None
        self.last_connect_time: Optional[float] = None

    @property
    def ws_url(self) -> str:
        return (
            f"wss://api-feed.dhan.co?version=2"
            f"&token={self.access_token}"
            f"&clientId={self.client_id}"
            f"&authType=2"
        )

    def _status(self, msg: str) -> None:
        if self.on_status_callback:
            try:
                self.on_status_callback(msg)
            except Exception:
                pass

    def start(self) -> None:
        if self.ws_thread and self.ws_thread.is_alive():
            return
        self.stop_event.clear()
        self.reconnect_event.clear()
        self.ws_thread = threading.Thread(target=self._run_forever, daemon=True)
        self.ws_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.reconnect_event.set()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass

    def set_instruments(self, instruments: Sequence[Tuple[str, str]]) -> None:
        normalized: List[Tuple[str, str]] = []
        seen = set()
        for exchange_segment, security_id in instruments:
            seg = str(exchange_segment).strip()
            sid = str(security_id).strip()
            if not seg or not sid:
                continue
            key = (seg, sid)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        with self.lock:
            if normalized == self.watchlist:
                return
            self.watchlist = normalized
            self.last_ticker_keys = {}
            self.reconnect_event.set()

    def clear_instruments(self) -> None:
        self.set_instruments([])

    def _current_watchlist(self) -> List[Tuple[str, str]]:
        with self.lock:
            return list(self.watchlist)

    def _on_open(self, ws) -> None:
        self.last_connect_time = time.time()
        watchlist = self._current_watchlist()
        if not watchlist:
            self._status("Option WS connected with empty watchlist")
            return

        sub_msg = {
            "RequestCode": REQ_SUB_TICKER,
            "InstrumentCount": len(watchlist),
            "InstrumentList": [
                {"ExchangeSegment": seg, "SecurityId": sid}
                for seg, sid in watchlist
            ],
        }
        ws.send(json.dumps(sub_msg))
        joined = ", ".join(f"{seg}:{sid}" for seg, sid in watchlist)
        self._status(f"Option WS subscribed to {joined}")

    def _on_message(self, ws, message) -> None:
        try:
            if isinstance(message, str):
                return
            msg = bytes(message)
            hdr = parse_header_8(msg)
            if not hdr:
                return

            code = int(hdr["resp_code"])
            sec = str(hdr["security_id"])
            seg = str(hdr.get("exch_seg_name") or "")
            if (seg, sec) not in self._current_watchlist():
                return

            self.last_packet_time = time.time()

            if code == RESP_TICKER:
                t = parse_ticker(hdr["payload"])
                if not t:
                    return
                ltp = float(t["ltp"])
                ltt_epoch = int(t["ltt_epoch"])
                dedup_key = (seg, sec)
                packet_key = (round(ltp, 8), ltt_epoch)
                if self.last_ticker_keys.get(dedup_key) == packet_key:
                    return
                self.last_ticker_keys[dedup_key] = packet_key
                self.on_tick_callback(sec, ltp, ltt_epoch)
                return

            if code == RESP_PREV_CLOSE:
                parse_prev_close(hdr["payload"])
                return

            if code == RESP_DISCONNECT:
                self._status("Option WS feed disconnect packet received")
                return
        except Exception as e:
            self._status(f"Option WS parse error: {e}")

    def _on_error(self, ws, error) -> None:
        self._status(f"Option WS error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        self._status(f"Option WS closed: code={close_status_code}, msg={close_msg}")

    def _run_forever(self) -> None:
        websocket.enableTrace(False)
        while not self.stop_event.is_set():
            watchlist = self._current_watchlist()
            if not watchlist:
                self.reconnect_event.wait(timeout=0.5)
                self.reconnect_event.clear()
                continue

            try:
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.reconnect_event.clear()
                self.ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                self._status(f"Option WS run error: {e}")

            if self.stop_event.is_set():
                break

            if self.reconnect_event.is_set():
                self.reconnect_event.clear()
                time.sleep(0.2)
            else:
                time.sleep(1.0)
