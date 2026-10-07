#!/usr/bin/env python3
"""Painel rápido da mesa T1161 no GNOME/Wayland."""

from __future__ import annotations

import sys
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from t1161_profiles import get_active_profile, load_profiles, set_active_profile
from t1161_input_profiles import (
    activate_input_profile,
    get_active_input_profile,
    load_input_profiles,
)


APP_ID = "io.github.t1161.Actions"
DISPLAY_BUS = "org.gnome.Mutter.DisplayConfig"
DISPLAY_PATH = "/org/gnome/Mutter/DisplayConfig"
TABLET_SCHEMA = "org.gnome.desktop.peripherals.tablet"
TABLET_PATH = "/org/gnome/desktop/peripherals/tablets/08f2:6811/"
TELEMETRY_PATH = Path("/run/t1161-control/state")
BUTTON_MAP_PATH = Path.home() / ".config" / "t1161-control" / "buttons.json"
CONTROL_ACTIONS_PATH = Path.home() / ".config" / "t1161-control" / "control-actions.json"
MENU_STATE_PATH = Path("/dev/shm/t1161-menu-visible")
RESET_HELPER_PATH = Path("/usr/libexec/t1161-reset-driver")
PKEXEC_PATH = "/usr/bin/pkexec"
SYSTEMCTL_PATH = "/usr/bin/systemctl"
DEFAULT_BUTTON_MAP = (8, 10, 12, 11, 9, 7, 5, 3, 4, 1, 2, 6)
RAW_BUTTON_BITS = tuple(range(10)) + (12, 13)
BACKGROUND_MODE = "--background" in sys.argv

PANEL_WIDTH = 392
PANEL_HEIGHT = 350
SCREEN_PANEL_WIDTH = 440
SCREEN_PANEL_HEIGHT = 350

PANEL_CSS = """
window.projection-popup {
    background: rgba(25, 25, 28, 0.90);
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 12px;
    box-shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
}

.projection-content {
    padding: 24px;
}

.projection-grid {
    min-height: 0;
}

.profile-button {
    min-width: 148px;
    min-height: 118px;
    font-size: 15px;
    font-weight: 600;
    border-radius: 10px;
}

window.screen-profile-popup .projection-grid {
    min-height: 0;
}

window.screen-profile-popup .profile-button {
    min-height: 118px;
}

.profile-button:checked {
    background: rgba(53, 132, 228, 0.92);
}

.profile-button.keyboard-selected {
    outline: 3px solid rgba(255, 255, 255, 0.92);
    outline-offset: -4px;
}
"""


def rounded_rectangle(ctx, x: float, y: float, width: float, height: float, radius: float) -> None:
    """Adiciona um retângulo arredondado ao caminho Cairo atual."""
    radius = min(radius, width / 2, height / 2)
    ctx.new_sub_path()
    ctx.arc(x + width - radius, y + radius, radius, -1.5708, 0)
    ctx.arc(x + width - radius, y + height - radius, radius, 0, 1.5708)
    ctx.arc(x + radius, y + height - radius, radius, 1.5708, 3.1416)
    ctx.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
    ctx.close_path()


class ProfileIcon(Gtk.DrawingArea):
    """Miniatura paisagem do arranjo salvo no perfil."""

    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.set_content_width(142)
        self.set_content_height(82)
        self.set_draw_func(self._draw)

    def _draw(self, _area, ctx, width, height):
        margin = 5
        tablet_width = width - margin * 2
        tablet_height = min(height - margin * 2, tablet_width * 188 / 300)
        x0, y0 = margin, (height - tablet_height) / 2
        rounded_rectangle(ctx, x0, y0, tablet_width, tablet_height, 7)
        ctx.set_source_rgba(1, 1, 1, 0.08)
        ctx.fill_preserve()
        ctx.set_source_rgba(1, 1, 1, 0.28)
        ctx.set_line_width(1)
        ctx.stroke()
        for zone in self.profile.zones:
            x = x0 + zone.x * tablet_width
            y = y0 + zone.y * tablet_height
            w = zone.width * tablet_width
            h = zone.height * tablet_height
            rounded_rectangle(ctx, x, y, w, h, 5)
            ctx.set_source_rgba(0.30, 0.70, 1.0, 0.42)
            ctx.fill_preserve()
            ctx.set_source_rgba(0.65, 0.86, 1.0, 0.95)
            ctx.set_line_width(1.5)
            ctx.stroke()
            ctx.save()
            ctx.translate(x + w / 2, y + h / 2)
            ctx.rotate(math.radians(zone.movement_rotation))
            ctx.set_source_rgba(1, 1, 1, 0.92)
            ctx.select_font_face("Sans", 0, 1)
            ctx.set_font_size(max(12, min(25, w * 0.34, h * 0.52)))
            extents = ctx.text_extents("A")
            ctx.move_to(-(extents.width / 2 + extents.x_bearing), -(extents.height / 2 + extents.y_bearing))
            ctx.show_text("A")
            ctx.restore()


