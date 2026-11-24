"""The Tk login presentation, tested headlessly: tkinter is replaced by a fake, so no display is needed.

Contract: prompt_credentials(*, title, header, width) -> (moniker, password). Enter in the password field or the Login
button returns the two values. A blank field keeps the window open. Escape or closing the window raises
CredentialsCancelled. The window is destroyed on every path. The presentation does not check the password itself.
"""

import importlib
import inspect
import types
import unittest
from unittest.mock import patch

tk_mod = importlib.import_module("bed.tools.presentation_tk")
from bed.tools.presentation import CredentialsCancelled


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.bindings = {}

    def grid(self, **kwargs):
        pass

    def pack(self, **kwargs):
        pass

    def bind(self, sequence, func):
        self.bindings[sequence] = func

    def focus_set(self):
        pass

    def delete(self, *args):
        pass


class FakeButton(FakeWidget):
    """A button records its command so the test can press it."""

    last = None

    def __init__(self, root=None, text="", command=None, **kwargs):
        super().__init__(root, **kwargs)
        self.command = command
        FakeButton.last = self

    def invoke(self):
        self.command()


class FakeRoot:
    """Stands in for tk.Tk. ``script`` runs once the window is up: it can fill the entries, press Enter, click Login, or
    press Escape. The entries and buttons the module creates are reachable as ``entries`` and ``buttons``."""

    instances = []

    def __init__(self, script=None):
        self.script = script
        self.destroyed = False
        self.protocol_handler = None
        self.bindings = {}
        self.title_text = None
        self.entries = {}
        self.buttons = {}
        self.idle_callbacks = []
        FakeRoot.instances.append(self)

    def title(self, text):
        self.title_text = text

    def bind(self, sequence, func):
        self.bindings[sequence] = func

    def protocol(self, name, func):
        self.protocol_handler = func

    def after_idle(self, func, *args):
        self.idle_callbacks.append((func, args))

    def mainloop(self):
        if self.script:
            self.script(self)

    def destroy(self):
        self.destroyed = True

    def columnconfigure(self, *a, **k):
        pass


def _run(script):
    """Run tk_mod.prompt_credentials with a fake Tk and the given script. Returns the presentation's result."""
    FakeRoot.instances.clear()

    def make_root(*a, **k):
        return FakeRoot(script)

    fake_tk = types.SimpleNamespace(W="w", E="e", END="end")
    fake_ttk = types.SimpleNamespace(Label=FakeWidget, Entry=FakeWidget, Button=FakeButton)
    with patch.object(tk_mod, "_tk_components", return_value=(make_root, FakeVar, (fake_tk, fake_ttk))):
        return tk_mod.prompt_credentials(title="zoid6 auth", header="v1", width=25)


class TestContract(unittest.TestCase):
    def test_the_signature_is_keyword_only_with_title_header_width(self):
        params = inspect.signature(tk_mod.prompt_credentials).parameters
        self.assertEqual(list(params)[:3], ["title", "header", "width"])
        for name in ("title", "header", "width"):
            self.assertEqual(params[name].kind, inspect.Parameter.KEYWORD_ONLY, name)

    def test_the_module_imports_without_a_display(self):
        self.assertTrue(hasattr(tk_mod, "prompt_credentials"))


class TestReturnsTheFields(unittest.TestCase):
    def test_enter_in_the_password_field_returns_both_values(self):
        def script(root):
            root.entries["moniker"].set("jam")
            root.entries["password"].set("secret")
            root.bindings["<Return>"](None)

        moniker, password = _run(script)
        self.assertEqual((moniker, password), ("jam", "secret"))

    def test_the_login_button_returns_both_values(self):
        # The button is created with command=submit, as a real ttk.Button is. The fake records the button, and the test
        # presses it through that command.
        def script(root):
            root.entries["moniker"].set("jam")
            root.entries["password"].set("secret")
            button = FakeButton.last
            button.invoke()

        self.assertEqual(_run(script), ("jam", "secret"))


class TestBlankFieldKeepsTheWindowOpen(unittest.TestCase):
    def test_a_blank_moniker_does_not_return(self):
        seen = []

        def script(root):
            root.entries["moniker"].set("")
            root.entries["password"].set("pw")
            root.bindings["<Return>"](None)
            seen.append(root.destroyed)
            root.entries["moniker"].set("jam")
            root.bindings["<Return>"](None)

        self.assertEqual(_run(script), ("jam", "pw"))
        self.assertEqual(seen, [False], "the window stayed open after a blank moniker")


class TestCancel(unittest.TestCase):
    def test_escape_raises_credentials_cancelled(self):
        def script(root):
            root.bindings["<Escape>"](None)

        with self.assertRaises(CredentialsCancelled):
            _run(script)

    def test_closing_the_window_raises_credentials_cancelled(self):
        def script(root):
            root.protocol_handler()

        with self.assertRaises(CredentialsCancelled):
            _run(script)


