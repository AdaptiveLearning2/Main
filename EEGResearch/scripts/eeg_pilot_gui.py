from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _http_json(method: str, url: str, *, bearer: str | None = None, timeout_s: float = 5.0) -> dict:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    parsed = urlparse(url)
    if parsed.hostname:
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        headers["Host"] = host

    data = None
    if method.upper() in {"POST", "PUT", "PATCH"}:
        headers["Content-Type"] = "application/json"
        data = b"{}"

    req = Request(url, data=data, headers=headers, method=method.upper())
    with urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


class PilotGuiApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("EEG pilot verifier (local API)")
        self.geometry("900x720")

        self._poll_after_id: str | None = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="API base URL").grid(row=0, column=0, sticky="w")
        self.base_url = tk.StringVar(value="http://127.0.0.1:8000")
        ttk.Entry(frm, textvariable=self.base_url, width=60).grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="Learner token").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.learner_token = tk.StringVar(value="learner-token-123")
        ttk.Entry(frm, textvariable=self.learner_token, width=60, show="*").grid(
            row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(frm, text="Admin token").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.admin_token = tk.StringVar(value="admin-token-123")
        ttk.Entry(frm, textvariable=self.admin_token, width=60, show="*").grid(
            row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(frm, text="Poll (ms)").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.poll_ms = tk.IntVar(value=500)
        ttk.Spinbox(frm, from_=100, to=5000, increment=100, textvariable=self.poll_ms, width=8).grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=(8, 0)
        )

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=4, column=0, columnspan=2, sticky="we", pady=(12, 0))

        ttk.Button(btn_row, text="Start session (admin)", command=self.start_session).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Stop session (admin)", command=self.stop_session).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="Refresh metrics (admin)", command=self.refresh_metrics).pack(side=tk.LEFT, padx=(8, 0))

        muse_frm = ttk.LabelFrame(frm, text="Muse bridge (EEG_SOURCE=muse)", padding=(0, 8, 0, 0))
        muse_frm.grid(row=5, column=0, columnspan=2, sticky="we", pady=(12, 0))
        ttk.Label(muse_frm, text="Headbands (from bridge)").grid(row=0, column=0, sticky="w")
        self.muse_device_combo = ttk.Combobox(muse_frm, width=40, state="readonly")
        self.muse_device_combo.grid(row=0, column=1, sticky="we", padx=(8, 0))
        muse_btn = ttk.Frame(muse_frm)
        muse_btn.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(muse_btn, text="Refresh devices (admin)", command=self.muse_refresh_devices).pack(side=tk.LEFT)
        ttk.Button(muse_btn, text="Connect (admin)", command=self.muse_connect_selected).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(muse_btn, text="Disconnect (admin)", command=self.muse_disconnect).pack(side=tk.LEFT, padx=(8, 0))
        self.muse_bridge_status = tk.StringVar(value="Muse: (poll /api/v1/muse/status)")
        ttk.Label(muse_frm, textvariable=self.muse_bridge_status, wraplength=820).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        muse_frm.columnconfigure(1, weight=1)

        self.status = tk.StringVar(value="Idle")
        ttk.Label(frm, textvariable=self.status).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.summary = tk.Text(frm, height=8, wrap="word")
        self.summary.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

        ttk.Label(frm, text="Raw /api/v1/state JSON").grid(row=8, column=0, sticky="w", pady=(8, 0))
        self.raw = tk.Text(frm, height=18, wrap="none")
        self.raw.grid(row=9, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

        frm.rowconfigure(9, weight=1)
        frm.columnconfigure(1, weight=1)

        self._schedule_poll()

    def _base(self) -> str:
        return self.base_url.get().rstrip("/")

    def _api_error_message(self, err: Exception) -> str:
        if isinstance(err, HTTPError):
            try:
                body = err.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return f"HTTP {err.code} {err.reason}\n{body}".strip()
        if isinstance(err, URLError):
            return f"URL error: {err.reason}"
        return str(err)

    @staticmethod
    def _replace_text_preserve_scroll(widget: tk.Text, new_text: str) -> None:
        """Replace text without forcing the view back to top."""
        y_first, _ = widget.yview()
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, new_text)
        widget.yview_moveto(y_first)

    def start_session(self) -> None:
        try:
            url = f"{self._base()}/api/v1/session/start"
            _http_json("POST", url, bearer=self.admin_token.get().strip())
            self.status.set("Session start requested")
        except Exception as e:
            messagebox.showerror("Start session failed", self._api_error_message(e))

    def stop_session(self) -> None:
        try:
            url = f"{self._base()}/api/v1/session/stop"
            _http_json("POST", url, bearer=self.admin_token.get().strip())
            self.status.set("Session stop requested")
        except Exception as e:
            messagebox.showerror("Stop session failed", self._api_error_message(e))

    def muse_refresh_devices(self) -> None:
        try:
            url = f"{self._base()}/api/v1/muse/refresh"
            payload = _http_json("POST", url, bearer=self.admin_token.get().strip())
            if not (payload.get("data") or {}).get("ok", True):
                messagebox.showerror("Muse refresh", json.dumps(payload, indent=2))
            else:
                self.poll_muse_status()
        except Exception as e:
            messagebox.showerror("Muse refresh failed", self._api_error_message(e))

    def muse_connect_selected(self) -> None:
        name = (self.muse_device_combo.get() or "").strip()
        if not name:
            messagebox.showwarning("Muse connect", "Select a headband in the dropdown (use Refresh devices first).")
            return
        try:
            url = f"{self._base()}/api/v1/muse/connect"
            req = Request(
                url,
                data=json.dumps({"name": name}).encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.admin_token.get().strip()}",
                },
                method="POST",
            )
            with urlopen(req, timeout=10.0) as resp:
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw) if raw else {}
            if not (payload.get("data") or {}).get("ok", True):
                messagebox.showerror("Muse connect", json.dumps(payload, indent=2))
            else:
                self.poll_muse_status()
        except Exception as e:
            messagebox.showerror("Muse connect failed", self._api_error_message(e))

    def muse_disconnect(self) -> None:
        try:
            url = f"{self._base()}/api/v1/muse/disconnect"
            payload = _http_json("POST", url, bearer=self.admin_token.get().strip())
            if not (payload.get("data") or {}).get("ok", True):
                messagebox.showerror("Muse disconnect", json.dumps(payload, indent=2))
            else:
                self.poll_muse_status()
        except Exception as e:
            messagebox.showerror("Muse disconnect failed", self._api_error_message(e))

    def poll_muse_status(self) -> None:
        try:
            url = f"{self._base()}/api/v1/muse/status"
            payload = _http_json("GET", url, bearer=self.learner_token.get().strip(), timeout_s=2.0)
            data = payload.get("data") or {}
            ing = data.get("ingestion") or {}
            devices = ing.get("muse_devices") or []
            if isinstance(devices, list) and devices:
                self.muse_device_combo.configure(state="normal")
                self.muse_device_combo["values"] = tuple(str(d) for d in devices)
                self.muse_device_combo.set(str(devices[0]))
                self.muse_device_combo.configure(state="readonly")
            conn = ing.get("connection_state_name", "n/a")
            active = ing.get("active_muse_name") or ""
            fw = ing.get("firmware_version") or ""
            bridge = ing.get("bridge_mode", "?")
            self.muse_bridge_status.set(
                f"bridge={bridge} | state={conn} | active={active or '—'} | firmware={fw or '—'} | "
                f"devices={len(devices) if isinstance(devices, list) else 0}"
            )
        except Exception as e:
            self.muse_bridge_status.set(f"muse status error: {self._api_error_message(e)}")

    def refresh_metrics(self) -> None:
        try:
            url = f"{self._base()}/api/v1/metrics"
            payload = _http_json("GET", url, bearer=self.admin_token.get().strip())
            pretty = json.dumps(payload, indent=2, sort_keys=True)
            self._replace_text_preserve_scroll(self.summary, pretty)
            self.status.set("Metrics refreshed")
        except Exception as e:
            messagebox.showerror("Metrics failed", self._api_error_message(e))

    def poll_state(self) -> None:
        try:
            url = f"{self._base()}/api/v1/state"
            payload = _http_json("GET", url, bearer=self.learner_token.get().strip(), timeout_s=2.0)

            pretty = json.dumps(payload, indent=2, sort_keys=True)
            self._replace_text_preserve_scroll(self.raw, pretty)

            status = payload.get("status")
            data = payload.get("data")
            if status == "ok" and isinstance(data, dict):
                ch = data.get("channels") or {}
                feats = data.get("features") or {}
                st = data.get("state") or {}
                ing = data.get("ingestion") or {}
                conn = ing.get("connection_state_name", "n/a")
                band = "on" if ing.get("muse_connected") else "off"
                bridge = ing.get("bridge_mode", "?")
                msg = (
                    f"status=ok | headband={band} ({conn}) bridge={bridge} | "
                    f"tp9={ch.get('tp9')} af7={ch.get('af7')} af8={ch.get('af8')} tp10={ch.get('tp10')} | "
                    f"quality={feats.get('signal_quality')} conf={feats.get('confidence')} | "
                    f"label={st.get('label')}"
                )
                self.status.set(msg)
                devices = ing.get("muse_devices") or []
                if isinstance(devices, list) and devices:
                    self.muse_device_combo.configure(state="normal")
                    self.muse_device_combo["values"] = tuple(str(d) for d in devices)
                    if self.muse_device_combo.get() not in self.muse_device_combo["values"]:
                        self.muse_device_combo.set(str(devices[0]))
                    self.muse_device_combo.configure(state="readonly")
            else:
                self.status.set(f"status={status} | {payload.get('message', '')}")
        except Exception as e:
            self.status.set(f"state poll error: {self._api_error_message(e)}")

    def _schedule_poll(self) -> None:
        self.poll_state()
        self.poll_muse_status()
        delay = max(50, int(self.poll_ms.get()))
        self._poll_after_id = self.after(delay, self._schedule_poll)

    def destroy(self) -> None:
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
        super().destroy()


def main() -> None:
    PilotGuiApp().mainloop()


if __name__ == "__main__":
    main()