class InputProfileIcon(Gtk.DrawingArea):
    """Resumo visual das ações de um perfil."""

    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.set_content_width(142)
        self.set_content_height(82)
        self.set_draw_func(self._draw)

    def _draw(self, _area, ctx, width, height):
        rounded_rectangle(ctx, 5, 5, width - 10, height - 10, 8)
        ctx.set_source_rgba(1, 1, 1, 0.08)
        ctx.fill_preserve()
        ctx.set_source_rgba(1, 1, 1, 0.28)
        ctx.stroke()
        labels = {
            "none": "—", "left": "E", "right": "D", "middle": "M",
            "scroll-vertical": "↕", "scroll-horizontal": "↔",
        }
        values = [self.profile.pressure, self.profile.pen_04,
                  self.profile.pen_06, self.profile.pen_04_pressure_move]
        ctx.set_source_rgba(1, 1, 1, 0.92)
        ctx.select_font_face("Sans", 0, 1)
        ctx.set_font_size(20)
        for index, value in enumerate(values):
            ctx.move_to(18 + index * 31, height / 2 + 7)
            ctx.show_text(labels.get(value, "—"))


class DriverResetButton(Gtk.Button):
    """Reinicia o hardware USB e o serviço com autorização administrativa."""

    def __init__(self):
        super().__init__(label="Reiniciar driver")
        self.set_tooltip_text("Reinicia a conexão USB e o driver da mesa T1161")
        self.connect("clicked", self._clicked)
        self._process = None

    def _clicked(self, _button) -> None:
        if self._process is not None:
            return
        self.set_sensitive(False)
        self.set_label("Reiniciando…")
        command = ([PKEXEC_PATH, str(RESET_HELPER_PATH)]
                   if RESET_HELPER_PATH.is_file()
                   else [PKEXEC_PATH, SYSTEMCTL_PATH, "restart", "t1161-driver.service"])
        try:
            self._process = Gio.Subprocess.new(
                command,
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._process.wait_check_async(None, self._finished)
        except GLib.Error:
            self._show_result(False)

    def _finished(self, process, result) -> None:
        try:
            successful = process.wait_check_finish(result)
        except GLib.Error:
            successful = False
        self._process = None
        self._show_result(successful)

    def _show_result(self, successful: bool) -> None:
        self.set_label("Driver reiniciado" if successful else "Falha ao reiniciar")
        GLib.timeout_add_seconds(3, self._restore_label)

    def _restore_label(self) -> bool:
        self.set_label("Reiniciar driver")
        self.set_sensitive(True)
        return False


class TelemetryLogButton(Gtk.Button):
    """Grava durante dez segundos o estado publicado pelo driver."""

    DURATION_SECONDS = 10
    SAMPLE_INTERVAL_MS = 10

    def __init__(self):
        super().__init__(label="Log de 10 segundos")
        self.set_tooltip_text("Registra posições, pressão, botões e bytes com marca de tempo")
        self.connect("clicked", self._clicked)
        self._source = 0
        self._started_ns = 0
        self._deadline_ns = 0
        self._file = None
        self._path: Path | None = None

    def _clicked(self, _button) -> None:
        if self._source:
            return
        log_dir = Path.home() / ".local" / "state" / "t1161-control" / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            self._path = log_dir / f"telemetry-{stamp}.tsv"
            self._file = self._path.open("w", encoding="utf-8")
            self._file.write("elapsed_ms\twall_time\tstate\n")
        except OSError as error:
            self._finish(f"Falha: {error}")
            return
        self._started_ns = time.monotonic_ns()
        self._deadline_ns = self._started_ns + self.DURATION_SECONDS * 1_000_000_000
        self.set_sensitive(False)
        self.set_label("Gravando… 10,0 s")
        self._source = GLib.timeout_add(self.SAMPLE_INTERVAL_MS, self._sample)

    def _sample(self) -> bool:
        now_ns = time.monotonic_ns()
        elapsed_ms = (now_ns - self._started_ns) / 1_000_000
        try:
            state = " ".join(TELEMETRY_PATH.read_text().split())
        except OSError as error:
            state = f"ERRO: {error}"
        wall_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
        try:
            self._file.write(f"{elapsed_ms:.3f}\t{wall_time}\t{state}\n")
        except OSError as error:
            self._finish(f"Falha: {error}")
            return False
        remaining = max(0.0, (self._deadline_ns - now_ns) / 1_000_000_000)
        self.set_label(f"Gravando… {remaining:.1f} s")
        if now_ns < self._deadline_ns:
            return True
        self._finish("Log salvo")
        return False

    def _finish(self, label: str) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        self._source = 0
        self.set_label(label)
        if self._path is not None:
            self.set_tooltip_text(str(self._path))
        GLib.timeout_add_seconds(4, self._restore_label)

    def _restore_label(self) -> bool:
        self.set_label("Log de 10 segundos")
        self.set_sensitive(True)
        return False


def load_button_map(path: Path = BUTTON_MAP_PATH) -> tuple[int, ...]:
    """Carrega a ordem física confirmada pelo configurador da T1161."""
    try:
        values = tuple(int(value) for value in json.loads(path.read_text()))
        if (
            len(values) == 12
            and len(set(values)) == 12
            and all(1 <= value <= 12 for value in values)
        ):
            return values
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return DEFAULT_BUTTON_MAP


def load_control_actions() -> dict[str, str]:
    actions = {f"tablet-{index}": {"normal": "none", "menu": "none"} for index in range(1, 13)}
    actions.update({
        "tablet-1": {"normal": "open-screen-profiles", "menu": "none"},
        "tablet-2": {"normal": "open-input-profiles", "menu": "none"},
        "tablet-3": {"normal": "none", "menu": "previous"},
        "tablet-4": {"normal": "none", "menu": "next"},
        "tablet-5": {"normal": "none", "menu": "confirm"},
    })
    # Perfis sao a fonte unica das acoes. O arquivo control-actions.json ainda
    # e publicado para compatibilidade com versoes antigas, mas nao deve
    # decidir o comportamento do popup independentemente do perfil ativo.
    active_id = get_active_input_profile()
    active_profile = next(
        (profile for profile in load_input_profiles() if profile.profile_id == active_id),
        None,
    )
    if active_profile is not None:
        actions.update(active_profile.control_actions)
    return actions


def physical_button_pressed(
    buttons: int,
    baseline: int,
    physical_button: int,
    button_map: tuple[int, ...],
) -> bool:
    """Informa se um botão físico está diferente do estado de repouso."""
    signal = button_map[physical_button - 1]
    bit = RAW_BUTTON_BITS[signal - 1]
    return bool((buttons ^ baseline) & (1 << bit))


@dataclass(frozen=True)
class Monitor:
    connector: str
    vendor: str
    product: str
    serial: str
    width: int
    height: int
    scale: float
    x: int
    y: int
    primary: bool
    logical_width: int
    logical_height: int

    @property
    def output(self) -> list[str]:
        return [self.vendor, self.product, self.serial]

    @property
    def label(self) -> str:
        principal = " • principal" if self.primary else ""
        return (
            f"{self.product or self.connector} — {self.width}×{self.height} "
            f"({self.scale:g}×){principal}"
        )


def parse_monitors(state: tuple) -> list[Monitor]:
    """Converte GetCurrentState do Mutter numa lista simples e estável."""
    physical = {}
    for spec, modes, _properties in state[1]:
        connector, vendor, product, serial = spec
        current = next(
            (mode for mode in modes if mode[6].get("is-current", False)),
            modes[0] if modes else None,
        )
        if current:
            physical[connector] = (vendor, product, serial, current[1], current[2])

    result = []
    for x, y, scale, transform, primary, monitor_specs, _properties in state[2]:
        for spec in monitor_specs:
            connector = spec[0]
            vendor, product, serial, width, height = physical.get(
                connector, (spec[1], spec[2], spec[3], 0, 0)
            )
            logical_width = round(width / float(scale))
            logical_height = round(height / float(scale))
            if int(transform) in (1, 3, 5, 7):
                logical_width, logical_height = logical_height, logical_width
            result.append(
                Monitor(
                    connector, vendor, product, serial, width, height,
                    float(scale), int(x), int(y), bool(primary),
                    logical_width, logical_height,
                )
            )
    return sorted(result, key=lambda item: (item.x, item.y, item.connector))


def read_monitors() -> list[Monitor]:
    proxy = Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SESSION,
        Gio.DBusProxyFlags.NONE,
        None,
        DISPLAY_BUS,
        DISPLAY_PATH,
        DISPLAY_BUS,
        None,
    )
    reply = proxy.call_sync(
        "GetCurrentState", None, Gio.DBusCallFlags.NONE, 3000, None
    )
    return parse_monitors(reply.unpack())


