"""Settings and Configuration View for video2book Desktop GUI."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import customtkinter as ctk

from video2book.extractor import check_ffmpeg_installed
from video2book.gui.utils import add_context_menu
from video2book.openrouter import (
    DEFAULT_MODEL,
    SYSTEM_INSTRUCTION,
    USER_PROMPT,
    fetch_openrouter_gemini_models,
    validate_openrouter_key,
)
from video2book.renderer import find_headless_chromium_binary

if TYPE_CHECKING:
    from video2book.gui.app import Video2BookApp


class SettingsView(ctk.CTkFrame):
    """View to configure OpenRouter API, Gemini models, prompts, FFmpeg, and theme settings."""

    def __init__(self, master: Any, app: Video2BookApp):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.app = app

        self._show_key: bool = False
        self._available_models: list[str] = [DEFAULT_MODEL]
        self._build_ui()
        self._load_from_config()
        # Fetch latest Gemini models from OpenRouter in background
        self._async_refresh_models()

    def _build_ui(self) -> None:
        """Construct the settings tabs and form fields."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, corner_radius=10)
        scroll.grid(row=0, column=0, sticky="nsew", padx=15, pady=15)
        scroll.grid_columnconfigure(0, weight=1)

        # ---------------- Section 1: OpenRouter API ----------------
        sec_api = ctk.CTkFrame(scroll, corner_radius=10)
        sec_api.pack(fill="x", padx=10, pady=(5, 10))
        sec_api.grid_columnconfigure(1, weight=1)

        lbl_api_hdr = ctk.CTkLabel(
            sec_api,
            text="🔑 OpenRouter API Settings (https://openrouter.ai)",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_api_hdr.grid(row=0, column=0, columnspan=3, sticky="w", padx=15, pady=(12, 8))

        # API Key
        lbl_key = ctk.CTkLabel(sec_api, text="API Key:")
        lbl_key.grid(row=1, column=0, sticky="w", padx=15, pady=6)

        self.entry_key = ctk.CTkEntry(
            sec_api,
            placeholder_text="Enter OpenRouter API Key (sk-or-v1-...) or set OPENROUTER_API_KEY",
            show="•",
            height=32,
        )
        self.entry_key.grid(row=1, column=1, sticky="ew", padx=5, pady=6)
        add_context_menu(self.entry_key)
        self.entry_key.bind("<FocusOut>", lambda e: self._on_key_changed())

        key_actions = ctk.CTkFrame(sec_api, fg_color="transparent")
        key_actions.grid(row=1, column=2, padx=(5, 15), pady=6, sticky="e")

        self.btn_paste_key = ctk.CTkButton(
            key_actions,
            text="📋 Paste",
            width=75,
            height=32,
            fg_color="#1F6FEB",
            hover_color="#388BFD",
            command=self._on_paste_key,
        )
        self.btn_paste_key.pack(side="left", padx=2)

        self.btn_toggle_key = ctk.CTkButton(
            key_actions,
            text="👁 Show",
            width=70,
            height=32,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_toggle_show_key,
        )
        self.btn_toggle_key.pack(side="left", padx=2)

        self.btn_clear_key = ctk.CTkButton(
            key_actions,
            text="✕ Clear",
            width=65,
            height=32,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_clear_key,
        )
        self.btn_clear_key.pack(side="left", padx=2)

        # Test Connection button & status
        btn_test = ctk.CTkButton(
            sec_api,
            text="Test OpenRouter Key",
            width=170,
            command=self._on_test_connection,
        )
        btn_test.grid(row=2, column=1, sticky="w", padx=5, pady=6)

        self.lbl_conn_status = ctk.CTkLabel(
            sec_api,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_conn_status.grid(row=2, column=1, sticky="e", padx=10, pady=6)

        # Save key on disk toggle
        self.switch_save_api_key = ctk.CTkSwitch(
            sec_api,
            text="Remember API key on this computer (saved to ~/.video2book/gui_config.json)",
            font=ctk.CTkFont(size=12),
            command=self._on_save_key_switch_changed,
        )
        self.switch_save_api_key.grid(row=3, column=1, columnspan=2, sticky="w", padx=5, pady=(4, 12))

        # ---------------- Section 2: Model & Performance ----------------
        sec_model = ctk.CTkFrame(scroll, corner_radius=10)
        sec_model.pack(fill="x", padx=10, pady=10)
        sec_model.grid_columnconfigure(1, weight=1)

        lbl_m_hdr = ctk.CTkLabel(
            sec_model,
            text="⚡ Gemini Model Selection (from https://openrouter.ai/models)",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_m_hdr.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(12, 8))

        # Model Choice
        lbl_m = ctk.CTkLabel(sec_model, text="Gemini Model:")
        lbl_m.grid(row=1, column=0, sticky="w", padx=15, pady=6)

        model_row = ctk.CTkFrame(sec_model, fg_color="transparent")
        model_row.grid(row=1, column=1, sticky="ew", padx=5, pady=6)
        model_row.grid_columnconfigure(0, weight=1)

        self.menu_model = ctk.CTkOptionMenu(
            model_row,
            values=[
                "google/gemini-2.5-flash",
                "google/gemini-2.5-pro",
                "google/gemini-3.8-flash",
                "google/gemini-3.7-flash",
                "google/gemini-3.5-flash",
                "google/gemini-3.5-flash-lite",
                "google/gemini-2.0-flash-001",
            ],
            width=280,
        )
        self.menu_model.grid(row=0, column=0, sticky="w")

        self.btn_refresh_models = ctk.CTkButton(
            model_row,
            text="↻ Fetch Models from OpenRouter",
            width=210,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._async_refresh_models,
        )
        self.btn_refresh_models.grid(row=0, column=1, padx=(10, 0))

        self.lbl_models_status = ctk.CTkLabel(
            model_row,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E",
        )
        self.lbl_models_status.grid(row=0, column=2, padx=10)

        # Video Extensions
        lbl_ext = ctk.CTkLabel(sec_model, text="File Extensions:")
        lbl_ext.grid(row=2, column=0, sticky="w", padx=15, pady=6)

        self.entry_ext = ctk.CTkEntry(
            sec_model,
            placeholder_text="mp4,mkv,mov,webm,avi",
            height=32,
        )
        self.entry_ext.grid(row=2, column=1, sticky="ew", padx=(5, 15), pady=6)
        add_context_menu(self.entry_ext)

        # Poll interval
        lbl_poll = ctk.CTkLabel(sec_model, text="Poll Interval (s):")
        lbl_poll.grid(row=3, column=0, sticky="w", padx=15, pady=6)

        self.entry_poll = ctk.CTkEntry(
            sec_model,
            placeholder_text="5.0",
            width=100,
            height=32,
        )
        self.entry_poll.grid(row=3, column=1, sticky="w", padx=5, pady=(6, 12))
        add_context_menu(self.entry_poll)

        # ---------------- Section 3: Prompts & Instructions ----------------
        sec_prompts = ctk.CTkFrame(scroll, corner_radius=10)
        sec_prompts.pack(fill="x", padx=10, pady=10)
        sec_prompts.grid_columnconfigure(0, weight=1)

        prompt_hdr = ctk.CTkFrame(sec_prompts, fg_color="transparent")
        prompt_hdr.pack(fill="x", padx=15, pady=(12, 4))
        prompt_hdr.grid_columnconfigure(0, weight=1)

        lbl_p_title = ctk.CTkLabel(
            prompt_hdr,
            text="📝 Custom System & User Prompts",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_p_title.grid(row=0, column=0, sticky="w")

        btn_reset_p = ctk.CTkButton(
            prompt_hdr,
            text="Reset to Defaults",
            width=120,
            height=26,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._on_reset_prompts,
        )
        btn_reset_p.grid(row=0, column=1)

        lbl_sys = ctk.CTkLabel(
            sec_prompts,
            text="System Instruction (Textbook Author Persona & Snapshot Instructions):",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl_sys.pack(fill="x", padx=15, pady=(4, 2))

        self.txt_sys_instruction = ctk.CTkTextbox(
            sec_prompts,
            height=140,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self.txt_sys_instruction.pack(fill="x", padx=15, pady=(2, 8))
        add_context_menu(self.txt_sys_instruction)

        lbl_usr = ctk.CTkLabel(
            sec_prompts,
            text="User Prompt:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl_usr.pack(fill="x", padx=15, pady=(4, 2))

        self.txt_user_prompt = ctk.CTkTextbox(
            sec_prompts,
            height=60,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self.txt_user_prompt.pack(fill="x", padx=15, pady=(2, 12))
        add_context_menu(self.txt_user_prompt)

        # ---------------- Section 4: Book & PDF Output ----------------
        sec_book = ctk.CTkFrame(scroll, corner_radius=10)
        sec_book.pack(fill="x", padx=10, pady=10)
        sec_book.grid_columnconfigure(1, weight=1)

        lbl_book_hdr = ctk.CTkLabel(
            sec_book,
            text="📖 Book & PDF Output Options",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_book_hdr.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(12, 8))

        self.switch_auto_html = ctk.CTkSwitch(
            sec_book,
            text="Auto-generate Interactive HTML Web Book (.html)",
            font=ctk.CTkFont(size=13),
        )
        self.switch_auto_html.grid(row=1, column=0, columnspan=2, sticky="w", padx=15, pady=6)

        self.switch_auto_pdf = ctk.CTkSwitch(
            sec_book,
            text="Auto-compile Publication PDF Book (.pdf) via Edge/Chromium",
            font=ctk.CTkFont(size=13),
        )
        self.switch_auto_pdf.grid(row=2, column=0, columnspan=2, sticky="w", padx=15, pady=6)

        self.switch_standalone_html = ctk.CTkSwitch(
            sec_book,
            text="Standalone HTML (Embed images in Base64 for 1-file sharing)",
            font=ctk.CTkFont(size=13),
        )
        self.switch_standalone_html.grid(row=3, column=0, columnspan=2, sticky="w", padx=15, pady=6)

        self.switch_translate_fa = ctk.CTkSwitch(
            sec_book,
            text="Auto-translate to Persian (Keep technical terms in English)",
            font=ctk.CTkFont(size=13),
        )
        self.switch_translate_fa.grid(row=4, column=0, columnspan=2, sticky="w", padx=15, pady=6)

        # PDF Engine Status
        lbl_pdf_engine = ctk.CTkLabel(sec_book, text="PDF Engine:")
        lbl_pdf_engine.grid(row=5, column=0, sticky="w", padx=15, pady=(6, 12))

        browser_bin = find_headless_chromium_binary()
        if browser_bin:
            b_name = Path(browser_bin).name
            engine_text = f"✓ Ready ({b_name})"
            engine_color = "#2EA043"
        else:
            engine_text = "✗ Missing (Microsoft Edge or Chrome recommended)"
            engine_color = "#DA3633"

        lbl_engine_val = ctk.CTkLabel(
            sec_book,
            text=engine_text,
            text_color=engine_color,
            font=ctk.CTkFont(weight="bold"),
        )
        lbl_engine_val.grid(row=5, column=1, sticky="w", padx=5, pady=(6, 12))

        # ---------------- Section 5: System Diagnostics & Theme ----------------
        sec_diag = ctk.CTkFrame(scroll, corner_radius=10)
        sec_diag.pack(fill="x", padx=10, pady=10)
        sec_diag.grid_columnconfigure(1, weight=1)

        lbl_diag_hdr = ctk.CTkLabel(
            sec_diag,
            text="🛠 System Diagnostics & Appearance",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        lbl_diag_hdr.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(12, 8))

        # FFmpeg status
        lbl_ff = ctk.CTkLabel(sec_diag, text="System FFmpeg:")
        lbl_ff.grid(row=1, column=0, sticky="w", padx=15, pady=6)

        ff_ok, ff_info = check_ffmpeg_installed()
        ff_text = f"✓ Ready ({ff_info})" if ff_ok else "✗ Missing (Install via: winget install Gyan.FFmpeg)"
        ff_color = "#2EA043" if ff_ok else "#DA3633"

        lbl_ff_val = ctk.CTkLabel(
            sec_diag,
            text=ff_text,
            text_color=ff_color,
            font=ctk.CTkFont(weight="bold"),
        )
        lbl_ff_val.grid(row=1, column=1, sticky="w", padx=5, pady=6)

        # Theme selector
        lbl_theme = ctk.CTkLabel(sec_diag, text="Appearance Mode:")
        lbl_theme.grid(row=2, column=0, sticky="w", padx=15, pady=6)

        self.menu_theme = ctk.CTkOptionMenu(
            sec_diag,
            values=["Dark", "Light", "System"],
            command=self._on_theme_changed,
            width=140,
        )
        self.menu_theme.grid(row=2, column=1, sticky="w", padx=5, pady=(6, 12))

        # ---------------- Section 5: Save Actions ----------------
        save_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        save_frame.pack(fill="x", padx=10, pady=(10, 20))
        save_frame.grid_columnconfigure(0, weight=1)

        self.btn_save = ctk.CTkButton(
            save_frame,
            text="💾 Save All Settings",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2EA043",
            hover_color="#238636",
            height=40,
            command=self.save_settings,
        )
        self.btn_save.pack(side="left", padx=5)

        self.lbl_save_status = ctk.CTkLabel(
            save_frame,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#2EA043",
        )
        self.lbl_save_status.pack(side="left", padx=15)

    # ---------------- Load / Save ----------------

    def _load_from_config(self) -> None:
        """Populate inputs with current application configuration."""
        cfg = self.app.config
        # Key: prioritize saved config or env
        key = cfg.api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        self.entry_key.delete(0, "end")
        self.entry_key.insert(0, key)

        # Model
        if cfg.model:
            self.menu_model.set(cfg.model)

        # Extensions
        self.entry_ext.delete(0, "end")
        self.entry_ext.insert(0, cfg.extensions or "mp4,mkv,mov,webm,avi")

        # Poll
        self.entry_poll.delete(0, "end")
        self.entry_poll.insert(0, str(cfg.poll_interval or 5.0))

        # Prompts
        self.txt_sys_instruction.delete("1.0", "end")
        self.txt_sys_instruction.insert("1.0", cfg.custom_system_instruction or SYSTEM_INSTRUCTION)

        self.txt_user_prompt.delete("1.0", "end")
        self.txt_user_prompt.insert("1.0", cfg.custom_user_prompt or USER_PROMPT)

        # Theme
        self.menu_theme.set(cfg.theme or "Dark")

        # Book & PDF Switches
        if getattr(cfg, "auto_generate_html", True):
            self.switch_auto_html.select()
        else:
            self.switch_auto_html.deselect()

        if getattr(cfg, "auto_generate_pdf", True):
            self.switch_auto_pdf.select()
        else:
            self.switch_auto_pdf.deselect()

        if getattr(cfg, "standalone_html", False):
            self.switch_standalone_html.select()
        else:
            self.switch_standalone_html.deselect()

        if getattr(cfg, "translate_persian", True):
            self.switch_translate_fa.select()
        else:
            self.switch_translate_fa.deselect()

        if getattr(cfg, "save_api_key", True):
            self.switch_save_api_key.select()
        else:
            self.switch_save_api_key.deselect()

    def _async_refresh_models(self) -> None:
        """Fetch live Gemini models from https://openrouter.ai/models in background thread."""
        self.lbl_models_status.configure(text="Fetching models...", text_color="#E3B341")

        def _fetch_bg() -> None:
            models_data = fetch_openrouter_gemini_models()
            model_ids = [m["id"] for m in models_data]

            def _update_ui():
                if model_ids:
                    current = self.menu_model.get()
                    self.menu_model.configure(values=model_ids)
                    if current in model_ids:
                        self.menu_model.set(current)
                    elif DEFAULT_MODEL in model_ids:
                        self.menu_model.set(DEFAULT_MODEL)
                    self.lbl_models_status.configure(
                        text=f"✓ {len(model_ids)} Gemini models loaded",
                        text_color="#2EA043",
                    )
                else:
                    self.lbl_models_status.configure(text="Using default models", text_color="#8B949E")

            try:
                if self.winfo_exists():
                    self.after(0, _update_ui)
            except Exception:
                pass

        threading.Thread(target=_fetch_bg, daemon=True).start()

    def save_settings(self) -> None:
        """Save settings to persistent JSON file and update live app state."""
        self.app.save_current_settings()
        self.lbl_save_status.configure(text="✓ Settings saved successfully!", text_color="#2EA043")
        self.after(3000, lambda: self.lbl_save_status.configure(text=""))

    # ---------------- Event Handlers ----------------

    def _on_key_changed(self) -> None:
        """Auto-save when API key entry loses focus."""
        self.app.config.api_key = self.entry_key.get().strip()
        self.app.save_current_settings()

    def _on_save_key_switch_changed(self) -> None:
        """Auto-save when save_api_key switch is toggled."""
        self.app.config.save_api_key = bool(self.switch_save_api_key.get())
        self.app.save_current_settings()

    def _on_paste_key(self) -> None:
        """Paste clipboard text directly into the API key field and auto-save."""
        try:
            clip = self.clipboard_get()
            if clip:
                cleaned = clip.strip()
                self.entry_key.delete(0, "end")
                self.entry_key.insert(0, cleaned)
                self.lbl_conn_status.configure(text="Pasted from clipboard", text_color="#58A6FF")
                # Immediately update active config
                self.app.config.api_key = cleaned
                self.app.save_current_settings()
            else:
                self.lbl_conn_status.configure(text="Clipboard is empty", text_color="#E3B341")
        except Exception:
            self.lbl_conn_status.configure(text="Clipboard inaccessible / empty", text_color="#E3B341")

    def _on_clear_key(self) -> None:
        """Clear the API key input and active config."""
        self.entry_key.delete(0, "end")
        self.app.config.api_key = ""
        self.app.save_current_settings()
        self.lbl_conn_status.configure(text="API Key cleared", text_color="#8B949E")

    def _on_toggle_show_key(self) -> None:
        self._show_key = not self._show_key
        if self._show_key:
            self.entry_key.configure(show="")
            self.btn_toggle_key.configure(text="🔒 Hide")
        else:
            self.entry_key.configure(show="•")
            self.btn_toggle_key.configure(text="👁 Show")

    def _on_theme_changed(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.app.config.theme = mode
        self.app.save_current_settings()

    def _on_reset_prompts(self) -> None:
        self.txt_sys_instruction.delete("1.0", "end")
        self.txt_sys_instruction.insert("1.0", SYSTEM_INSTRUCTION)
        self.txt_user_prompt.delete("1.0", "end")
        self.txt_user_prompt.insert("1.0", USER_PROMPT)
        self.app.save_current_settings()

    def _on_test_connection(self) -> None:
        """Validate OpenRouter API key in a background thread."""
        key = self.entry_key.get().strip() or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            self.lbl_conn_status.configure(text="✗ No API key provided", text_color="#DA3633")
            return

        self.lbl_conn_status.configure(text="Validating key...", text_color="#E3B341")

        def _test_bg() -> None:
            valid, msg = validate_openrouter_key(key)
            col = "#2EA043" if valid else "#DA3633"
            try:
                if self.winfo_exists():
                    self.after(0, lambda: self.lbl_conn_status.configure(text=msg[:40], text_color=col))
            except Exception:
                pass

        threading.Thread(target=_test_bg, daemon=True).start()
