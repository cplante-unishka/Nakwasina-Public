import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, messagebox

from ai_analysis_report import (
    AI_PROVIDER_ANTHROPIC,
    AI_PROVIDER_DISABLED,
    AI_PROVIDER_OPENAI,
    AI_PROVIDER_VALUES,
    AIAnalysisError,
    generate_ai_analysis_report,
)
from aml_crypto_analyzer import (
    Analyzer,
    IntelligenceStore,
    ProviderError,
    ProviderRouter,
    SUPPORTED_ASSETS,
    export_graphml,
    export_json,
    export_xlsx,
    normalize_lookup_address,
)
from sanctions_updater import SanctionsSyncError, sync_sanctions

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    BUNDLE_ROOT = Path(sys._MEIPASS)
    if sys.platform == "darwin":
        candidate = BUNDLE_ROOT.parent / "Resources"
        RESOURCE_ROOT = candidate if candidate.exists() else BUNDLE_ROOT
    else:
        RESOURCE_ROOT = BUNDLE_ROOT
    APP_ROOT = Path.home() / "Library" / "Application Support" / "Nakwasina"
    APP_ROOT.mkdir(parents=True, exist_ok=True)
else:
    RESOURCE_ROOT = Path(__file__).resolve().parent
    APP_ROOT = RESOURCE_ROOT

LOCAL_LOGO_PATH = RESOURCE_ROOT / "UNISHKA_Logo.png"
LOADING_GIF_PATH = RESOURCE_ROOT / "UNISHKA_Loading.gif"
APP_NAME = "Nakwasina"
SPLASH_FRAME_DELAY_MS = 60
SHOW_AI_CONTROLS = os.getenv("NAKWASINA_SHOW_AI_CONTROLS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _api_key_store_path() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME / "api_keys.json"


API_KEY_STORE_PATH = _api_key_store_path()

DEBUG_LOG_PATH: Optional[Path] = None
if IS_FROZEN:
    DEBUG_LOG_PATH = Path.home() / "Library" / "Logs" / APP_NAME / "startup.log"
    DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _debug_log(message: str) -> None:
    if DEBUG_LOG_PATH is None:
        return
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat()} {message}\n")
    except OSError:
        pass


def _gif_frame_delays(gif_path: Path) -> List[int]:
    try:
        data = gif_path.read_bytes()
    except OSError:
        return []
    if len(data) < 13 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        return []

    pos = 13
    packed = data[10]
    if packed & 0x80:
        pos += 3 * (2 ** ((packed & 0x07) + 1))

    delays: List[int] = []
    pending_delay = SPLASH_FRAME_DELAY_MS

    def skip_sub_blocks(start: int) -> int:
        cursor = start
        while cursor < len(data):
            block_size = data[cursor]
            cursor += 1
            if block_size == 0:
                break
            cursor += block_size
        return cursor

    while pos < len(data):
        marker = data[pos]
        pos += 1

        if marker == 0x21:
            if pos >= len(data):
                break
            label = data[pos]
            pos += 1
            if label == 0xF9 and pos < len(data):
                block_size = data[pos]
                pos += 1
                if block_size == 4 and pos + 4 <= len(data):
                    delay_cs = int.from_bytes(data[pos + 1 : pos + 3], "little")
                    pending_delay = max(20, delay_cs * 10) if delay_cs > 0 else SPLASH_FRAME_DELAY_MS
                    pos += 4
                    if pos < len(data) and data[pos] == 0:
                        pos += 1
                else:
                    pos = skip_sub_blocks(pos + block_size)
            else:
                pos = skip_sub_blocks(pos)
        elif marker == 0x2C:
            if pos + 9 > len(data):
                break
            delays.append(pending_delay)
            pending_delay = SPLASH_FRAME_DELAY_MS
            image_packed = data[pos + 8]
            pos += 9
            if image_packed & 0x80:
                pos += 3 * (2 ** ((image_packed & 0x07) + 1))
            if pos >= len(data):
                break
            pos += 1
            pos = skip_sub_blocks(pos)
        elif marker == 0x3B:
            break
        else:
            break

    return delays


class LoadingSplash:
    def __init__(self, parent: tk.Tk, gif_path: Path, on_complete) -> None:
        self.parent = parent
        self.gif_path = gif_path
        self.on_complete = on_complete
        self.delays = _gif_frame_delays(gif_path)
        self.frame_index = 0
        self.current_frame: Optional[tk.PhotoImage] = None

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg="#ffffff")
        self.label = tk.Label(self.window, bg="#ffffff", bd=0, highlightthickness=0)
        self.label.pack()

    def start(self) -> bool:
        if not self.gif_path.exists() or not self._show_frame(0):
            self.close()
            return False

        self._center()
        self.window.deiconify()
        self.window.lift()
        self.parent.after(self._delay_for(0), self._advance)
        return True

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()

    def _show_frame(self, index: int) -> bool:
        try:
            frame = tk.PhotoImage(file=str(self.gif_path), format=f"gif -index {index}")
        except tk.TclError:
            return False

        scale = self._frame_scale(frame)
        if scale > 1:
            frame = frame.subsample(scale, scale)
        self.current_frame = frame
        self.frame_index = index
        self.label.configure(image=self.current_frame)
        return True

    def _frame_scale(self, frame: tk.PhotoImage) -> int:
        screen_w = max(1, self.window.winfo_screenwidth())
        screen_h = max(1, self.window.winfo_screenheight())
        max_w = min(620, max(320, screen_w - 160))
        max_h = min(620, max(320, screen_h - 160))
        scale = 1
        while frame.width() // scale > max_w or frame.height() // scale > max_h:
            scale += 1
        return scale

    def _center(self) -> None:
        self.window.update_idletasks()
        width = self.label.winfo_reqwidth()
        height = self.label.winfo_reqheight()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _delay_for(self, index: int) -> int:
        if 0 <= index < len(self.delays):
            return self.delays[index]
        return SPLASH_FRAME_DELAY_MS

    def _advance(self) -> None:
        if not self.window.winfo_exists():
            return
        next_index = self.frame_index + 1
        if self.delays and next_index >= len(self.delays):
            self._complete()
            return
        if not self._show_frame(next_index):
            self._complete()
            return
        self.parent.after(self._delay_for(next_index), self._advance)

    def _complete(self) -> None:
        self.close()
        self.on_complete()