def monitor_for_target(target, monitors: list[Monitor]) -> Monitor | None:
    """Resolve uma tela usando identidade estável e o conector como desempate."""
    identity = tuple(target.output)
    candidates = [monitor for monitor in monitors if tuple(monitor.output) == identity]
    if target.connector:
        exact = next(
            (monitor for monitor in candidates if monitor.connector == target.connector),
            None,
        )
        if exact is not None:
            return exact
        # O conector também permite reencontrar uma tela cuja identificação EDID
        # esteja incompleta, mas nunca substitui uma identidade completa divergente.
        if not any(identity):
            exact = next(
                (monitor for monitor in monitors if monitor.connector == target.connector),
                None,
            )
            if exact is not None:
                return exact
    if len(candidates) == 1:
        return candidates[0]
    if not any(identity):
        return next((monitor for monitor in monitors if monitor.primary), monitors[0] if monitors else None)
    return None


def zone_runtime_issue(zone, monitor: Monitor | None) -> str:
    """Explica por que uma correspondência não pode ser aplicada sem distorção."""
    if monitor is None:
        return "monitor ausente; esta região será ignorada"
    source_x = zone.source_x
    source_y = zone.source_y
    source_width = zone.source_width or monitor.logical_width
    source_height = zone.source_height or monitor.logical_height
    if source_x < 0 or source_y < 0 or source_width <= 0 or source_height <= 0:
        return "recorte da tela inválido; esta região será ignorada"
    if source_x + source_width > monitor.logical_width or source_y + source_height > monitor.logical_height:
        return (
            f"a resolução mudou para {monitor.logical_width}×{monitor.logical_height} e "
            "o recorte salvo não cabe; esta região será ignorada"
        )
    saved = (zone.target.logical_width, zone.target.logical_height)
    current = (monitor.logical_width, monitor.logical_height)
    if all(saved) and saved != current and not zone.allow_distortion:
        return (
            f"a resolução mudou de {saved[0]}×{saved[1]} para {current[0]}×{current[1]}; "
            "revise o recorte antes de aplicar"
        )
    return ""


