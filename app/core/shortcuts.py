"""Centralized keyboard shortcuts (Qt-free).

Every command has a stable action id (`widget.toggle`, `app.open_history`...). The ShortcutManager owns the key
combinations, validates them, detects conflicts, persists them in SQLite and dispatches commands to the handlers
registered by the interfaces. Components never hardcode key combinations: the Qt layer (`app.ui.shortcuts`) turns the
active bindings into QShortcuts and `app.system.global_hotkeys` registers the global ones with Windows.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.infrastructure.database import Database
from app.utils.formatters import now_iso

log = logging.getLogger("luut.shortcuts")

# ----------------------------------------------------------------------------- keys
MODIFIERS = ("Ctrl", "Alt", "Shift", "Win")
_MODIFIER_ALIASES = {"ctrl": "Ctrl", "control": "Ctrl", "ctl": "Ctrl", "alt": "Alt", "shift": "Shift",
                     "win": "Win", "meta": "Win", "super": "Win", "windows": "Win"}

LETTERS = tuple(chr(c) for c in range(ord("A"), ord("Z") + 1))
DIGITS = tuple(str(d) for d in range(10))
FUNCTION_KEYS = tuple(f"F{n}" for n in range(1, 25))
NAMED_KEYS = ("Enter", "Space", "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
              "Up", "Down", "Left", "Right", "Escape")
PUNCTUATION = (",", ".", "/", ";", "'", "[", "]", "\\", "-", "=", "`")
VALID_KEYS = frozenset(LETTERS + DIGITS + FUNCTION_KEYS + NAMED_KEYS + PUNCTUATION)
# Keys that can work alone (in a local context) without typing a character.
STANDALONE_KEYS = frozenset(FUNCTION_KEYS + ("Enter", "Space", "Backspace", "Delete", "Insert", "Home", "End",
                                             "PageUp", "PageDown", "Up", "Down", "Left", "Right"))
_KEY_ALIASES = {"return": "Enter", "del": "Delete", "esc": "Escape", "pgup": "PageUp", "pgdown": "PageDown",
                "pgdn": "PageDown", "ins": "Insert", "spacebar": "Space", "comma": ",", "period": ".",
                "minus": "-", "equal": "=", "slash": "/", "backslash": "\\", "semicolon": ";",
                "apostrophe": "'", "quote": "'", "grave": "`", "arrowup": "Up", "arrowdown": "Down",
                "arrowleft": "Left", "arrowright": "Right"}
_CANONICAL_KEYS = {k.lower(): k for k in VALID_KEYS}

# Combinations Windows (or the shell) keeps for itself, or that every program expects for text editing.
RESERVED_SYSTEM = frozenset({
    "Alt+F4", "Alt+Tab", "Alt+Shift+Tab", "Alt+Escape", "Alt+Space", "Ctrl+Escape", "Ctrl+Shift+Escape",
    "Ctrl+Alt+Delete", "Ctrl+Alt+Tab", "Ctrl+Win+D", "Ctrl+Win+Left", "Ctrl+Win+Right", "Ctrl+Win+F4",
    "Win+Shift+S", "Ctrl+Alt+Escape",
})
TEXT_EDITING = frozenset({"Ctrl+C", "Ctrl+X", "Ctrl+V", "Ctrl+Z", "Ctrl+Y", "Ctrl+A"})

DISABLED_TEXT = "Desativado"


class ShortcutError(ValueError):
    """A combination that cannot be used. `message` is friendly (pt-BR)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ShortcutConflict(ShortcutError):
    def __init__(self, combo: KeyCombo, other: ShortcutAction) -> None:
        super().__init__("conflict", f"{combo.display} já está sendo usado por:\n\n“{other.label}”")
        self.combo = combo
        self.other = other


