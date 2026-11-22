"""The Tk login presentation: one window per sign-in, with the same contract as the terminal presentation.

``prompt_credentials(*, title, header, width) -> (moniker, password)``. The window shows the header as a label, a
``moniker:`` entry, a ``password:`` entry masked with ``*``, and a Login button. Enter in the password field, or the Login
button, returns the two values; a blank field keeps the window open. Escape or closing the window raises
``CredentialsCancelled``. The window is destroyed on every path. The password is not checked here: the caller logs in
over the wire, so every presentation gets the same daemon check.

``tkinter`` is imported only when the prompt runs, so a terminal-only install does not need it.
"""

from __future__ import annotations

from typing import Optional

from bed.tools.presentation import CredentialsCancelled


def _tk():
    """(Tk, StringVar, Entry-like widget class). Imported here so the module loads without a display."""
    import tkinter as tk
    from tkinter import ttk

    return tk.Tk, tk.StringVar, (tk, ttk)


def prompt_credentials(*, title: str = "login", header: Optional[str] = None, width: int = 25,
                       **kwargs) -> tuple[str, str]:
    """Ask for a moniker and a password in one Tk window. Returns ``(moniker, password)``.

    Raises CredentialsCancelled on Escape or when the window is closed. ``**kwargs`` is accepted so the configured
    presentation keeps matching the contract; extra options are ignored.
    """
    make_root, make_var, (tk, ttk) = _tk_components()
    root = make_root()
    outcome: dict = {}
    try:
        root.title(title)
        if header:
            ttk.Label(root, text=header).grid(column=0, row=0, columnspan=2, sticky=tk.W, padx=5, pady=5)

        moniker_var = make_var()
        password_var = make_var()
        entries: dict = {}

        ttk.Label(root, text="moniker:").grid(column=0, row=1, sticky=tk.W, padx=5, pady=5)
        moniker_entry = ttk.Entry(root, textvariable=moniker_var, width=max(8, width - 6))
        moniker_entry.grid(column=1, row=1, sticky=tk.E, padx=5, pady=5)
        entries["moniker"] = moniker_var

        ttk.Label(root, text="password:").grid(column=0, row=2, sticky=tk.W, padx=5, pady=5)
        password_entry = ttk.Entry(root, textvariable=password_var, show="*", width=max(8, width - 6))
        password_entry.grid(column=1, row=2, sticky=tk.E, padx=5, pady=5)
        entries["password"] = password_var

        def submit(*_):
            moniker = moniker_var.get().strip()
            password = password_var.get()
            if not moniker or not password:
                # A blank field keeps the window open, as the terminal presentation does.
                return
            outcome["result"] = (moniker, password)
            root.destroy()

        def cancel(*_):
            outcome["cancelled"] = True
            root.destroy()

        login = ttk.Button(root, text="Login", command=submit)
        login.grid(column=1, row=3, sticky=tk.E, padx=5, pady=5)

        root.bind("<Return>", submit)
        root.bind("<Escape>", cancel)
        root.protocol("WM_DELETE_WINDOW", cancel)
        moniker_entry.focus_set()

        # Test hook: a fake root that exposes ``entries`` receives the field variables. A real Tk root has no such attribute.
        if hasattr(root, "entries"):
            root.entries.update(entries)

        root.mainloop()
    finally:
        root.destroy()

    if outcome.get("cancelled"):
        raise CredentialsCancelled()
    if "result" not in outcome:
        raise CredentialsCancelled()
    return outcome["result"]


def _tk_components():
    """The Tk root factory, StringVar factory, and the tk / ttk modules. Split out so tests can replace it."""
    make_root, make_var, modules = _tk()
    return make_root, make_var, modules