LIGHT_THEME = {
    "window_bg": "#f5f7fb",
    "surface": "#ffffff",
    "surface_alt": "#edf2f7",
    "text": "#16202a",
    "muted_text": "#415466",
    "border": "#c7d2de",
    "accent": "#1f6feb",
    "accent_active": "#1557b0",
    "input_bg": "#ffffff",
    "input_fg": "#16202a",
    "log_bg": "#ffffff",
    "info_fg": "#16202a",
    "warning_fg": "#d97706",
    "error_fg": "#dc2626",
}
DARK_THEME = {
    "window_bg": "#0f1723",
    "surface": "#162130",
    "surface_alt": "#1c2a3c",
    "text": "#e5edf7",
    "muted_text": "#9eb0c6",
    "border": "#2b3a4d",
    "accent": "#4c8dff",
    "accent_active": "#74a6ff",
    "input_bg": "#0f1723",
    "input_fg": "#e5edf7",
    "log_bg": "#0b1220",
    "info_fg": "#e5edf7",
    "warning_fg": "#fbbf24",
    "error_fg": "#f87171",
}


class AMLGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self.title("Nakwasina")
        self.geometry("980x760")
        self.minsize(900, 650)

        self.style = ttk.Style(self)
        self.logo_photo = None
        self.loading_splash: Optional[LoadingSplash] = None
        self.update_in_progress = False
        self.api_key_load_error: Optional[str] = None
        self.saved_api_keys = self._load_saved_api_keys()
        self.ai_token_by_provider = self._load_initial_ai_tokens()
        self.current_ai_provider = self._initial_ai_provider()

        self.asset_var = tk.StringVar(value="BTC")
        self.mode_var = tk.StringVar(value="tx")
        self.seed_var = tk.StringVar()
        self.max_transactions_var = tk.StringVar(value="50")
        self.all_transactions_var = tk.BooleanVar(value=False)
        self.blockcypher_key_var = tk.StringVar(
            value=self._initial_api_key("BLOCKCYPHER_API_KEY", "blockcypher")
        )
        self.blockchair_key_var = tk.StringVar(
            value=self._initial_api_key("BLOCKCHAIR_API_KEY", "blockchair")
        )
        self.coinstats_key_var = tk.StringVar(
            value=self._initial_api_key("COINSTATS_API_KEY", "coinstats")
        )
        self.remember_api_keys_var = tk.BooleanVar(value=True)
        self.api_key_status_var = tk.StringVar(value=self._initial_api_key_status())
        self.ai_provider_var = tk.StringVar(value=self.current_ai_provider)
        self.ai_token_var = tk.StringVar(value=self._ai_token_for_provider(self.current_ai_provider))
        self.update_status_var = tk.StringVar(value="Idle")
        self.update_progress_var = tk.IntVar(value=0)
        self.export_json_var = tk.BooleanVar(value=True)
        self.export_xlsx_var = tk.BooleanVar(value=True)
        self.export_graphml_var = tk.BooleanVar(value=True)
        self.dark_mode_var = tk.BooleanVar(value=False)

        self._configure_styles()
        self._build_ui()
        self._apply_theme()
        self._load_logo()
        if self.api_key_load_error:
            self.log(f"API key load warning: {self.api_key_load_error}", level="warning")
        self.after(0, self._start_loading_screen)

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=12, style="App.TFrame")
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, weight=1)

        self.logo_label = ttk.Label(top, text="Unishka Research Service", style="App.TLabel")
        self.logo_label.grid(row=0, column=0, sticky="w")

        self.title_label = ttk.Label(
            top,
            text="Cryptocurrency Investigation Console",
            font=("Segoe UI", 16, "bold"),
            style="Title.TLabel",
        )
        self.title_label.grid(row=0, column=1)

        theme_toggle = ttk.Checkbutton(
            top,
            text="Dark Mode",
            variable=self.dark_mode_var,
            command=self._toggle_dark_mode,
            style="Switch.TCheckbutton",
        )
        theme_toggle.grid(row=0, column=2, sticky="e")

        form = ttk.LabelFrame(self, text="Investigation Inputs", padding=12, style="Section.TLabelframe")
        form.pack(fill="x", padx=12, pady=8)

        ttk.Label(form, text="Asset", style="App.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        assets = sorted(SUPPORTED_ASSETS.keys())
        asset_combo = ttk.Combobox(form, state="readonly", textvariable=self.asset_var, values=assets, width=10)
        asset_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Mode", style="App.TLabel").grid(row=0, column=2, sticky="w", padx=4, pady=4)
        mode_frame = ttk.Frame(form, style="App.TFrame")
        mode_frame.grid(row=0, column=3, sticky="w", padx=4, pady=4)
        ttk.Radiobutton(mode_frame, text="Transaction", variable=self.mode_var, value="tx").pack(side="left")
        ttk.Radiobutton(mode_frame, text="Wallet/Address", variable=self.mode_var, value="address").pack(side="left", padx=10)

        ttk.Label(form, text="TXID / Address", style="App.TLabel").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        seed_entry = ttk.Entry(form, textvariable=self.seed_var, width=90)
        seed_entry.grid(row=1, column=1, columnspan=5, sticky="we", padx=4, pady=4)

        ttk.Label(form, text="Max Transactions", style="App.TLabel").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.max_transactions_entry = ttk.Entry(form, textvariable=self.max_transactions_var, width=12)
        self.max_transactions_entry.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        ttk.Checkbutton(
            form,
            text="All transactions",
            variable=self.all_transactions_var,
            command=self._toggle_transaction_limit,
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="BlockCypher Key", style="App.TLabel").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.blockcypher_key_var, width=90, show="*").grid(
            row=3, column=1, columnspan=5, sticky="we", padx=4, pady=4
        )

        ttk.Label(form, text="Blockchair Key", style="App.TLabel").grid(row=4, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.blockchair_key_var, width=90, show="*").grid(
            row=4, column=1, columnspan=5, sticky="we", padx=4, pady=4
        )

        ttk.Label(form, text="CoinStats Key", style="App.TLabel").grid(row=5, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.coinstats_key_var, width=90, show="*").grid(
            row=5, column=1, columnspan=5, sticky="we", padx=4, pady=4
        )

        next_row = 6
        if SHOW_AI_CONTROLS:
            ttk.Label(form, text="AI Analysis", style="App.TLabel").grid(
                row=next_row, column=0, sticky="w", padx=4, pady=4
            )
            ai_provider_combo = ttk.Combobox(
                form,
                state="readonly",
                textvariable=self.ai_provider_var,
                values=AI_PROVIDER_VALUES,
                width=14,
            )
            ai_provider_combo.grid(row=next_row, column=1, sticky="w", padx=4, pady=4)
            ai_provider_combo.bind("<<ComboboxSelected>>", self._on_ai_provider_changed)
            next_row += 1

            ttk.Label(form, text="AI API Token", style="App.TLabel").grid(
                row=next_row, column=0, sticky="w", padx=4, pady=4
            )
            self.ai_token_entry = ttk.Entry(form, textvariable=self.ai_token_var, width=90, show="*")
            self.ai_token_entry.grid(row=next_row, column=1, columnspan=5, sticky="we", padx=4, pady=4)
            next_row += 1

        ttk.Label(form, text="API Key Storage", style="App.TLabel").grid(row=next_row, column=0, sticky="w", padx=4, pady=4)
        key_store_frame = ttk.Frame(form, style="App.TFrame")
        key_store_frame.grid(row=next_row, column=1, columnspan=5, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(key_store_frame, text="Remember API keys", variable=self.remember_api_keys_var).pack(side="left")
        ttk.Button(key_store_frame, text="Save API Keys", command=self.save_api_keys).pack(side="left", padx=8)
        ttk.Button(key_store_frame, text="Clear Saved Keys", command=self.clear_saved_api_keys).pack(side="left")
        ttk.Label(key_store_frame, textvariable=self.api_key_status_var, style="Status.TLabel").pack(side="left", padx=10)
        next_row += 1

        ttk.Label(form, text="Exports", style="App.TLabel").grid(row=next_row, column=0, sticky="w", padx=4, pady=4)
        exports_frame = ttk.Frame(form, style="App.TFrame")
        exports_frame.grid(row=next_row, column=1, columnspan=5, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(exports_frame, text="JSON", variable=self.export_json_var).pack(side="left")
        ttk.Checkbutton(exports_frame, text="XLSX", variable=self.export_xlsx_var).pack(side="left", padx=10)
        ttk.Checkbutton(exports_frame, text="GraphML", variable=self.export_graphml_var).pack(side="left")

        btns = ttk.Frame(self, padding=(12, 4), style="App.TFrame")
        btns.pack(fill="x")

        ttk.Button(btns, text="Run Trace", command=self.run_trace).pack(side="left")
        self.update_button = ttk.Button(btns, text="Update Sanctions + Mixers", command=self.run_update)
        self.update_button.pack(side="left", padx=8)
        ttk.Button(btns, text="Clear Log", command=self.clear_log).pack(side="left")
        ttk.Label(btns, textvariable=self.update_status_var, style="Status.TLabel").pack(side="left", padx=10)
        self.update_progress = ttk.Progressbar(
            btns,
            mode="determinate",
            maximum=100,
            variable=self.update_progress_var,
            length=220,
        )
        self.update_progress.pack(side="left", padx=8)

        log_frame = ttk.LabelFrame(self, text="Output", padding=8, style="Section.TLabelframe")
        log_frame.pack(fill="both", expand=True, padx=12, pady=8)

        self.log_text = tk.Text(log_frame, height=24, wrap="word")
        self.log_text.pack(fill="both", expand=True)

        self._toggle_transaction_limit()
        self._sync_ai_provider_controls()
        self.log("GUI initialized. Enter parameters and run a trace.")

    def _configure_styles(self) -> None:
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

    def _toggle_dark_mode(self) -> None:
        self._apply_theme()

    def _start_loading_screen(self) -> None:
        self.loading_splash = LoadingSplash(self, LOADING_GIF_PATH, self._show_main_window)
        if not self.loading_splash.start():
            self._show_main_window()
            return
        self.after(800, self._force_show_main_window)

    def _force_show_main_window(self) -> None:
        if self.loading_splash:
            self.loading_splash.close()
        self._show_main_window()

    def _show_main_window(self) -> None:
        _debug_log("show main window")
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_force()

    def _toggle_transaction_limit(self) -> None:
        if hasattr(self, "max_transactions_entry"):
            state = "disabled" if self.all_transactions_var.get() else "normal"
            self.max_transactions_entry.configure(state=state)

    def _load_initial_ai_tokens(self) -> dict:
        return {
            AI_PROVIDER_OPENAI: os.getenv("OPENAI_API_KEY") or str(self.saved_api_keys.get("openai", "")),
            AI_PROVIDER_ANTHROPIC: os.getenv("ANTHROPIC_API_KEY") or str(self.saved_api_keys.get("anthropic", "")),
        }

    def _initial_ai_provider(self) -> str:
        env_provider = self._normalize_ai_provider(os.getenv("NAKWASINA_AI_PROVIDER", ""))
        if env_provider:
            return env_provider
        saved_provider = str(self.saved_api_keys.get("ai_provider", AI_PROVIDER_DISABLED))
        if saved_provider in AI_PROVIDER_VALUES:
            return saved_provider
        return AI_PROVIDER_DISABLED

    def _normalize_ai_provider(self, provider: str) -> str:
        normalized = str(provider or "").strip()
        if normalized in AI_PROVIDER_VALUES:
            return normalized
        aliases = {
            "disabled": AI_PROVIDER_DISABLED,
            "off": AI_PROVIDER_DISABLED,
            "none": AI_PROVIDER_DISABLED,
            "chatgpt": AI_PROVIDER_OPENAI,
            "openai": AI_PROVIDER_OPENAI,
            "claude": AI_PROVIDER_ANTHROPIC,
            "anthropic": AI_PROVIDER_ANTHROPIC,
        }
        return aliases.get(normalized.lower(), "")

    def _ai_token_for_provider(self, provider: str) -> str:
        if provider in {AI_PROVIDER_OPENAI, AI_PROVIDER_ANTHROPIC}:
            return str(self.ai_token_by_provider.get(provider, ""))
        return ""

    def _on_ai_provider_changed(self, _event=None) -> None:
        self._store_current_ai_token()
        self.current_ai_provider = str(self.ai_provider_var.get() or AI_PROVIDER_DISABLED)
        self.ai_token_var.set(self._ai_token_for_provider(self.current_ai_provider))
        self._sync_ai_provider_controls()

    def _store_current_ai_token(self) -> None:
        if self.current_ai_provider in {AI_PROVIDER_OPENAI, AI_PROVIDER_ANTHROPIC}:
            self.ai_token_by_provider[self.current_ai_provider] = self.ai_token_var.get().strip()

    def _sync_ai_provider_controls(self) -> None:
        if hasattr(self, "ai_token_entry"):
            state = "disabled" if self.ai_provider_var.get() == AI_PROVIDER_DISABLED else "normal"
            self.ai_token_entry.configure(state=state)

    def _initial_api_key(self, env_name: str, settings_key: str) -> str:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
        return str(self.saved_api_keys.get(settings_key, ""))

    def _initial_api_key_status(self) -> str:
        if self.saved_api_keys:
            return "Saved keys loaded"
        if self.api_key_load_error:
            return "Saved key load failed"
        return "No saved keys"

    def _load_saved_api_keys(self) -> dict:
        if not API_KEY_STORE_PATH.exists():
            return {}
        try:
            payload = json.loads(API_KEY_STORE_PATH.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("settings file is not a JSON object")
            keys = {}
            for name in ("blockcypher", "blockchair", "coinstats", "openai", "anthropic"):
                value = payload.get(name, "")
                if isinstance(value, str) and value.strip():
                    keys[name] = value.strip()
            provider = payload.get("ai_provider", "")
            if isinstance(provider, str) and provider in AI_PROVIDER_VALUES:
                keys["ai_provider"] = provider
            return keys
        except Exception as exc:
            self.api_key_load_error = f"{API_KEY_STORE_PATH}: {exc}"
            return {}

    def _collect_api_keys(self) -> dict:
        self._store_current_ai_token()
        return {
            "blockcypher": self.blockcypher_key_var.get().strip(),
            "blockchair": self.blockchair_key_var.get().strip(),
            "coinstats": self.coinstats_key_var.get().strip(),
            "openai": str(self.ai_token_by_provider.get(AI_PROVIDER_OPENAI, "")).strip(),
            "anthropic": str(self.ai_token_by_provider.get(AI_PROVIDER_ANTHROPIC, "")).strip(),
            "ai_provider": self.ai_provider_var.get().strip() or AI_PROVIDER_DISABLED,
        }

    def save_api_keys(self) -> None:
        if self._save_api_keys(show_message=True):
            self.remember_api_keys_var.set(True)

    def _save_api_keys(self, show_message: bool = False) -> bool:
        keys = self._collect_api_keys()
        has_token = any(value for name, value in keys.items() if name != "ai_provider")
        if not has_token:
            if show_message and not API_KEY_STORE_PATH.exists():
                self.api_key_status_var.set("No API keys to save")
                messagebox.showinfo("No API Keys", "Enter at least one API key to save.")
                return True
            return self._delete_saved_api_keys(show_message=show_message, empty_ok=True)

        try:
            API_KEY_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = API_KEY_STORE_PATH.with_suffix(API_KEY_STORE_PATH.suffix + ".tmp")
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(keys, handle, indent=2)
                handle.write("\n")
            tmp_path.replace(API_KEY_STORE_PATH)
            try:
                os.chmod(API_KEY_STORE_PATH, 0o600)
            except OSError:
                pass
            self.api_key_status_var.set("API keys saved")
            self.log(f"API keys saved to {API_KEY_STORE_PATH}")
            if show_message:
                messagebox.showinfo("API Keys Saved", f"Saved API keys to:\n{API_KEY_STORE_PATH}")
            return True
        except Exception as exc:
            self.api_key_status_var.set("API key save failed")
            self.log(f"API key save failed: {exc}", level="warning")
            if show_message:
                messagebox.showerror("API Key Save Failed", str(exc))
            return False

    def clear_saved_api_keys(self) -> None:
        if self._delete_saved_api_keys(show_message=True, empty_ok=False):
            self.remember_api_keys_var.set(False)

    def _delete_saved_api_keys(self, show_message: bool = False, empty_ok: bool = False) -> bool:
        try:
            if API_KEY_STORE_PATH.exists():
                API_KEY_STORE_PATH.unlink()
                self.api_key_status_var.set("Saved keys cleared")
                self.log(f"Saved API keys cleared from {API_KEY_STORE_PATH}")
                if show_message:
                    messagebox.showinfo("Saved Keys Cleared", "Saved API keys were removed.")
            else:
                self.api_key_status_var.set("No saved keys")
                if not empty_ok:
                    self.log("No saved API keys found.")
                    if show_message:
                        messagebox.showinfo("No Saved Keys", "No saved API keys were found.")
            return True
        except Exception as exc:
            self.api_key_status_var.set("Clear saved keys failed")
            self.log(f"Clear saved API keys failed: {exc}", level="warning")
            if show_message:
                messagebox.showerror("Clear Saved Keys Failed", str(exc))
            return False

    def _apply_theme(self) -> None:
        palette = DARK_THEME if self.dark_mode_var.get() else LIGHT_THEME

        self.configure(bg=palette["window_bg"])
        self.option_add("*TCombobox*Listbox.background", palette["input_bg"])
        self.option_add("*TCombobox*Listbox.foreground", palette["input_fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", palette["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", palette["text"])

        self.style.configure("App.TFrame", background=palette["window_bg"])
        self.style.configure("App.TLabel", background=palette["window_bg"], foreground=palette["text"])
        self.style.configure("Title.TLabel", background=palette["window_bg"], foreground=palette["text"])
        self.style.configure("Status.TLabel", background=palette["window_bg"], foreground=palette["muted_text"])
        self.style.configure(
            "Section.TLabelframe",
            background=palette["surface"],
            bordercolor=palette["border"],
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "Section.TLabelframe.Label",
            background=palette["surface"],
            foreground=palette["text"],
        )
        self.style.configure(
            "TButton",
            background=palette["surface_alt"],
            foreground=palette["text"],
            bordercolor=palette["border"],
            focuscolor=palette["accent"],
            padding=(10, 6),
        )
        self.style.map(
            "TButton",
            background=[("active", palette["accent"]), ("disabled", palette["surface_alt"])],
            foreground=[("active", palette["text"]), ("disabled", palette["muted_text"])],
        )
        self.style.configure(
            "TCheckbutton",
            background=palette["window_bg"],
            foreground=palette["text"],
            indicatorcolor=palette["surface"],
            indicatormargin=4,
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", palette["window_bg"])],
            foreground=[("active", palette["text"])],
        )
        self.style.configure(
            "Switch.TCheckbutton",
            background=palette["window_bg"],
            foreground=palette["text"],
        )
        self.style.configure(
            "TRadiobutton",
            background=palette["window_bg"],
            foreground=palette["text"],
            indicatorcolor=palette["surface"],
        )
        self.style.map(
            "TRadiobutton",
            background=[("active", palette["window_bg"])],
            foreground=[("active", palette["text"])],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=palette["input_bg"],
            foreground=palette["input_fg"],
            bordercolor=palette["border"],
            insertcolor=palette["input_fg"],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=palette["input_bg"],
            background=palette["input_bg"],
            foreground=palette["input_fg"],
            arrowcolor=palette["input_fg"],
            bordercolor=palette["border"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["input_bg"])],
            foreground=[("readonly", palette["input_fg"])],
            selectbackground=[("readonly", palette["accent"])],
            selectforeground=[("readonly", palette["text"])],
        )
        self.style.configure(
            "Horizontal.TProgressbar",
            troughcolor=palette["surface_alt"],
            background=palette["accent"],
            bordercolor=palette["border"],
            lightcolor=palette["accent"],
            darkcolor=palette["accent"],
        )

        if hasattr(self, "log_text"):
            self.log_text.configure(
                bg=palette["log_bg"],
                fg=palette["info_fg"],
                insertbackground=palette["input_fg"],
                selectbackground=palette["accent"],
                selectforeground=palette["text"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=palette["border"],
                highlightcolor=palette["accent"],
            )
            self.log_text.tag_configure("info", foreground=palette["info_fg"])
            self.log_text.tag_configure("warning", foreground=palette["warning_fg"])
            self.log_text.tag_configure("error", foreground=palette["error_fg"])

    def _load_logo(self) -> None:
        if Image is None or ImageTk is None:
            self.log("Pillow not installed; logo image display disabled.")
            return

        def _worker() -> None:
            try:
                if LOCAL_LOGO_PATH.exists():
                    self.after(0, lambda: self._render_logo(LOCAL_LOGO_PATH))
                    self.log(f"Loaded logo from local file: {LOCAL_LOGO_PATH}")
                    return
                self.log(f"Logo file not found: {LOCAL_LOGO_PATH}")
            except Exception as exc:
                self.log(f"Logo load warning: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _render_logo(self, image_path: Path) -> None:
        img = Image.open(image_path)
        img.thumbnail((240, 100))
        self.logo_photo = ImageTk.PhotoImage(img)
        self.logo_label.configure(image=self.logo_photo, text="")
        self.log(f"Loaded logo from {image_path}")

    def run_trace(self) -> None:
        seed = self.seed_var.get().strip()
        if not seed:
            messagebox.showerror("Missing Input", "Provide a transaction hash or wallet/address.")
            return

        max_transactions = None
        if not self.all_transactions_var.get():
            try:
                max_transactions = int(self.max_transactions_var.get().strip())
                if max_transactions < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid Input",
                    "Max transactions must be a positive integer, or select All transactions.",
                )
                return

        export_json_enabled = bool(self.export_json_var.get())
        export_xlsx_enabled = bool(self.export_xlsx_var.get())
        export_graphml_enabled = bool(self.export_graphml_var.get())
        ai_provider = str(self.ai_provider_var.get() or AI_PROVIDER_DISABLED)
        self._store_current_ai_token()
        ai_token = ""
        if ai_provider != AI_PROVIDER_DISABLED:
            ai_token = self._ai_token_for_provider(ai_provider).strip()
            if not ai_token:
                if SHOW_AI_CONTROLS:
                    messagebox.showerror(
                        "Missing AI API Token",
                        f"Enter a {ai_provider} API token or disable AI analysis.",
                    )
                else:
                    messagebox.showerror(
                        "Missing Report Token",
                        "A configured supplemental report provider is missing its API token.",
                    )
                return

        if self.remember_api_keys_var.get():
            self._save_api_keys(show_message=False)

        limit_label = "all transactions" if max_transactions is None else f"up to {max_transactions} transactions"
        self.log(f"Starting trace ({limit_label})...")
        threading.Thread(
            target=self._run_trace_worker,
            args=(
                seed,
                max_transactions,
                export_json_enabled,
                export_xlsx_enabled,
                export_graphml_enabled,
                ai_provider,
                ai_token,
            ),
            daemon=True,
        ).start()

    def _run_trace_worker(
        self,
        seed: str,
        max_transactions: Optional[int],
        export_json_enabled: bool,
        export_xlsx_enabled: bool,
        export_graphml_enabled: bool,
        ai_provider: str,
        ai_token: str,
    ) -> None:
        try:
            provider = ProviderRouter(
                blockcypher_key=(self.blockcypher_key_var.get().strip() or None),
                blockchair_key=(self.blockchair_key_var.get().strip() or None),
                coinstats_key=(self.coinstats_key_var.get().strip() or None),
            )
            intel = IntelligenceStore(APP_ROOT / "intel")
            self.log(
                f"Intel loaded: sanctions={len(intel.sanctioned)} mixers={len(intel.mixers)} owners={len(intel.owners)}"
            )
            analyzer = Analyzer(provider=provider, intel=intel)

            asset = self.asset_var.get().strip().upper()
            mode = self.mode_var.get().strip()
            self._log_seed_list_warning(seed=seed, mode=mode, intel=intel)

            if mode == "tx":
                result = analyzer.trace_from_transaction(
                    asset=asset,
                    txid=seed,
                    max_transactions=max_transactions,
                )
                prefix = f"tx_{asset}_{self._safe_filename_part(seed)}"
            else:
                result = analyzer.trace_from_address(
                    asset=asset,
                    address=seed,
                    max_transactions=max_transactions,
                )
                prefix = f"addr_{asset}_{self._safe_filename_part(seed)}"

            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            base_dir = APP_ROOT / "exports"
            case_dir = base_dir / f"{prefix}_{ts}"
            case_dir.mkdir(parents=True, exist_ok=True)
            json_path = case_dir / "trace.json"
            xlsx_path = case_dir / "trace.xlsx"
            graphml_path = case_dir / "trace.graphml"

            if not any((export_json_enabled, export_xlsx_enabled, export_graphml_enabled)):
                self.log("No exports selected; skipping file export.")
            else:
                self.log(f"Export folder: {case_dir.resolve()}")

            if export_json_enabled:
                try:
                    export_json(result, json_path)
                    self.log(f"Saved JSON export: {json_path.resolve()}")
                except Exception as exc:
                    self.log(f"JSON export failed: {exc}", level="error")
                    export_json_enabled = False
            if export_xlsx_enabled:
                try:
                    export_xlsx(result, xlsx_path)
                    self.log(f"Saved XLSX export: {xlsx_path.resolve()}")
                except Exception as exc:
                    self.log(f"XLSX export failed: {exc}", level="error")
                    export_xlsx_enabled = False
            if export_graphml_enabled:
                try:
                    export_graphml(result, graphml_path)
                    self.log(f"Saved GraphML export: {graphml_path.resolve()}")
                except Exception as exc:
                    self.log(f"GraphML export failed: {exc}", level="error")
                    export_graphml_enabled = False

            ai_report_paths = {}
            if ai_provider != AI_PROVIDER_DISABLED:
                try:
                    if SHOW_AI_CONTROLS:
                        self.log(f"Running AI analysis with {ai_provider}...")
                    else:
                        self.log("Generating supplemental analysis report...")
                    ai_report_paths = generate_ai_analysis_report(
                        result=result,
                        seed=seed,
                        mode=mode,
                        provider=ai_provider,
                        api_token=ai_token,
                        output_dir=case_dir,
                    )
                    if SHOW_AI_CONTROLS:
                        self.log(f"Saved AI analysis DOCX: {ai_report_paths['docx'].resolve()}")
                        self.log(f"Saved AI trend chart: {ai_report_paths['trend_chart'].resolve()}")
                        self.log(f"Saved AI Sankey chart: {ai_report_paths['sankey_chart'].resolve()}")
                    else:
                        self.log(f"Saved supplemental report DOCX: {ai_report_paths['docx'].resolve()}")
                        self.log(f"Saved trend chart: {ai_report_paths['trend_chart'].resolve()}")
                        self.log(f"Saved flow chart: {ai_report_paths['sankey_chart'].resolve()}")
                except AIAnalysisError as exc:
                    if SHOW_AI_CONTROLS:
                        self.log(f"AI analysis failed: {exc}", level="error")
                    else:
                        self.log("Supplemental report generation failed.", level="error")
                except Exception as exc:
                    if SHOW_AI_CONTROLS:
                        self.log(f"AI analysis failed: {exc}", level="error")
                    else:
                        self.log("Supplemental report generation failed.", level="error")

            meta = result.get("metadata", {})
            self.log("Trace complete.")
            self.log(f"Asset: {meta.get('asset')}")
            self.log(f"Transaction limit: {meta.get('transaction_limit')}")
            self.log(f"Transactions: {meta.get('transaction_count')} | Nodes: {meta.get('node_count')} | Edges: {meta.get('edge_count')}")
            self.log(f"Findings: {meta.get('finding_count')}")
            self.log(f"JSON: {json_path if export_json_enabled else 'skipped'}")
            self.log(f"XLSX: {xlsx_path if export_xlsx_enabled else 'skipped'}")
            self.log(f"GraphML: {graphml_path if export_graphml_enabled else 'skipped'}")

            self._log_transaction_details(result)
            self._log_account_details(result)
            self._log_list_warnings(result)

            findings = result.get("findings", [])[:20]
            if findings:
                self.log("Top findings:")
                for f in findings:
                    level = str(f.get("level", "info")).lower()
                    finding_type = str(f.get("finding_type", ""))
                    line = f"- [{level}] {finding_type} | {f.get('subject')}"
                    if finding_type in {"sanctioned_address", "known_mixer"}:
                        self.log(line, level="warning")
                    else:
                        self.log(line, level=level if level in {"info", "warning", "error"} else "info")

        except ProviderError as exc:
            self.log(f"Provider error: {exc}")
        except Exception as exc:
            self.log(f"Trace failed: {exc}")

    def run_update(self) -> None:
        if self.update_in_progress:
            self.log("Update already running. Please wait for it to finish.")
            return

        self._set_update_busy(True)
        self.log("Starting sanctions + mixer update...")
        threading.Thread(target=self._run_update_worker, daemon=True).start()

    def _run_update_worker(self) -> None:
        def _progress(percent: int, message: str) -> None:
            self.after(0, lambda: self._set_update_progress(percent, message))

        try:
            report = sync_sanctions(
                output_path=APP_ROOT / "intel" / "sanctioned_addresses.csv",
                mixer_output_path=APP_ROOT / "intel" / "known_mixers.csv",
                metadata_path=APP_ROOT / "intel" / "sanctions_sync_meta.json",
                max_age_hours=24,
                force=True,
                progress_callback=_progress,
            )
            self.log(report.detail)
            self.log(f"Sanctions addresses: {report.address_count} from {report.file_count} files")
            self.log(f"Mixer addresses: {report.mixer_count} from {report.mixer_source_count} sources")
            self.log(f"Sanctions CSV: {report.output_path}")
            self.log(f"Mixers CSV: {report.mixer_output_path}")
        except SanctionsSyncError as exc:
            self.log(f"Update failed: {exc}")
        except Exception as exc:
            self.log(f"Update failed: {exc}")
        finally:
            self.after(0, lambda: self._set_update_busy(False))

    def _set_update_busy(self, busy: bool) -> None:
        self.update_in_progress = busy
        if busy:
            self.update_button.configure(state="disabled")
            self._set_update_progress(0, "Updating sanctions...")
        else:
            self.update_button.configure(state="normal")
            self.update_status_var.set("Idle")

    def _set_update_progress(self, percent: int, message: str) -> None:
        bounded = max(0, min(100, int(percent)))
        self.update_progress_var.set(bounded)
        self.update_status_var.set(f"{message} ({bounded}%)")

    def clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    @staticmethod
    def _safe_filename_part(value: str, max_len: int = 20) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
        cleaned = cleaned.strip("._-")
        if not cleaned:
            cleaned = "seed"
        return cleaned[:max_len]

    def _log_transaction_details(self, result: dict) -> None:
        transactions = result.get("transactions", [])
        if not transactions:
            return

        self.log("Transaction data:")
        max_tx = 10
        max_io = 5
        for tx in transactions[:max_tx]:
            txid = tx.get("txid", "unknown")
            timestamp = tx.get("timestamp", "unknown")
            fee = tx.get("fee")
            self.log(f"- TX {txid} | time={timestamp} | fee={fee if fee is not None else 'n/a'}")

            inputs = tx.get("inputs", [])
            outputs = tx.get("outputs", [])

            for item in inputs[:max_io]:
                self.log(f"  IN  {item.get('address', 'unknown')} | {item.get('amount', 0)}")
            if len(inputs) > max_io:
                self.log(f"  IN  ... +{len(inputs) - max_io} more")

            for item in outputs[:max_io]:
                self.log(f"  OUT {item.get('address', 'unknown')} | {item.get('amount', 0)}")
            if len(outputs) > max_io:
                self.log(f"  OUT ... +{len(outputs) - max_io} more")

        if len(transactions) > max_tx:
            self.log(f"... +{len(transactions) - max_tx} more transactions")

    def _log_account_details(self, result: dict) -> None:
        nodes = result.get("graph", {}).get("nodes", [])
        findings = result.get("findings", [])
        addresses = sorted(
            n.get("id", "")[5:]
            for n in nodes
            if isinstance(n, dict) and str(n.get("id", "")).startswith("addr:")
        )
        if not addresses:
            return
        address_set = set(addresses)

        address_findings = {}
        for finding in findings:
            subject = str(finding.get("subject", ""))
            if subject not in address_set:
                continue
            address_findings.setdefault(subject, []).append(finding.get("finding_type", "flag"))

        self.log("Account/address data:")
        max_addr = 20
        for addr in addresses[:max_addr]:
            flags = address_findings.get(addr, [])
            if flags:
                unique_flags = ",".join(sorted(set(str(f) for f in flags)))
                self.log(f"- {addr} | flags={unique_flags}")
            else:
                self.log(f"- {addr}")
        if len(addresses) > max_addr:
            self.log(f"... +{len(addresses) - max_addr} more addresses")

    def _log_list_warnings(self, result: dict) -> None:
        findings = result.get("findings", [])
        sanctioned = sorted(
            {
                str(f.get("subject", "")).strip()
                for f in findings
                if f.get("finding_type") == "sanctioned_address" and str(f.get("subject", "")).strip()
            }
        )
        mixers = sorted(
            {
                str(f.get("subject", "")).strip()
                for f in findings
                if f.get("finding_type") == "known_mixer" and str(f.get("subject", "")).strip()
            }
        )
        if not sanctioned and not mixers:
            return

        self.log("WARNING: Known bad address match detected.", level="warning")
        if sanctioned:
            self.log(
                f"WARNING: {len(sanctioned)} address(es) appear on the sanctions list.",
                level="warning",
            )
            for addr in sanctioned[:20]:
                self.log(f"  SANCTIONED: {addr}", level="warning")
        if mixers:
            self.log(
                f"WARNING: {len(mixers)} address(es) appear on the mixer list.",
                level="warning",
            )
            for addr in mixers[:20]:
                self.log(f"  MIXER: {addr}", level="warning")

    def _log_seed_list_warning(self, seed: str, mode: str, intel: IntelligenceStore) -> None:
        if mode != "address":
            return
        addr = str(seed or "").strip()
        if not addr:
            return
        addr_norm = normalize_lookup_address(addr)

        sanctioned_source = intel.sanctioned.get(addr) or intel.sanctioned.get(addr_norm)
        mixer_name = intel.mixers.get(addr) or intel.mixers.get(addr_norm)

        if sanctioned_source:
            self.log(
                f"WARNING: Entered address is on sanctions list: {addr} | source={sanctioned_source}",
                level="warning",
            )
        if mixer_name:
            self.log(
                f"WARNING: Entered address is on mixer list: {addr} | mixer={mixer_name}",
                level="warning",
            )

    def log(self, message: str, level: str = "info") -> None:
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self.log(message, level=level))
            return
        tag = level if level in {"info", "warning", "error"} else "info"
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {message}\n", tag)
        self.log_text.see(tk.END)


def main() -> int:
    _debug_log(f"main start frozen={IS_FROZEN} resource_root={RESOURCE_ROOT} app_root={APP_ROOT}")
    app = AMLGui()
    _debug_log("AMLGui constructed")
    app.mainloop()
    _debug_log("mainloop exited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