class TestWindowIsDestroyed(unittest.TestCase):
    def test_the_window_is_destroyed_on_return(self):
        def script(root):
            root.entries["moniker"].set("jam")
            root.entries["password"].set("pw")
            root.bindings["<Return>"](None)

        _run(script)
        self.assertTrue(FakeRoot.instances[-1].destroyed)

    def test_the_window_is_destroyed_on_cancel(self):
        def script(root):
            root.bindings["<Escape>"](None)

        with self.assertRaises(CredentialsCancelled):
            _run(script)
        self.assertTrue(FakeRoot.instances[-1].destroyed)


class TestNoPasswordCheck(unittest.TestCase):
    def test_the_presentation_does_not_check_the_password(self):
        source = inspect.getsource(tk_mod)
        self.assertNotIn("checkpassword", source)
        self.assertNotIn("_login", source)


if __name__ == "__main__":
    unittest.main()


class TestFocus(unittest.TestCase):
    """The moniker entry takes focus once the window is drawn, so the first key goes to it on every window manager."""

    def test_the_moniker_focus_is_deferred_until_the_window_is_idle(self):
        seen = {}

        def script(root):
            seen["idle"] = list(root.idle_callbacks)
            root.entries["moniker"].set("jam")
            root.entries["password"].set("pw")
            root.bindings["<Return>"](None)

        _run(script)
        self.assertEqual(len(seen["idle"]), 1, "exactly one focus call is queued before mainloop")
        func, _args = seen["idle"][0]
        self.assertEqual(getattr(func, "__name__", ""), "focus_set")


class TestFormContract(unittest.TestCase):
    """The Tk presentation declares form last (the contract's position) and reads its labels and password mask from it."""

    def test_the_signature_declares_form_after_the_original_keywords(self):
        names = list(inspect.signature(tk_mod.prompt_credentials).parameters)
        self.assertEqual(names[:3], ["title", "header", "width"])
        self.assertEqual(names[3], "form")

    def test_the_shipped_presentation_passes_the_contract(self):
        from bbsengine6 import module as be6
        self.assertIsNone(be6.check_signature(tk_mod.prompt_credentials, be6._stub_prompt_credentials,
                                              name="prompt_credentials"))

    def test_the_labels_come_from_the_form(self):
        from bbsengine6.form import Form, FormItemTextbox
        form = Form("zoid6 auth", [FormItemTextbox(label="Name:", name="moniker"),
                                   FormItemTextbox(label="Secret:", name="password", mask="")])
        labels = []

        def script(root):
            root.entries["moniker"].set("jam")
            root.entries["password"].set("pw")
            root.bindings["<Return>"](None)

        FakeRoot.instances.clear()
        fake_tk = types.SimpleNamespace(W="w", E="e", END="end")
        fake_ttk = types.SimpleNamespace(Label=FakeWidgetLabel(labels), Entry=FakeWidget, Button=FakeButton)

        def make_root(*a, **k):
            return FakeRoot(script)

        with patch.object(tk_mod, "_tk_components", return_value=(make_root, FakeVar, (fake_tk, fake_ttk))):
            tk_mod.prompt_credentials(title="zoid6 auth", header="v1", width=25, form=form)
        self.assertIn("Name:", labels)
        self.assertIn("Secret:", labels)


class FakeWidgetLabel:
    """A Label that records its text, so a test can see the labels the window was given."""

    def __init__(self, sink):
        self.sink = sink

    def __call__(self, root=None, text="", **kwargs):
        self.sink.append(text)
        return FakeWidget(root, **kwargs)


class TestPasswordMaskFromTheForm(unittest.TestCase):
    """The password entry's show option comes from the form's password textbox: "*" shows asterisks, "" shows nothing."""

    def _show_for(self, mask):
        from bbsengine6.form import Form, FormItemTextbox
        form = Form("zoid6 auth", [FormItemTextbox(label="moniker: ", name="moniker"),
                                   FormItemTextbox(label="password: ", name="password", mask=mask)])
        shows = []

        class Entry(FakeWidget):
            def __init__(self, root=None, **kwargs):
                super().__init__(root, **kwargs)
                shows.append(kwargs.get("show"))

        def script(root):
            root.entries["moniker"].set("jam")
            root.entries["password"].set("pw")
            root.bindings["<Return>"](None)

        FakeRoot.instances.clear()
        fake_tk = types.SimpleNamespace(W="w", E="e", END="end")
        fake_ttk = types.SimpleNamespace(Label=FakeWidget, Entry=Entry, Button=FakeButton)

        def make_root(*a, **k):
            return FakeRoot(script)

        with patch.object(tk_mod, "_tk_components", return_value=(make_root, FakeVar, (fake_tk, fake_ttk))):
            tk_mod.prompt_credentials(title="zoid6 auth", header="v1", width=25, form=form)
        return shows[1]

    def test_an_asterisk_mask_shows_asterisks(self):
        self.assertEqual(self._show_for("*"), "*")

    def test_an_empty_mask_shows_nothing(self):
        self.assertEqual(self._show_for(""), "")