@dataclass(frozen=True)
class KeyCombo:
    modifiers: frozenset[str]
    key: str

    @property
    def text(self) -> str:
        """Canonical form stored in the database, e.g. "Ctrl+Shift+L"."""
        return "+".join([m for m in MODIFIERS if m in self.modifiers] + [self.key])

    @property
    def display(self) -> str:
        return " + ".join([m for m in MODIFIERS if m in self.modifiers] + [self.key])

    @property
    def has_command_modifier(self) -> bool:
        return bool(self.modifiers & {"Ctrl", "Alt", "Win"})

    @property
    def is_function_key(self) -> bool:
        return self.key in FUNCTION_KEYS

    def __str__(self) -> str:
        return self.text


def _canonical_key(token: str) -> str | None:
    lowered = token.strip().lower()
    if not lowered:
        return None
    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]
    return _CANONICAL_KEYS.get(lowered)


def parse_combo(text: str) -> KeyCombo:
    """Parse "Ctrl + Shift + L" / "ctrl+shift+l". Raises ShortcutError with a friendly message."""
    raw = (text or "").strip()
    if not raw:
        raise ShortcutError("empty", "Pressione uma combinação de teclas.")
    parts = [p for p in re.split(r"\s*\+\s*", raw)]
    if raw.endswith("+"):
        raise ShortcutError("invalid_key", "A tecla “+” não pode ser usada. Escolha outra tecla principal.")
    modifiers: set[str] = set()
    key: str | None = None
    for part in parts:
        if not part:
            raise ShortcutError("invalid_key", "Combinação inválida.")
        modifier = _MODIFIER_ALIASES.get(part.lower())
        if modifier:
            modifiers.add(modifier)
            continue
        if key is not None:
            raise ShortcutError("invalid_key", "Use apenas uma tecla principal, junto com Ctrl, Shift, Alt ou Win.")
        key = _canonical_key(part)
        if key is None:
            raise ShortcutError("invalid_key", f"A tecla “{part}” não é suportada. Escolha outra tecla principal.")
    if key is None:
        raise ShortcutError("modifier_only", "Não foi possível usar esse atalho.\n\nEscolha uma combinação contendo "
                                             "pelo menos uma tecla principal.")
    return KeyCombo(frozenset(modifiers), key)


# --------------------------------------------------------------------------- actions
GROUP_APP = "app"
GROUP_DOWNLOADS = "downloads"
GROUP_WIDGET = "widget"
GROUP_SYSTEM = "system"
GROUPS = {GROUP_APP: "Aplicação desktop", GROUP_DOWNLOADS: "Downloads", GROUP_WIDGET: "Widget",
          GROUP_SYSTEM: "Sistema"}
GROUP_HINTS = {
    GROUP_APP: "Funcionam com a janela principal em foco.",
    GROUP_DOWNLOADS: "Funcionam na janela principal e agem sobre o download selecionado na página Downloads.",
    GROUP_WIDGET: "Funcionam com o widget flutuante em foco. Atalhos GLOBAIS funcionam mesmo com o Luut em segundo "
                  "plano.",
    GROUP_SYSTEM: "Comandos gerais do aplicativo.",
}
WINDOW_APP = "app"
WINDOW_WIDGET = "widget"


@dataclass(frozen=True)
class ShortcutAction:
    id: str
    label: str
    group: str
    default: str = ""
    is_global: bool = False
    window: str = WINDOW_APP           # where a local shortcut works
    allow_single_key: bool = False     # e.g. Enter/Space/Delete, local only
    shares: str | None = None          # actions with the same tag may share a combination (context decides)
    allowed_reserved: frozenset[str] = field(default_factory=frozenset)

    @property
    def scope_label(self) -> str:
        return "GLOBAL" if self.is_global else "LOCAL"


def _a(action_id: str, label: str, group: str, default: str = "", **kwargs: object) -> ShortcutAction:
    window = WINDOW_WIDGET if group == GROUP_WIDGET else WINDOW_APP
    return ShortcutAction(action_id, label, group, default, window=kwargs.pop("window", window), **kwargs)  # type: ignore[arg-type]


