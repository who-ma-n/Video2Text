"""UI utilities for clipboard, context menus, and keyboard shortcuts."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Optional
import customtkinter as ctk


def add_context_menu(
    widget: Any,
    on_paste: Optional[Callable[[str], None]] = None,
) -> tk.Menu:
    """Attach a right-click context menu (Cut, Copy, Paste, Select All) and explicit paste shortcuts.

    Handles international keyboard layouts on Windows where default Ctrl+V keysyms may not map.
    """
    target = getattr(widget, "_entry", getattr(widget, "_textbox", widget))

    menu = tk.Menu(target, tearoff=0)

    def do_cut(event: Any = None) -> str:
        try:
            if isinstance(widget, ctk.CTkEntry):
                if target.select_present():
                    target.clipboard_clear()
                    target.clipboard_append(target.selection_get())
                    target.delete("sel.first", "sel.last")
            else:
                target.event_generate("<<Cut>>")
        except Exception:
            pass
        return "break"

    def do_copy(event: Any = None) -> str:
        try:
            if isinstance(widget, ctk.CTkEntry):
                if target.select_present():
                    target.clipboard_clear()
                    target.clipboard_append(target.selection_get())
            else:
                target.event_generate("<<Copy>>")
        except Exception:
            pass
        return "break"

    def do_paste(event: Any = None) -> str:
        try:
            text = target.clipboard_get()
            if text:
                if isinstance(widget, ctk.CTkEntry):
                    cleaned = text.strip()
                    # If text selected, replace selection
                    if target.select_present():
                        target.delete("sel.first", "sel.last")
                    target.insert(target.index("insert"), cleaned)
                else:
                    target.insert("insert", text)

                if on_paste:
                    on_paste(text)
        except Exception:
            try:
                target.event_generate("<<Paste>>")
            except Exception:
                pass
        return "break"

    def do_select_all(event: Any = None) -> str:
        try:
            if isinstance(widget, ctk.CTkEntry):
                target.select_range(0, "end")
                target.icursor("end")
            else:
                target.tag_add("sel", "1.0", "end")
        except Exception:
            pass
        return "break"

    menu.add_command(label="Cut", command=do_cut)
    menu.add_command(label="Copy", command=do_copy)
    menu.add_command(label="Paste", command=do_paste)
    menu.add_separator()
    menu.add_command(label="Select All", command=do_select_all)

    def show_popup(event: Any) -> None:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # Bind right-click on both wrapper and inner widget
    target.bind("<Button-3>", show_popup)
    try:
        widget.bind("<Button-3>", show_popup)
    except Exception:
        pass

    # Bind explicit keyboard paste variants
    for key_combo in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
        target.bind(key_combo, do_paste)
        try:
            widget.bind(key_combo, do_paste)
        except Exception:
            pass

    # Bind Ctrl+A (Select All)
    for key_combo in ("<Control-a>", "<Control-A>"):
        target.bind(key_combo, do_select_all)
        try:
            widget.bind(key_combo, do_select_all)
        except Exception:
            pass

    return menu