def write_driver_profile(profile, monitors: list[Monitor]) -> int:
    """Publica um perfil validado no canal de execução do driver."""
    if not monitors:
        return 0
    left = min(monitor.x for monitor in monitors)
    top = min(monitor.y for monitor in monitors)
    right = max(monitor.x + monitor.logical_width for monitor in monitors)
    bottom = max(monitor.y + monitor.logical_height for monitor in monitors)
    lines = [f"desktop {left} {top} {right - left} {bottom - top}"]
    for zone in profile.zones:
        target = monitor_for_target(zone.target, monitors)
        if zone_runtime_issue(zone, target):
            continue
        source_x = zone.source_x
        source_y = zone.source_y
        source_width = zone.source_width or target.logical_width
        source_height = zone.source_height or target.logical_height
        lines.append(
            "zone "
            f"{zone.x:.9f} {zone.y:.9f} {zone.width:.9f} {zone.height:.9f} "
            f"{target.x + source_x} {target.y + source_y} {source_width} {source_height} "
            f"{zone.movement_rotation}"
        )
    path = Path("/dev/shm/t1161-active-profile")
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n")
    temporary.replace(path)
    return len(lines) - 1


class ControlWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, title, profile_loader,
                 active_loader, activate_profile, icon_type,
                 compact_landscape=False):
        super().__init__(application=application, title=title)
        self.compact_landscape = compact_landscape
        if compact_landscape:
            self.set_default_size(SCREEN_PANEL_WIDTH, SCREEN_PANEL_HEIGHT)
            self.add_css_class("screen-profile-popup")
        else:
            self.set_default_size(PANEL_WIDTH, PANEL_HEIGHT)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_hide_on_close(True)
        self.add_css_class("projection-popup")

        # Contêiner propositalmente vazio. Os botões grandes entram neste grid.
        self.options_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        self.options_grid.set_hexpand(True)
        self.options_grid.set_vexpand(False)
        self.options_grid.set_valign(Gtk.Align.START)
        self.options_grid.add_css_class("projection-grid")
        self.profile_buttons: list[Gtk.ToggleButton] = []
        self.profile_ids: list[str] = []
        self.selected_index = 0
        self.profile_loader = profile_loader
        self.active_loader = active_loader
        self.activate_profile = activate_profile
        self.icon_type = icon_type

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.add_css_class("projection-content")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.options_grid)
        content.append(scrolled)
        self.set_child(content)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.reload_profiles()

    def reload_profiles(self) -> None:
        child = self.options_grid.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.options_grid.remove(child)
            child = following
        self.profile_buttons = []
        self.profile_ids = []
        active = self.active_loader()
        visible_profiles = [item for item in self.profile_loader() if item.pinned]
        for index, profile in enumerate(visible_profiles):
            button = Gtk.ToggleButton()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            content.append(self.icon_type(profile))
            content.append(Gtk.Label(label=profile.name, wrap=True, justify=Gtk.Justification.CENTER))
            button.set_child(content)
            button.add_css_class("profile-button")
            button.set_hexpand(True)
            button.set_vexpand(not self.compact_landscape)
            button.set_active(profile.profile_id == active)
            if hasattr(profile, "supported_by_current_engine") and not profile.supported_by_current_engine:
                button.set_tooltip_text("Perfil salvo; aplicação aguarda o motor multizona")
            button.connect("clicked", self._profile_clicked, profile.profile_id)
            self.options_grid.attach(button, index % 2, index // 2, 1, 1)
            self.profile_buttons.append(button)
            self.profile_ids.append(profile.profile_id)
            if profile.profile_id == active:
                self.selected_index = index
        self._show_selection()

    def _show_selection(self) -> None:
        for index, button in enumerate(self.profile_buttons):
            if index == self.selected_index:
                button.add_css_class("keyboard-selected")
            else:
                button.remove_css_class("keyboard-selected")

    def select_relative(self, direction: int) -> None:
        if not self.profile_buttons:
            return
        self.selected_index = (self.selected_index + direction) % len(self.profile_buttons)
        self._show_selection()
        self.profile_buttons[self.selected_index].grab_focus()

    def confirm_selected(self) -> None:
        if not self.profile_ids:
            return
        profile_id = self.profile_ids[self.selected_index]
        if self.activate_profile(profile_id):
            self.hide()

    def _profile_clicked(self, _button, profile_id: str) -> None:
        if profile_id in self.profile_ids:
            self.selected_index = self.profile_ids.index(profile_id)
            self._show_selection()
        if self.activate_profile(profile_id):
            for button in self.profile_buttons:
                button.set_active(False)
            _button.set_active(True)
            self.hide()
        else:
            _button.set_active(False)

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self.select_relative(-1)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self.select_relative(1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.confirm_selected()
            return True
        return False


class ControlApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window: ControlWindow | None = None
        self.input_window: ControlWindow | None = None
        self.button_map = load_button_map()
        self.button_baseline: int | None = None
        self.pressed_buttons: set[int] = set()
        self.background_activation = BACKGROUND_MODE
        self.first_activation = True
        self.poll_source = 0
        self.background_held = False
        toggle_action = Gio.SimpleAction.new("toggle", None)
        toggle_action.connect("activate", lambda *_args: self._toggle_popup())
        self.add_action(toggle_action)

    def do_activate(self) -> None:
        css = Gtk.CssProvider()
        css.load_from_data(PANEL_CSS.encode())
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        if self.window is None:
            self.window = ControlWindow(
                self, "Perfis de telas — MT500–T1161 Linux Graphics Tablet Driver", load_profiles,
                get_active_profile, self._activate_profile, ProfileIcon,
                compact_landscape=True,
            )
            self.input_window = ControlWindow(
                self, "Selecionar perfil de ações — MT500–T1161 Linux Graphics Tablet Driver", load_input_profiles,
                get_active_input_profile, self._activate_input_profile, InputProfileIcon,
            )
            self.window.connect("notify::visible", self._window_visibility_changed)
            self.input_window.connect("notify::visible", self._window_visibility_changed)
            self.poll_source = GLib.timeout_add(25, self._poll_button)
            GLib.timeout_add(1200, self._restore_active_profile)
        if self.background_activation and not self.background_held:
            self.hold()
            self.background_held = True

        # A primeira ativação do autostart apenas arma o monitor. Ativações
        # posteriores (ícone/comando) trazem a mesma janela para a frente.
        if not (self.background_activation and self.first_activation):
            self.window.present()
        self.first_activation = False

    def _window_visibility_changed(self, *_args) -> None:
        visible = any(window is not None and window.get_visible()
                      for window in (self.window, self.input_window))
        self._publish_menu_state(visible, delayed=not visible)

    def _restore_active_profile(self) -> bool:
        self._activate_profile(get_active_profile())
        self._activate_input_profile(get_active_input_profile())
        return False

    def _poll_button(self) -> bool:
        try:
            fields = tuple(int(item) for item in TELEMETRY_PATH.read_text().split())
            if len(fields) < 6:
                return True
            buttons = fields[5]
            if self.button_baseline is None:
                self.button_baseline = buttons
                return True
            pressed_now = {
                physical for physical in range(1, 6)
                if physical_button_pressed(buttons, self.button_baseline, physical, self.button_map)
            }
            for physical in sorted(pressed_now - self.pressed_buttons):
                self._handle_tablet_button(physical)
            self.pressed_buttons = pressed_now
        except (OSError, ValueError):
            pass
        return True

    def _handle_tablet_button(self, physical: int) -> None:
        if self.window is None or self.input_window is None:
            return
        mapping = load_control_actions().get(
            f"tablet-{physical}", {"normal": "none", "menu": "none"})
        visible = self.window.get_visible() or self.input_window.get_visible()
        action = mapping.get("menu", "none") if visible else mapping.get("normal", "none")
        if visible and action == "none":
            action = mapping.get("normal", "none")
        if action == "open-screen-profiles":
            self._toggle_window(self.window, self.input_window)
        elif action == "open-input-profiles":
            self._toggle_window(self.input_window, self.window)
        elif action == "previous":
            self._visible_window_action(lambda window: window.select_relative(-1))
        elif action == "next":
            self._visible_window_action(lambda window: window.select_relative(1))
        elif action == "confirm":
            self._visible_window_action(lambda window: window.confirm_selected())

    def _visible_window_action(self, action) -> None:
        for window in (self.window, self.input_window):
            if window is not None and window.get_visible():
                action(window)
                return

    def _toggle_window(self, target, other) -> None:
        if target.get_visible():
            target.hide()
            self._publish_menu_state(False, delayed=True)
            return
        other.hide()
        target.reload_profiles()
        target.present()
        self._publish_menu_state(True)

    def _toggle_popup(self) -> None:
        if self.window is None:
            self.activate()
            return
        if self.window.get_visible():
            self.window.hide()
            self._publish_menu_state(False, delayed=True)
        else:
            self.window.reload_profiles()
            self.window.present()
            self._publish_menu_state(True)

    def _publish_menu_state(self, visible: bool, delayed: bool = False) -> None:
        def write_state():
            try:
                MENU_STATE_PATH.write_text("1\n" if visible else "0\n")
            except OSError:
                pass
            return False
        if delayed:
            GLib.timeout_add(250, write_state)
        else:
            write_state()

    def _activate_input_profile(self, profile_id: str) -> bool:
        profile = next(
            (item for item in load_input_profiles() if item.profile_id == profile_id),
            None,
        )
        if profile is None:
            return False
        try:
            activate_input_profile(profile)
        except OSError:
            return False
        return True

    def _activate_profile(self, profile_id: str) -> bool:
        profile = next((item for item in load_profiles() if item.profile_id == profile_id), None)
        if profile is None:
            return False
        try:
            monitors = read_monitors()
            active_zones = write_driver_profile(profile, monitors)
        except (GLib.Error, OSError):
            return False
        if active_zones == 0:
            return False
        settings = Gio.Settings.new_with_path(TABLET_SCHEMA, TABLET_PATH)
        settings.set_value("area", GLib.Variant("ad", [0.0, 0.0, 0.0, 0.0]))
        settings.set_strv("output", ["", "", ""])
        settings.set_boolean("keep-aspect", False)
        settings.set_boolean("left-handed", False)
        settings.set_string("mapping", "absolute")
        Gio.Settings.sync()
        set_active_profile(profile.profile_id)
        return True


def main() -> int:
    if "--background" not in sys.argv:
        configurator = Path(__file__).with_name("t1161_configurator.py")
        if configurator.exists():
            os.execv(sys.executable, [sys.executable, str(configurator), *sys.argv[1:]])
        installed = Path.home() / ".local" / "bin" / "t1161-configurator"
        if installed.exists():
            os.execv(str(installed), [str(installed), *sys.argv[1:]])
    arguments = [argument for argument in sys.argv if argument != "--background"]
    return ControlApp().run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