DEFAULT_ACTIONS: tuple[ShortcutAction, ...] = (
    # Aplicação desktop
    _a("app.toggle", "Mostrar/Ocultar janela principal", GROUP_APP, "Ctrl+Alt+L", is_global=True),
    _a("app.show", "Mostrar aplicação", GROUP_APP, is_global=True),
    _a("app.focus", "Focar aplicação", GROUP_APP, is_global=True),
    _a("app.hide", "Ocultar aplicação (bandeja)", GROUP_APP),
    _a("app.minimize", "Minimizar aplicação", GROUP_APP),
    _a("app.maximize", "Maximizar/restaurar aplicação", GROUP_APP),
    _a("app.close", "Fechar aplicação", GROUP_APP, "Ctrl+Q"),
    _a("app.open_home", "Abrir Início", GROUP_APP, "Ctrl+1"),
    _a("app.open_downloads", "Abrir Downloads (fila)", GROUP_APP, "Ctrl+D"),
    _a("app.open_history", "Abrir Histórico", GROUP_APP, "Ctrl+H"),
    _a("app.open_settings", "Abrir Configurações", GROUP_APP, "Ctrl+,"),
    _a("app.open_about", "Abrir Sobre", GROUP_APP),
    _a("app.new_url", "Nova URL", GROUP_APP, "Ctrl+N"),
    _a("app.focus_url", "Focar campo URL", GROUP_APP, "Ctrl+L"),
    _a("app.paste_url", "Colar URL", GROUP_APP, "Ctrl+V", allowed_reserved=frozenset({"Ctrl+V"})),
    _a("app.clear_url", "Limpar URL", GROUP_APP),
    _a("app.analyze", "Analisar URL (no campo URL)", GROUP_APP, "Enter", allow_single_key=True),
    _a("app.start_download", "Iniciar download (adicionar à fila)", GROUP_APP, "Ctrl+Enter"),
    # Downloads
    _a("downloads.pause_selected", "Pausar download selecionado", GROUP_DOWNLOADS, "Space", allow_single_key=True,
       shares="downloads.toggle_selected"),
    _a("downloads.resume_selected", "Retomar download selecionado", GROUP_DOWNLOADS, "Space", allow_single_key=True,
       shares="downloads.toggle_selected"),
    _a("downloads.cancel_selected", "Cancelar/remover download selecionado", GROUP_DOWNLOADS, "Delete",
       allow_single_key=True),
    _a("downloads.retry_selected", "Tentar novamente / reiniciar selecionado", GROUP_DOWNLOADS, "Ctrl+R"),
    _a("downloads.open_file", "Abrir arquivo do selecionado", GROUP_DOWNLOADS, "Ctrl+O"),
    _a("downloads.open_folder", "Abrir pasta do selecionado", GROUP_DOWNLOADS, "Ctrl+E"),
    _a("downloads.copy_path", "Copiar caminho do selecionado", GROUP_DOWNLOADS),
    _a("downloads.pause_all", "Pausar todos", GROUP_DOWNLOADS, "Ctrl+Shift+P"),
    _a("downloads.resume_all", "Retomar todos", GROUP_DOWNLOADS, "Ctrl+Shift+R"),
    _a("downloads.cancel_all", "Cancelar todos", GROUP_DOWNLOADS, "Ctrl+Shift+C"),
    _a("downloads.clear_finished", "Limpar concluídos", GROUP_DOWNLOADS),
    # Widget
    _a("widget.toggle", "Mostrar/Ocultar Widget", GROUP_WIDGET, "Ctrl+Shift+L", is_global=True),
    _a("widget.focus", "Focar Widget", GROUP_WIDGET, is_global=True),
    _a("widget.minimize", "Minimizar Widget (botão compacto)", GROUP_WIDGET, "Ctrl+Shift+M"),
    _a("widget.expand", "Expandir Widget", GROUP_WIDGET, "Ctrl+Shift+E"),
    _a("widget.focus_url", "Focar campo URL", GROUP_WIDGET, "Ctrl+Shift+U"),
    _a("widget.paste_url", "Colar URL", GROUP_WIDGET, "Ctrl+Shift+V"),
    _a("widget.clear_url", "Limpar URL", GROUP_WIDGET),
    _a("widget.analyze", "Analisar URL", GROUP_WIDGET, "Ctrl+Shift+Enter", shares="widget.primary"),
    _a("widget.start_download", "Iniciar download", GROUP_WIDGET, "Ctrl+Shift+Enter", shares="widget.primary"),
    _a("widget.pause_download", "Pausar download", GROUP_WIDGET, "Ctrl+Shift+P"),
    _a("widget.resume_download", "Retomar download", GROUP_WIDGET, "Ctrl+Shift+R"),
    _a("widget.cancel_download", "Cancelar download", GROUP_WIDGET, "Ctrl+Shift+X"),
    _a("widget.open_queue", "Abrir fila", GROUP_WIDGET, "Ctrl+Shift+Q"),
    _a("widget.next_item", "Próximo item da fila", GROUP_WIDGET, "Alt+Down"),
    _a("widget.prev_item", "Item anterior da fila", GROUP_WIDGET, "Alt+Up"),
    _a("widget.pause_all", "Pausar todos", GROUP_WIDGET),
    _a("widget.resume_all", "Retomar todos", GROUP_WIDGET),
    _a("widget.open_app", "Abrir aplicativo principal", GROUP_WIDGET, "Ctrl+Shift+O"),
    _a("widget.open_history", "Abrir histórico", GROUP_WIDGET, "Ctrl+Shift+H"),
    _a("widget.open_folder", "Abrir pasta de downloads", GROUP_WIDGET, "Ctrl+Shift+D"),
    # Sistema
    _a("system.open_download_folder", "Abrir pasta de downloads", GROUP_SYSTEM, "Ctrl+Shift+D"),
    _a("system.open_logs", "Abrir logs", GROUP_SYSTEM, "Ctrl+Shift+G"),
    _a("system.open_tutorial", "Abrir tutorial", GROUP_SYSTEM, "F1"),
    _a("system.show_tray_menu", "Mostrar menu da bandeja", GROUP_SYSTEM, is_global=True),
)


def validate_for(action: ShortcutAction, combo: KeyCombo) -> None:
    """Rules that depend only on the action (conflicts are checked by the manager)."""
    text = combo.text
    if combo.key == "Escape" and not combo.modifiers:
        raise ShortcutError("reserved", "Esc é usado para cancelar e fechar janelas. Escolha outra combinação.")
    if text in RESERVED_SYSTEM or (combo.modifiers == {"Win"}):
        raise ShortcutError("reserved", f"{combo.display} é reservado pelo Windows. Escolha outra combinação.")
    if text in TEXT_EDITING and text not in action.allowed_reserved:
        raise ShortcutError("reserved", f"{combo.display} é usado para editar texto em todos os programas. "
                                        "Escolha outra combinação.")
    if action.is_global:
        generic = not combo.has_command_modifier or (len(combo.modifiers) < 2 and not combo.is_function_key)
        if generic:
            raise ShortcutError("global_too_generic", "Atalhos globais funcionam em todo o Windows, então precisam de "
                                "pelo menos duas teclas modificadoras (por exemplo Ctrl + Shift + L).")
        return
    if combo.has_command_modifier or combo.is_function_key:
        return
    if action.allow_single_key and combo.key in STANDALONE_KEYS and not combo.modifiers - {"Shift"}:
        return
    raise ShortcutError("needs_modifier", "Esse atalho digitaria texto. Use Ctrl, Alt ou Win junto com a tecla.")


def contexts_overlap(a: ShortcutAction, b: ShortcutAction) -> bool:
    """Two bindings with the same combination collide when they can fire in the same place."""
    if a.is_global or b.is_global:
        return True
    return a.window == b.window


# ------------------------------------------------------------------------ persistence
@dataclass(frozen=True)
class Binding:
    combo: KeyCombo | None  # None = no combination
    enabled: bool = True

    @property
    def active(self) -> bool:
        return self.enabled and self.combo is not None


class ShortcutRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def load(self) -> dict[str, tuple[str, bool]]:
        rows = self._db.query("SELECT action_id, key_combination, enabled FROM shortcuts")
        return {r["action_id"]: (r["key_combination"] or "", bool(r["enabled"])) for r in rows}

    def save(self, action: ShortcutAction, combo_text: str, enabled: bool) -> None:
        self._db.execute(
            "INSERT INTO shortcuts (action_id, scope, key_combination, enabled, is_global, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(action_id) DO UPDATE SET scope = excluded.scope, "
            "key_combination = excluded.key_combination, enabled = excluded.enabled, "
            "is_global = excluded.is_global, updated_at = excluded.updated_at",
            (action.id, action.group, combo_text, int(enabled), int(action.is_global), now_iso()))

    def clear(self) -> None:
        self._db.execute("DELETE FROM shortcuts")


# ---------------------------------------------------------------------------- manager
Handler = Callable[[], object]  # returning False means "not applicable now" (shared combinations)
ChangeListener = Callable[[set[str]], None]


class ShortcutManager:
    def __init__(self, repository: ShortcutRepository, actions: Iterable[ShortcutAction] = DEFAULT_ACTIONS) -> None:
        self._repo = repository
        self._actions = {a.id: a for a in actions}
        self._lock = threading.RLock()
        self._bindings: dict[str, Binding] = {}
        self._handlers: dict[str, list[Handler]] = {}
        self._listeners: list[ChangeListener] = []
        self._global_status: dict[str, bool | None] = {}
        self._capturing = False
        self._load()

    # -------------------------------------------------------------- loading
    def _default_binding(self, action: ShortcutAction) -> Binding:
        return Binding(parse_combo(action.default) if action.default else None, True)

    def _load(self) -> None:
        stored = self._repo.load()
        for action in self._actions.values():
            binding = self._default_binding(action)
            if action.id in stored:
                text, enabled = stored[action.id]
                try:
                    combo = parse_combo(text) if text else None
                    if combo is not None:
                        validate_for(action, combo)
                    binding = Binding(combo, enabled and combo is not None)
                except ShortcutError:
                    log.warning("Ignoring invalid stored shortcut for %s", action.id)
            self._bindings[action.id] = binding

    # -------------------------------------------------------------- queries
    def actions(self, group: str | None = None) -> list[ShortcutAction]:
        return [a for a in self._actions.values() if group is None or a.group == group]

    def action(self, action_id: str) -> ShortcutAction:
        return self._actions[action_id]

    def binding(self, action_id: str) -> Binding:
        with self._lock:
            return self._bindings[action_id]

    def is_active(self, action_id: str) -> bool:
        return self.binding(action_id).active

    def combo(self, action_id: str) -> KeyCombo | None:
        binding = self.binding(action_id)
        return binding.combo if binding.active else None

    def display(self, action_id: str) -> str:
        combo = self.combo(action_id)
        return combo.display if combo else DISABLED_TEXT

    def hint(self, action_id: str, label: str | None = None) -> str:
        """Tooltip text such as "Analisar URL\nAtalho: Ctrl + Shift + Enter"."""
        name = label or self._actions[action_id].label
        combo = self.combo(action_id)
        return f"{name}\nAtalho: {combo.display}" if combo else name

    def is_default(self, action_id: str) -> bool:
        return self.binding(action_id) == self._default_binding(self._actions[action_id])

    def global_actions(self) -> list[ShortcutAction]:
        return [a for a in self._actions.values() if a.is_global]

    def bindings_for_window(self, window: str) -> dict[str, list[str]]:
        """Active local combinations of a window: canonical text -> action ids (shared combos have several)."""
        result: dict[str, list[str]] = {}
        with self._lock:
            for action in self._actions.values():
                combo = self._bindings[action.id].combo
                if action.is_global or action.window != window or not self._bindings[action.id].active or not combo:
                    continue
                result.setdefault(combo.text, []).append(action.id)
        return result

    def conflict(self, action_id: str, combo: KeyCombo) -> ShortcutAction | None:
        action = self._actions[action_id]
        with self._lock:
            for other in self._actions.values():
                if other.id == action_id:
                    continue
                binding = self._bindings[other.id]
                if not binding.active or binding.combo != combo:
                    continue
                if action.shares and action.shares == other.shares:
                    continue
                if contexts_overlap(action, other):
                    return other
        return None

    # ------------------------------------------------------------- changes
    def validate(self, action_id: str, text: str) -> KeyCombo:
        combo = parse_combo(text)
        validate_for(self._actions[action_id], combo)
        return combo

    def assign(self, action_id: str, text: str, replace: bool = False) -> KeyCombo:
        """Set a new combination. Raises ShortcutConflict (unless `replace`, which disables the other command)."""
        combo = self.validate(action_id, text)
        changed = {action_id}
        with self._lock:
            other = self.conflict(action_id, combo)
            if other is not None:
                if not replace:
                    raise ShortcutConflict(combo, other)
                self._store(other.id, Binding(None, False))
                changed.add(other.id)
                log.info("Shortcut %s removed from %s (replaced)", combo.text, other.id)
            self._store(action_id, Binding(combo, True))
        log.info("Shortcut changed: %s = %s", action_id, combo.text)
        self._notify(changed)
        return combo

    def disable(self, action_id: str) -> None:
        with self._lock:
            self._store(action_id, Binding(None, False))
        log.info("Shortcut disabled: %s", action_id)
        self._notify({action_id})

    def reset(self, action_id: str, replace: bool = False) -> None:
        """Back to this command's default (a conflict with a customized command raises ShortcutConflict)."""
        action = self._actions[action_id]
        if not action.default:
            self.disable(action_id)
            return
        self.assign(action_id, action.default, replace=replace)

    def restore_defaults(self) -> None:
        with self._lock:
            self._repo.clear()
            for action in self._actions.values():
                self._bindings[action.id] = self._default_binding(action)
        log.info("Shortcuts restored to defaults")
        self._notify(set(self._actions))

    def _store(self, action_id: str, binding: Binding) -> None:
        self._bindings[action_id] = binding
        self._repo.save(self._actions[action_id], binding.combo.text if binding.combo else "", binding.enabled)

    # ------------------------------------------------------------ listeners
    def add_listener(self, listener: ChangeListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: ChangeListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self, changed: set[str]) -> None:
        for listener in list(self._listeners):
            try:
                listener(set(changed))
            except Exception:
                log.exception("Shortcut listener failed")

    # ------------------------------------------------------------ dispatch
    def register_handler(self, action_id: str, handler: Handler) -> Callable[[], None]:
        if action_id not in self._actions:
            raise KeyError(action_id)
        self._handlers.setdefault(action_id, []).append(handler)

        def unregister() -> None:
            handlers = self._handlers.get(action_id, [])
            if handler in handlers:
                handlers.remove(handler)

        return unregister

    def has_handler(self, action_id: str) -> bool:
        return bool(self._handlers.get(action_id))

    @property
    def capturing(self) -> bool:
        return self._capturing

    def set_capturing(self, capturing: bool) -> None:
        """While the user records a new combination nothing fires and global hotkeys are released."""
        if capturing != self._capturing:
            self._capturing = capturing
            self._notify(set())

    def trigger(self, action_id: str) -> bool:
        """Run the command if its shortcut is enabled. Returns False when disabled or not applicable."""
        if self._capturing or not self.is_active(action_id):
            return False
        for handler in list(self._handlers.get(action_id, ())):
            if handler() is not False:
                return True
        return False

    def trigger_first(self, action_ids: Iterable[str]) -> str | None:
        """Shared combination: run the first command that applies in the current context."""
        for action_id in action_ids:
            if self.trigger(action_id):
                return action_id
        return None

    # ---------------------------------------------------- global registration
    def set_global_status(self, action_id: str, registered: bool | None) -> None:
        self._global_status[action_id] = registered

    def global_status(self, action_id: str) -> bool | None:
        """True = registered with Windows, False = refused (used by another program), None = not attempted."""
        return self._global_status.get(action_id)
