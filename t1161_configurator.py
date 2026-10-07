#!/usr/bin/env python3
"""T1161 Control: configuração nativa de mapeamento no GNOME/Wayland."""

from __future__ import annotations

import sys
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from t1161_input_profiles import get_active_input_profile, load_input_profiles

APP_ID = "io.github.t1161.Control"
BUS_NAME = "org.gnome.Mutter.DisplayConfig"
OBJECT_PATH = "/org/gnome/Mutter/DisplayConfig"
SCHEMA = "org.gnome.desktop.peripherals.tablet"
SETTINGS_PATH = "/org/gnome/desktop/peripherals/tablets/08f2:6811/"
TELEMETRY_PATH = Path("/run/t1161-control/state")
BUTTON_MAP_PATH = Path.home() / ".config" / "t1161-control" / "buttons.json"
IDLE_TIMEOUT_PATH = Path("/etc/t1161-control/idle-timeout")
IDLE_HELPER = "/usr/libexec/t1161-set-idle-timeout"
LIBINPUT_OVERRIDE_PATH = Path("/etc/libinput/local-overrides.quirks")
PRESSURE_SETTINGS_PATH = Path("/etc/t1161-control/pressure-settings")
CONTROL_ACTIONS_PATH = Path.home() / ".config" / "t1161-control" / "control-actions.json"
RUNTIME_ACTIONS_PATH = Path("/dev/shm/t1161-control-actions")
GESTURE_SETTINGS_PATH = Path.home() / ".config" / "t1161-control" / "gesture-settings.json"
RESET_HELPER_PATH = Path("/usr/libexec/t1161-reset-driver")
PKEXEC_PATH = "/usr/bin/pkexec"
SYSTEMCTL_PATH = "/usr/bin/systemctl"
DEFAULT_BUTTON_MAP = (8, 10, 12, 11, 9, 7, 5, 3, 4, 1, 2, 6)
RAW_BUTTON_BITS = tuple(range(10)) + (12, 13)

ACTION_IDS = (
    "none", "open-screen-profiles", "open-input-profiles", "previous", "next",
    "confirm", "left", "middle", "right", "pen-scroll", "dual-touch", "triple-touch",
    "mirror-zoom", "reverse-expand", "reverse-contract",
)
ACTION_LABELS = (
    "Nenhuma ação", "Abrir perfis de tela", "Abrir perfis de ações",
    "Anterior no menu", "Próximo no menu", "Confirmar no menu",
    "Clique esquerdo", "Clique do meio/scroll", "Clique direito",
    "Rolagem pelo movimento da caneta", "Dois contatos paralelos",
    "Três contatos paralelos", "Zoom espelhado",
    "Espelhamento reverso — expandir", "Espelhamento reverso — contrair",
)
MENU_ACTION_IDS = ("none", "previous", "next", "confirm")
MENU_ACTION_LABELS = ("Usar ação normal", "Anterior no menu", "Próximo no menu", "Confirmar no menu")


def default_control_actions():
    actions = {f"tablet-{index}": {"normal": "none", "menu": "none"} for index in range(1, 13)}
    actions.update({
        "tablet-1": {"normal": "open-screen-profiles", "menu": "none"},
        "tablet-2": {"normal": "open-input-profiles", "menu": "none"},
        "tablet-3": {"normal": "none", "menu": "previous"},
        "tablet-4": {"normal": "none", "menu": "next"},
        "tablet-5": {"normal": "none", "menu": "confirm"},
        "pen-04": {"normal": "right", "menu": "none"},
        "pen-06": {"normal": "middle", "menu": "none"},
        "tip": {"normal": "left", "menu": "none"},
    })
    return actions


def load_control_actions():
    actions = default_control_actions()
    active_id = get_active_input_profile()
    active_profile = next(
        (profile for profile in load_input_profiles() if profile.profile_id == active_id),
        None,
    )
    if active_profile is not None:
        actions.update(active_profile.control_actions)
    return actions


def load_gesture_settings():
    settings = {"spacing": 180, "direction_threshold": 20}
    active_id = get_active_input_profile()
    active_profile = next(
        (profile for profile in load_input_profiles() if profile.profile_id == active_id),
        None,
    )
    if active_profile is not None:
        settings["spacing"] = active_profile.touch_spacing
        settings["direction_threshold"] = active_profile.direction_threshold
    return settings


def save_control_actions(actions, button_map, gesture_settings=None):
    gesture_settings = gesture_settings or load_gesture_settings()
    CONTROL_ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONTROL_ACTIONS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(actions, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(CONTROL_ACTIONS_PATH)
    gesture_temp = GESTURE_SETTINGS_PATH.with_suffix(".tmp")
    gesture_temp.write_text(json.dumps(gesture_settings, indent=2) + "\n")
    gesture_temp.replace(GESTURE_SETTINGS_PATH)
    lines = [f"touch-spacing {gesture_settings['spacing']}",
             f"direction-threshold {gesture_settings['direction_threshold']}"]
    lines += [f"{name} {value['normal']}" for name, value in actions.items()]
    for physical in range(1, 13):
        signal = button_map[physical - 1]
        bit = RAW_BUTTON_BITS[signal - 1]
        lines.append(f"tablet-bit {bit} {actions[f'tablet-{physical}']['normal']}")
        lines.append(f"tablet-menu-bit {bit} {actions[f'tablet-{physical}']['menu']}")
    runtime = RUNTIME_ACTIONS_PATH.with_suffix(".tmp")
    runtime.write_text("\n".join(lines) + "\n")
    runtime.replace(RUNTIME_ACTIONS_PATH)


def load_button_map():
    try:
        values = tuple(int(value) for value in json.loads(BUTTON_MAP_PATH.read_text()))
        if len(values) == 12 and len(set(values)) == 12 and all(1 <= value <= 12 for value in values):
            return values
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return DEFAULT_BUTTON_MAP


def save_button_map(values):
    BUTTON_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = BUTTON_MAP_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(list(values), indent=2) + "\n")
    temporary.replace(BUTTON_MAP_PATH)


def load_pressure_settings():
    try:
        values = [int(value) for value in PRESSURE_SETTINGS_PATH.read_text().split()]
    except (OSError, ValueError):
        values = []
    if len(values) >= 4:
        click, maximum, press, release = values[:4]
    elif len(values) == 3:
        click, press, release = values
        maximum = 0
    else:
        click, maximum, press, release = 1490, 0, 2, 3
    click = max(1, min(2000, click))
    maximum = max(0, min(click - 1, maximum))
    return click, maximum, max(1, min(10, press)), max(1, min(10, release))


def mapped_pressure(raw, click, maximum):
    if raw > click:
        return 0
    return max(1, min(8191, (click - raw) * 8191 // max(1, click - maximum)))


@dataclass(frozen=True)
class Monitor:
    connector: str
    vendor: str
    product: str
    serial: str
    display_name: str
    width: int
    height: int
    scale: float
    x: int
    y: int
    rotation: int
    primary: bool

    @property
    def output(self) -> list[str]:
        return [self.vendor, self.product, self.serial]

    @property
    def label(self) -> str:
        flags = []
        if self.primary:
            flags.append("principal")
        if self.rotation in (1, 3):
            flags.append("vertical")
        suffix = f" • {', '.join(flags)}" if flags else ""
        return f"{self.display_name} ({self.connector}) — {self.width}×{self.height}{suffix}"


def _plain(value):
    return value.unpack() if isinstance(value, GLib.Variant) else value


def parse_monitors(state: tuple) -> list[Monitor]:
    physical = {}
    for spec, modes, properties in state[1]:
        connector, vendor, product, serial = spec
        current = next(
            (mode for mode in modes if _plain(mode[6].get("is-current", False))),
            modes[0] if modes else None,
        )
        if current:
            name = _plain(properties.get("display-name", product or connector))
            physical[connector] = (vendor, product, serial, str(name), current[1], current[2])

    monitors = []
    for x, y, scale, rotation, primary, specs, _properties in state[2]:
        for spec in specs:
            connector = spec[0]
            vendor, product, serial, name, width, height = physical[connector]
            monitors.append(Monitor(
                connector, vendor, product, serial, name, int(width), int(height),
                float(scale), int(x), int(y), int(rotation), bool(primary)
            ))
    return sorted(monitors, key=lambda monitor: (monitor.x, monitor.y, monitor.connector))


class DisplayConfig:
    def __init__(self, changed_callback=None):
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            BUS_NAME, OBJECT_PATH, BUS_NAME, None,
        )
        if changed_callback:
            self.proxy.connect("g-signal", self._signal, changed_callback)

    @staticmethod
    def _signal(_proxy, _sender, signal_name, _parameters, callback):
        if signal_name == "MonitorsChanged":
            callback()

    def monitors(self) -> list[Monitor]:
        result = self.proxy.call_sync(
            "GetCurrentState", None, Gio.DBusCallFlags.NONE, 3000, None
        )
        return parse_monitors(result.unpack())


class MainWindow(Gtk.ApplicationWindow):
    """Janela principal: visualização ao vivo e acesso às ferramentas."""
    def __init__(self, app):
        super().__init__(application=app, title="MT500–T1161 Linux Graphics Tablet Driver")
        self.set_default_size(980, 680)
        self.settings_window = None
        self.state = (0, 0, 0, 0, 0, 0)
        self.raw_axis_bytes = (0, 0, 0, 0, 0, 0)
        self.button_baseline = None
        self.button_map = load_button_map()
        self.mapping_active = False
        self.mapping_result = []
        self.mapping_baseline = None
        self.waiting_release = False
        self.good_frames = 0
        self.has_telemetry = False
        self.raw_pressure = 0
        self.pressure_click, self.pressure_maximum, _, _ = load_pressure_settings()
        self.calibration_stage = None
        self.calibrated_click = None
        self.calibrated_maximum = None
        self.preview_mode = "none"
        self.preview_origin = (0, 0)
        self.preview_direction_point = (0, 0)
        self.preview_normal = None
        self.log_source = 0
        self.log_file = None
        self.log_path = None
        self.log_started_ns = 0
        self.log_deadline_ns = 0
        self.reset_process = None

        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="MT500–T1161 Linux Graphics Tablet Driver"))
        self.set_titlebar(header)

        screen_profiles = Gtk.Button(label="Perfis de telas")
        screen_profiles.connect("clicked", self.open_profiles)
        header.pack_start(screen_profiles)
        input_profiles = Gtk.Button(label="Perfis de ações")
        input_profiles.connect("clicked", self.open_input_profiles)
        header.pack_start(input_profiles)
        settings_button = Gtk.Button(label="Ajustes da caneta")
        settings_button.connect("clicked", self.open_settings)
        header.pack_start(settings_button)

        mapping_menu = Gio.Menu()
        mapping_menu.append("Mapear os 12 botões", "win.map-buttons")
        mapping_menu.append("Restaurar mapeamento original", "win.reset-buttons")
        mapping_button = Gtk.MenuButton(label="Mapeamento físico", menu_model=mapping_menu)
        header.pack_start(mapping_button)

        settings_action = Gio.SimpleAction.new("open-settings", None)
        settings_action.connect("activate", self.open_settings)
        self.add_action(settings_action)
        profiles_action = Gio.SimpleAction.new("open-profiles", None)
        profiles_action.connect("activate", self.open_profiles)
        self.add_action(profiles_action)
        mapping_action = Gio.SimpleAction.new("map-buttons", None)
        mapping_action.connect("activate", lambda *_args: self.start_mapping(None))
        self.add_action(mapping_action)
        reset_action = Gio.SimpleAction.new("reset-buttons", None)
        reset_action.connect("activate", self.reset_mapping)
        self.add_action(reset_action)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(18); root.set_margin_bottom(18)
        root.set_margin_start(18); root.set_margin_end(18)
        self.set_child(root)
        title = Gtk.Label(label="Visualizador da mesa", xalign=0)
        title.add_css_class("title-2")
        root.append(title)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_content_width(850); self.canvas.set_content_height(450)
        self.canvas.set_vexpand(True)
        self.canvas.set_draw_func(self.draw)
        root.append(self.canvas)
        self.readout = Gtk.Label(xalign=0)
        root.append(self.readout)
        self.byte_readout = Gtk.Label(xalign=0, selectable=True)
        self.byte_readout.add_css_class("monospace")
        root.append(self.byte_readout)
        pressure_feedback = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.pressure_feedback = Gtk.Label(xalign=0, wrap=True)
        self.pressure_level = Gtk.LevelBar.new_for_interval(0, 8191)
        self.pressure_level.set_hexpand(True)
        pressure_feedback.append(self.pressure_feedback)
        pressure_feedback.append(self.pressure_level)
        root.append(pressure_feedback)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.map_status = Gtk.Label(label="Mapeamento físico carregado.", xalign=0)
        self.map_status.set_hexpand(True)
        controls.append(self.map_status)
        self.log_button = Gtk.Button(label="Log de 10 segundos")
        self.log_button.set_tooltip_text(
            "Registra tempo, coordenadas, pressão, botões e bytes brutos do driver"
        )
        self.log_button.connect("clicked", self.start_log)
        controls.append(self.log_button)
        self.reset_button = Gtk.Button(label="Reiniciar driver")
        self.reset_button.set_tooltip_text("Reinicia a conexão USB e o serviço da T1161")
        self.reset_button.connect("clicked", self.restart_driver)
        controls.append(self.reset_button)
        root.append(controls)
        self.timer = GLib.timeout_add(16, self.update_state)
        self.connect("close-request", self.closed)

    def closed(self, *_args):
        if self.timer:
            GLib.source_remove(self.timer); self.timer = None
        if self.log_source:
            GLib.source_remove(self.log_source); self.log_source = 0
        if self.log_file is not None:
            self.log_file.close(); self.log_file = None
        return False

    def start_log(self, _button):
        if self.log_source:
            return
        log_dir = Path.home() / ".local" / "state" / "t1161-control" / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            self.log_path = log_dir / f"driver-{stamp}.tsv"
            self.log_file = self.log_path.open("w", encoding="utf-8")
            self.log_file.write("elapsed_ms\twall_time\tstate\n")
        except OSError as error:
            self.map_status.set_text(f"Não foi possível iniciar o log: {error}")
            return
        self.log_started_ns = time.monotonic_ns()
        self.log_deadline_ns = self.log_started_ns + 10_000_000_000
        self.log_button.set_sensitive(False)
        self.log_button.set_label("Gravando… 10,0 s")
        self.map_status.set_text("Log iniciado: mova a caneta, pressione, mova e solte o botão.")
        self.log_source = GLib.timeout_add(10, self.capture_log_sample)

    def capture_log_sample(self):
        now_ns = time.monotonic_ns()
        elapsed_ms = (now_ns - self.log_started_ns) / 1_000_000
        try:
            state = " ".join(TELEMETRY_PATH.read_text().split())
        except OSError as error:
            state = f"ERRO: {error}"
        wall_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
        try:
            self.log_file.write(f"{elapsed_ms:.3f}\t{wall_time}\t{state}\n")
        except OSError as error:
            self.finish_log(f"Falha ao gravar log: {error}")
            return False
        remaining = max(0.0, (self.log_deadline_ns - now_ns) / 1_000_000_000)
        self.log_button.set_label(f"Gravando… {remaining:.1f} s")
        if now_ns < self.log_deadline_ns:
            return True
        self.finish_log(f"Log salvo em {self.log_path}")
        return False

    def finish_log(self, message):
        if self.log_file is not None:
            self.log_file.close(); self.log_file = None
        self.log_source = 0
        self.log_button.set_label("Log concluído")
        self.log_button.set_tooltip_text(str(self.log_path) if self.log_path else message)
        self.map_status.set_text(message)
        GLib.timeout_add_seconds(4, self.restore_log_button)

    def restore_log_button(self):
        self.log_button.set_label("Log de 10 segundos")
        self.log_button.set_sensitive(True)
        return False

    def restart_driver(self, _button):
        if self.reset_process is not None:
            return
        self.reset_button.set_sensitive(False)
        self.reset_button.set_label("Reiniciando…")
        command = ([PKEXEC_PATH, str(RESET_HELPER_PATH)]
                   if RESET_HELPER_PATH.is_file()
                   else [PKEXEC_PATH, SYSTEMCTL_PATH, "restart", "t1161-driver.service"])
        try:
            self.reset_process = Gio.Subprocess.new(
                command,
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self.reset_process.wait_check_async(None, self.driver_restarted)
        except GLib.Error as error:
            self.map_status.set_text(f"Não foi possível reiniciar o driver: {error.message}")
            self.finish_driver_restart(False)

    def driver_restarted(self, process, result):
        try:
            successful = process.wait_check_finish(result)
        except GLib.Error:
            successful = False
        self.reset_process = None
        self.finish_driver_restart(successful)

    def finish_driver_restart(self, successful):
        self.reset_button.set_label("Driver reiniciado" if successful else "Falha ao reiniciar")
        self.map_status.set_text(
            "Driver reiniciado com sucesso." if successful else "Não foi possível reiniciar o driver.")
        GLib.timeout_add_seconds(4, self.restore_reset_button)

    def restore_reset_button(self):
        self.reset_button.set_label("Reiniciar driver")
        self.reset_button.set_sensitive(True)
        return False

    def open_settings(self, *_args):
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
            self.settings_window.connect("destroy", self.settings_closed)
        self.settings_window.present()

    def settings_closed(self, _window):
        self.settings_window = None
        self.calibration_stage = None
        self.calibrated_click = None
        self.calibrated_maximum = None

    def open_profiles(self, *_args):
        try:
            Gio.Subprocess.new(["t1161-profile-manager"], Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.map_status.set_text(f"Não foi possível abrir os perfis: {error.message}")

    def open_input_profiles(self, *_args):
        try:
            Gio.Subprocess.new(["t1161-profile-manager", "--input-profiles"], Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.map_status.set_text(f"Não foi possível abrir os perfis de ações: {error.message}")

    def reload_pressure_settings(self):
        self.pressure_click, self.pressure_maximum, _, _ = load_pressure_settings()

    def reset_mapping(self, *_args):
        self.cancel_mapping()
        self.button_map = DEFAULT_BUTTON_MAP
        save_button_map(self.button_map)
        self.map_status.set_text("Mapeamento original restaurado.")
        self.canvas.queue_draw()

    def update_state(self):
        try:
            fields = tuple(int(item) for item in TELEMETRY_PATH.read_text().split())
            if len(fields) >= 6:
                self.has_telemetry = True
                self.good_frames += 1
                self.state = fields[:6]
                if len(fields) >= 12:
                    self.raw_axis_bytes = fields[6:12]
                if self.button_baseline is None:
                    self.button_baseline = fields[5]
                x, y, pressure, touching, pen, tablet = self.state
                x_mm, y_mm = x * 300 / 4095, y * 188 / 4095
                self.readout.set_text(
                    f"Posição {x_mm:5.1f} × {y_mm:5.1f} mm   "
                    f"(X {x:4d}, Y {y:4d})   Pressão {pressure:4d}   "
                    f"Ponta {'encostada' if touching else 'em proximidade'}   "
                    f"Caneta 0x{pen:02x}   Botões brutos 0x{tablet:04x}"
                )
                xh, xl, yh, yl, ph, pl = self.raw_axis_bytes
                self.raw_pressure = (ph << 8) | pl
                preview_click = self.calibrated_click or self.pressure_click
                preview_maximum = (
                    self.calibrated_maximum
                    if self.calibrated_maximum is not None
                    else self.pressure_maximum
                )
                preview = mapped_pressure(self.raw_pressure, preview_click, preview_maximum)
                self.pressure_level.set_value(preview)
                stage = {
                    "click": "aguardando Enter para o toque mínimo",
                    "maximum": "aguardando Enter para a força máxima",
                    "complete": "medidas prontas para aplicar",
                }.get(self.calibration_stage, "configuração ativa")
                self.pressure_feedback.set_text(
                    f"Pressão: leitura bruta {self.raw_pressure}  •  clique {preview_click}  •  "
                    f"força máxima {preview_maximum}  •  saída {preview}/8191  •  {stage}. "
                    "Nesta mesa, o número bruto diminui quando a força aumenta."
                )
                if self.settings_window is not None:
                    self.settings_window.update_pressure_feedback(self.raw_pressure)
                self.byte_readout.set_text(
                    f"Quadro {self.good_frames}   Bytes (hex, big-endian)   X [{xh:02X} {xl:02X}] → {x:4d}   "
                    f"Y [{yh:02X} {yl:02X}] → {y:4d}   "
                    f"Pressão bruta [{ph:02X} {pl:02X}] → {(ph << 8) | pl:4d}"
                )
                self.canvas.queue_draw()
                self.process_mapping(tablet)
        except Exception as error:
            # Mantém o último quadro bom se a leitura coincidir com uma
            # regravação do arquivo, em vez de apagar o visualizador.
            if not self.has_telemetry:
                self.readout.set_text(f"Aguardando dados do serviço T1161… ({error})")
        return True

    def start_mapping(self, _button):
        if self.mapping_active:
            self.cancel_mapping()
            return
        self.mapping_active = True
        self.mapping_result = []
        self.mapping_baseline = self.button_baseline if self.button_baseline is not None else self.state[5]
        self.waiting_release = False
        self.map_status.set_text("Solte todos os botões; depois pressione o botão físico 1.")

    def cancel_mapping(self, _button=None):
        self.mapping_active = False
        self.mapping_result = []
        self.waiting_release = False
        self.map_status.set_text("Mapeamento cancelado; configuração anterior preservada.")

    def process_mapping(self, buttons):
        if not self.mapping_active or self.mapping_baseline is None:
            return
        changed = buttons ^ self.mapping_baseline
        signals = [index + 1 for index, bit in enumerate(RAW_BUTTON_BITS) if changed & (1 << bit)]
        if self.waiting_release:
            if not signals:
                self.waiting_release = False
                next_button = len(self.mapping_result) + 1
                self.map_status.set_text(f"Pressione o botão físico {next_button}.")
            return
        if not signals:
            return
        if len(signals) != 1:
            self.map_status.set_text("Mais de um sinal detectado. Solte todos e pressione somente um botão.")
            return
        signal = signals[0]
        if signal in self.mapping_result:
            physical = self.mapping_result.index(signal) + 1
            self.map_status.set_text(
                f"Repetição: esse sinal já pertence ao botão físico {physical}. Solte e tente novamente."
            )
            return
        self.mapping_result.append(signal)
        self.waiting_release = True
        current = len(self.mapping_result)
        self.map_status.set_text(f"Botão físico {current} reconhecido como sinal {signal}. Solte-o.")
        if current == 12:
            self.button_map = tuple(self.mapping_result)
            save_button_map(self.button_map)
            self.mapping_active = False
            self.waiting_release = False
            self.map_status.set_text("Mapeamento concluído: 12 sinais únicos salvos.")
            self.canvas.queue_draw()

    def draw(self, _area, ctx, width, height):
        ctx.set_source_rgb(0.10, 0.11, 0.13); ctx.paint()
        margin, panel = 28.0, 125.0
        available_w, available_h = width-panel-margin*3, height-margin*2-35
        landscape_w = min(available_w, available_h*(300/188))
        landscape_h = landscape_w*(188/300)
        x0 = panel+margin*2+(available_w-landscape_w)/2
        y0 = margin+(available_h-landscape_h)/2

        # Área paisagem confirmada.
        ctx.set_source_rgb(0.20, 0.23, 0.28); ctx.rectangle(x0,y0,landscape_w,landscape_h); ctx.fill()
        ctx.set_source_rgb(0.30, 0.72, 1.0); ctx.set_line_width(3); ctx.rectangle(x0,y0,landscape_w,landscape_h); ctx.stroke()

        # O alvo central é a coordenada real. O modo multitoque mostra os dois
        # contatos sintetizados simetricamente ao redor dele.
        x,y,pressure,touching,pen,buttons = self.state
        mx=x0+max(0,min(4095,x))/4095*landscape_w
        my=y0+max(0,min(4095,y))/4095*landscape_h
        radius=7+min(pressure,8191)/8191*13
        actions = load_control_actions()
        gesture_settings = load_gesture_settings()
        spacing = gesture_settings["spacing"]
        direction_threshold = gesture_settings["direction_threshold"]
        baseline = self.button_baseline if self.button_baseline is not None else buttons
        active_mode = "none"
        for physical in range(1, 13):
            signal = self.button_map[physical - 1]
            bit = RAW_BUTTON_BITS[signal - 1]
            candidate = actions[f"tablet-{physical}"]["normal"]
            if candidate in ("dual-touch", "triple-touch", "mirror-zoom",
                             "reverse-expand", "reverse-contract") \
                    and (buttons ^ baseline) & (1 << bit):
                active_mode = candidate
        if pen == 0x04:
            active_mode = actions["pen-04"]["normal"]
        elif pen == 0x06:
            active_mode = actions["pen-06"]["normal"]
        if not touching:
            active_mode = "none"
        if active_mode != self.preview_mode:
            self.preview_mode = active_mode
            self.preview_origin = (x, y)
            self.preview_direction_point = (x, y)
            self.preview_normal = None
        dx, dy = x-self.preview_origin[0], y-self.preview_origin[1]
        movement = math.hypot(dx, dy)
        direction_dx = x-self.preview_direction_point[0]
        direction_dy = y-self.preview_direction_point[1]
        direction_movement = math.hypot(direction_dx, direction_dy)
        if direction_movement >= direction_threshold:
            self.preview_normal = (-direction_dy/direction_movement,
                                   direction_dx/direction_movement)
            self.preview_direction_point = (x, y)
        nx, ny = self.preview_normal or (1.0, 0.0)
        if active_mode == "dual-touch":
            contacts = [(x-spacing*nx, y-spacing*ny), (x+spacing*nx, y+spacing*ny)]
        elif active_mode == "triple-touch":
            contacts = [(x-spacing*nx, y-spacing*ny), (x, y), (x+spacing*nx, y+spacing*ny)]
        elif active_mode == "mirror-zoom":
            contacts = [(x, y), (2*self.preview_origin[0]-x, 2*self.preview_origin[1]-y)]
        elif active_mode in ("reverse-expand", "reverse-contract"):
            radius = max(5, min(1200, spacing + (movement if active_mode == "reverse-expand" else -movement)))
            contacts = [(self.preview_origin[0]-radius*nx, self.preview_origin[1]-radius*ny),
                        (self.preview_origin[0]+radius*nx, self.preview_origin[1]+radius*ny)]
        else:
            contacts = [(x, y)]
        for contact_x_raw, contact_y_raw in contacts:
            contact_x = x0+max(0,min(4095,contact_x_raw))/4095*landscape_w
            contact_y = y0+max(0,min(4095,contact_y_raw))/4095*landscape_h
            ctx.set_source_rgba(1.0,0.25,0.20,0.95 if touching else 0.60)
            ctx.arc(contact_x,contact_y,radius,0,6.2832); ctx.fill()
            ctx.set_source_rgb(1,1,1); ctx.set_line_width(1.5)
            ctx.arc(contact_x,contact_y,radius+2,0,6.2832); ctx.stroke()
        ctx.set_source_rgb(0.25,0.75,1.0); ctx.set_line_width(1.5)
        ctx.move_to(mx-7,my); ctx.line_to(mx+7,my); ctx.move_to(mx,my-7); ctx.line_to(mx,my+7); ctx.stroke()

        # Correspondência medida pelo usuário: sinal lógico atual -> botão físico.
        # Em ordem física 1..12, os sinais são:
        physical_to_signal = self.button_map
        ctx.set_source_rgb(0.85,0.87,0.90); ctx.set_font_size(14)
        ctx.move_to(margin,y0-8); ctx.show_text("12 botões")
        raw_bits = RAW_BUTTON_BITS
        for row in range(6):
            for col in range(2):
                index=row*2+col
                signal=physical_to_signal[index]
                bit=raw_bits[signal-1]
                bx=margin+col*48; by=y0+row*48
                changed=((buttons ^ baseline) & (1<<bit)) != 0
                ctx.set_source_rgb(1.0,0.55,0.15) if changed else ctx.set_source_rgb(0.27,0.30,0.35)
                ctx.rounded_rectangle(bx,by,38,34,7) if hasattr(ctx,"rounded_rectangle") else ctx.rectangle(bx,by,38,34)
                ctx.fill(); ctx.set_source_rgb(0.9,0.9,0.9); ctx.set_font_size(12)
                label=str(index+1)
                ctx.move_to(bx+(13 if len(label)==1 else 8),by+22); ctx.show_text(label)
        ctx.set_source_rgb(0.65,0.67,0.70); ctx.set_font_size(11)
        ctx.move_to(margin,y0+6*48+16); ctx.show_text("ordem física confirmada")

        # Dois botões laterais e a ponta lógica, cuja ativação já respeita a
        # calibração de pressão do driver.
        ctx.set_source_rgb(0.85,0.87,0.90); ctx.set_font_size(14)
        ctx.move_to(margin,y0+6*48+48); ctx.show_text("Caneta")
        pen_controls = [("04", pen == 0x04), ("06", pen == 0x06), ("P", bool(touching))]
        for index, (label, active) in enumerate(pen_controls):
            bx=margin+index*42; by=y0+6*48+58
            ctx.set_source_rgb(1.0,0.55,0.15) if active else ctx.set_source_rgb(0.27,0.30,0.35)
            ctx.rectangle(bx,by,34,30); ctx.fill()
            ctx.set_source_rgb(0.9,0.9,0.9); ctx.set_font_size(11)
            ctx.move_to(bx+8,by+20); ctx.show_text(label)


class ControlActionsWindow(Gtk.Window):
    """Editor único das ações atribuídas aos controles físicos e lógicos."""

    def __init__(self, parent):
        super().__init__(title="Ações dos controles — T1161")
        self.set_transient_for(parent)
        self.set_default_size(720, 720)
        self.parent_window = parent
        self.actions = load_control_actions()
        self.dropdowns = {}
        self.menu_dropdowns = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_top(20); outer.set_margin_bottom(20)
        outer.set_margin_start(20); outer.set_margin_end(20)
        heading = Gtk.Label(label="Ações dos controles", xalign=0)
        heading.add_css_class("title-1")
        outer.append(heading)
        outer.append(Gtk.Label(
            label=("Escolha o que cada entrada faz. “Toque duplo” funciona como modificador: "
                   "mantenha o controle pressionado e encoste a ponta da caneta."),
            xalign=0, wrap=True,
        ))

        grid = Gtk.Grid(column_spacing=18, row_spacing=8)
        grid.attach(Gtk.Label(label="Controle", xalign=0), 0, 0, 1, 1)
        grid.attach(Gtk.Label(label="Fora do menu", xalign=0), 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Com menu aberto", xalign=0), 2, 0, 1, 1)
        names = [(f"tablet-{index}", f"Botão físico {index}") for index in range(1, 13)]
        names += [("pen-04", "Caneta — botão 0x04"), ("pen-06", "Caneta — botão 0x06"),
                  ("tip", "Caneta — ponta/pressão")]
        for row, (control, label) in enumerate(names, 1):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            dropdown = Gtk.DropDown.new_from_strings(list(ACTION_LABELS))
            dropdown.set_hexpand(True)
            dropdown.set_selected(ACTION_IDS.index(self.actions[control]["normal"]))
            grid.attach(dropdown, 1, row, 1, 1)
            menu_dropdown = Gtk.DropDown.new_from_strings(list(MENU_ACTION_LABELS))
            menu_dropdown.set_selected(MENU_ACTION_IDS.index(self.actions[control]["menu"]))
            menu_dropdown.set_sensitive(control.startswith("tablet-"))
            grid.attach(menu_dropdown, 2, row, 1, 1)
            self.dropdowns[control] = dropdown
            self.menu_dropdowns[control] = menu_dropdown
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(grid)
        outer.append(scrolled)

        geometry = load_gesture_settings()
        gesture_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        gesture_row.append(Gtk.Label(label="Distância entre contatos:"))
        self.spacing = Gtk.SpinButton.new_with_range(10, 500, 5)
        self.spacing.set_value(geometry["spacing"])
        gesture_row.append(self.spacing)
        gesture_row.append(Gtk.Label(label="Limiar para fixar direção:"))
        self.direction_threshold = Gtk.SpinButton.new_with_range(5, 200, 5)
        self.direction_threshold.set_value(geometry["direction_threshold"])
        gesture_row.append(self.direction_threshold)
        outer.append(gesture_row)

        self.status = Gtk.Label(xalign=0, wrap=True)
        outer.append(self.status)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        restore = Gtk.Button(label="Restaurar padrão")
        restore.connect("clicked", self.restore_defaults)
        save = Gtk.Button(label="Salvar ações")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.save)
        buttons.append(restore); buttons.append(save)
        outer.append(buttons)
        self.set_child(outer)

    def restore_defaults(self, _button):
        defaults = default_control_actions()
        for control, dropdown in self.dropdowns.items():
            dropdown.set_selected(ACTION_IDS.index(defaults[control]["normal"]))
            self.menu_dropdowns[control].set_selected(
                MENU_ACTION_IDS.index(defaults[control]["menu"]))
        self.status.set_text("Padrões carregados; use “Salvar ações” para aplicar.")

    def save(self, _button):
        actions = {
            control: {
                "normal": ACTION_IDS[dropdown.get_selected()],
                "menu": MENU_ACTION_IDS[self.menu_dropdowns[control].get_selected()],
            }
            for control, dropdown in self.dropdowns.items()
        }
        try:
            settings = {"spacing": self.spacing.get_value_as_int(),
                        "direction_threshold": self.direction_threshold.get_value_as_int()}
            save_control_actions(actions, self.parent_window.button_map, settings)
            self.actions = actions
            self.status.set_text("Ações salvas e publicadas para o driver.")
            self.parent_window.canvas.queue_draw()
        except OSError as error:
            self.status.set_text(f"Não foi possível salvar: {error}")


class SettingsWindow(Gtk.Window):
    def __init__(self, parent):
        super().__init__(title="Configurações — MT500–T1161 Linux Graphics Tablet Driver")
        self.set_transient_for(parent)
        self.parent_window = parent
        self.set_default_size(740, 720)
        self.settings = Gio.Settings.new_with_path(SCHEMA, SETTINGS_PATH)
        self.display_config = DisplayConfig(self.refresh)
        self.monitors = []
        self.calibration_stage = None
        self.calibration_enter_down = False
        self.calibration_accept_after = 0
        self.calibrated_click = None
        self.calibrated_maximum = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(root)
        self.set_child(scrolled)

        heading = Gtk.Label(label="Configurações", xalign=0)
        heading.add_css_class("title-1")
        root.append(heading)
        root.append(Gtk.Label(
            label="Ajustes organizados por tela, ponteiro e pressão.",
            xalign=0,
        ))

        grid = Gtk.Grid(column_spacing=18, row_spacing=16)
        root.append(grid)
        section = Gtk.Label(label="Telas e movimento", xalign=0)
        section.add_css_class("heading")
        grid.attach(section, 0, 0, 2, 1)
        grid.attach(Gtk.Label(label="Área controlada", xalign=0), 0, 1, 1, 1)
        self.output = Gtk.DropDown()
        self.output.set_hexpand(True)
        grid.attach(self.output, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Movimento", xalign=0), 0, 2, 1, 1)
        self.mapping = Gtk.DropDown.new_from_strings([
            "Absoluto (posição da caneta)",
            "Relativo (como um mouse)",
        ])
        grid.attach(self.mapping, 1, 2, 1, 1)

        self.aspect = Gtk.CheckButton(label="Preservar proporção")
        self.aspect.set_tooltip_text(
            "Evita movimento esticado; pode deixar margens sem uso na mesa."
        )
        grid.attach(self.aspect, 1, 3, 1, 1)

        pointer_section = Gtk.Label(label="Ponteiro", xalign=0)
        pointer_section.add_css_class("heading")
        grid.attach(pointer_section, 0, 4, 2, 1)
        grid.attach(Gtk.Label(label="Quando estiver parada", xalign=0), 0, 5, 1, 1)
        idle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.pointer_mode = Gtk.DropDown.new_from_strings([
            "Sempre visível, mesmo sem movimento",
            "Ocultar depois de um tempo sem movimento",
        ])
        self.pointer_mode.set_hexpand(True)
        self.pointer_mode.connect("notify::selected", self.pointer_mode_changed)
        self.idle_seconds = Gtk.SpinButton.new_with_range(1, 60, 1)
        self.idle_seconds.set_value(5)
        self.idle_seconds.set_tooltip_text("Tempo sem mudança nos dados da caneta")
        idle_box.append(self.pointer_mode)
        idle_box.append(Gtk.Label(label="Tempo:"))
        idle_box.append(self.idle_seconds)
        idle_box.append(Gtk.Label(label="s"))
        grid.attach(idle_box, 1, 5, 1, 1)

        pressure_section = Gtk.Label(label="Pressão", xalign=0)
        pressure_section.add_css_class("heading")
        grid.attach(pressure_section, 0, 6, 2, 1)
        grid.attach(Gtk.Label(label="Toque mínimo para clicar", xalign=0), 0, 7, 1, 1)
        pressure_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.pressure_threshold = Gtk.SpinButton.new_with_range(1, 2000, 1)
        self.pressure_threshold.set_tooltip_text("Valor maior exige menos força da caneta")
        pressure_box.append(self.pressure_threshold)
        pressure_hint = Gtk.Label(label="maior = toque mais leve", xalign=0)
        pressure_hint.add_css_class("dim-label")
        pressure_box.append(pressure_hint)
        grid.attach(pressure_box, 1, 7, 1, 1)

        grid.attach(Gtk.Label(label="Força máxima", xalign=0), 0, 8, 1, 1)
        maximum_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.pressure_maximum = Gtk.SpinButton.new_with_range(0, 1999, 1)
        self.pressure_maximum.set_tooltip_text("Leitura bruta obtida com a maior força desejada")
        maximum_box.append(self.pressure_maximum)
        maximum_box.append(Gtk.Label(label="menor que o toque mínimo", xalign=0))
        grid.attach(maximum_box, 1, 8, 1, 1)

        grid.attach(Gtk.Label(label="Confirmar toque", xalign=0), 0, 9, 1, 1)
        self.press_frames = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.press_frames.set_tooltip_text("Leituras seguidas necessárias para iniciar o toque")
        grid.attach(self.press_frames, 1, 9, 1, 1)

        grid.attach(Gtk.Label(label="Confirmar soltura", xalign=0), 0, 10, 1, 1)
        self.release_frames = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.release_frames.set_tooltip_text("Leituras sem pressão necessárias para soltar")
        grid.attach(self.release_frames, 1, 10, 1, 1)

        calibration_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.calibrate_button = Gtk.Button(label="Calibrar com a caneta")
        self.calibrate_button.connect("clicked", self.start_pressure_calibration)
        self.use_calibration_button = Gtk.Button(label="Usar medidas capturadas")
        self.use_calibration_button.set_sensitive(False)
        self.use_calibration_button.connect("clicked", self.use_pressure_calibration)
        calibration_buttons.append(self.calibrate_button)
        calibration_buttons.append(self.use_calibration_button)
        grid.attach(Gtk.Label(label="Calibração guiada", xalign=0), 0, 11, 1, 1)
        grid.attach(calibration_buttons, 1, 11, 1, 1)

        self.calibration_status = Gtk.Label(
            label="Use a calibração guiada ou informe os dois valores manualmente.",
            xalign=0,
            wrap=True,
        )
        self.calibration_status.add_css_class("dim-label")
        grid.attach(self.calibration_status, 1, 12, 1, 1)

        calibration_feedback = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.calibration_readout = Gtk.Label(xalign=0, wrap=True)
        self.calibration_level = Gtk.LevelBar.new_for_interval(0, 8191)
        self.calibration_level.set_hexpand(True)
        calibration_feedback.append(self.calibration_readout)
        calibration_feedback.append(self.calibration_level)
        grid.attach(calibration_feedback, 1, 13, 1, 1)

        numeric_button = Gtk.Button(label="Usar valores numéricos")
        numeric_button.connect("clicked", self.apply)
        grid.attach(numeric_button, 1, 14, 1, 1)

        explanation = Gtk.Label(
            label=("Para usar as duas telas, selecione “Todos os monitores”. "
                   "O GNOME soma posições, rotações e escalas automaticamente."),
            xalign=0, wrap=True,
        )
        explanation.add_css_class("dim-label")
        root.append(explanation)

        self.status = Gtk.Label(xalign=0, wrap=True)
        root.append(self.status)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        actions.set_halign(Gtk.Align.END)
        refresh = Gtk.Button(label="Atualizar telas")
        refresh.connect("clicked", lambda _button: self.refresh())
        apply_button = Gtk.Button(label="Aplicar")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", self.apply)
        actions.append(refresh)
        actions.append(apply_button)
        root.append(actions)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.key_pressed)
        key_controller.connect("key-released", self.key_released)
        self.add_controller(key_controller)
        self.load_idle_timeout()
        self.refresh(load_current=True)

    def start_pressure_calibration(self, _button):
        self.calibration_stage = "click"
        self.calibration_enter_down = False
        self.calibration_accept_after = GLib.get_monotonic_time() + 250_000
        self.calibrated_click = None
        self.calibrated_maximum = None
        self.parent_window.calibration_stage = "click"
        self.parent_window.calibrated_click = None
        self.parent_window.calibrated_maximum = None
        self.use_calibration_button.set_sensitive(False)
        self.calibrate_button.set_label("Recomeçar calibração")
        self.calibration_status.set_text(
            "Encoste a caneta com a menor força que deve clicar e pressione Enter."
        )

    def key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) or self.calibration_stage is None:
            return False
        if self.calibration_enter_down:
            return True
        self.calibration_enter_down = True
        if GLib.get_monotonic_time() < self.calibration_accept_after:
            return True
        if not self.parent_window.has_telemetry:
            self.calibration_status.set_text("Aguardando uma leitura da caneta para calibrar.")
            return True
        raw_pressure = self.parent_window.raw_pressure
        if self.calibration_stage == "click":
            self.calibrated_click = raw_pressure
            self.pressure_threshold.set_value(raw_pressure)
            self.parent_window.calibrated_click = raw_pressure
            self.calibration_stage = "maximum"
            self.parent_window.calibration_stage = "maximum"
            self.calibration_accept_after = GLib.get_monotonic_time() + 250_000
            self.calibration_status.set_text(
                f"Toque mínimo capturado: {raw_pressure}. Solte Enter; depois aplique a "
                "maior força desejada e pressione Enter novamente."
            )
            return True
        if self.calibration_stage == "complete":
            return True
        if raw_pressure >= self.calibrated_click:
            self.calibration_status.set_text(
                "A força máxima precisa produzir um número menor que o toque mínimo. "
                "Pressione mais e tente Enter novamente."
            )
            return True
        self.calibrated_maximum = raw_pressure
        self.pressure_maximum.set_value(raw_pressure)
        self.parent_window.calibrated_maximum = raw_pressure
        self.calibration_stage = "complete"
        self.parent_window.calibration_stage = "complete"
        self.use_calibration_button.set_sensitive(True)
        self.calibration_status.set_text(
            f"Calibração pronta: clique {self.calibrated_click}, máximo {raw_pressure}. "
            "Confira a barra na tela principal e use as medidas capturadas."
        )
        return True

    def key_released(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.calibration_enter_down = False

    def use_pressure_calibration(self, _button):
        if self.calibrated_click is None or self.calibrated_maximum is None:
            return
        self.apply(None)

    def update_pressure_feedback(self, raw_pressure):
        click = self.pressure_threshold.get_value_as_int()
        maximum = self.pressure_maximum.get_value_as_int()
        output = mapped_pressure(raw_pressure, click, min(maximum, click - 1))
        self.calibration_level.set_value(output)
        self.calibration_readout.set_text(
            f"Agora: leitura bruta {raw_pressure} → saída {output}/8191"
        )

    def load_idle_timeout(self):
        try:
            seconds = int(IDLE_TIMEOUT_PATH.read_text().strip())
        except (OSError, ValueError):
            seconds = 0
        self.pointer_mode.set_selected(0 if seconds == 0 else 1)
        self.idle_seconds.set_value(max(1, min(60, seconds or 5)))
        click, maximum, press, release = load_pressure_settings()
        self.pressure_threshold.set_value(click)
        self.pressure_maximum.set_value(maximum)
        self.press_frames.set_value(press)
        self.release_frames.set_value(release)
        self.pointer_mode_changed(self.pointer_mode, None)

    def pointer_mode_changed(self, dropdown, _property):
        self.idle_seconds.set_sensitive(dropdown.get_selected() == 1)

    def refresh(self, load_current=False):
        try:
            self.monitors = self.display_config.monitors()
            labels = ["Todos os monitores (área somada)"] + [m.label for m in self.monitors]
            self.output.set_model(Gtk.StringList.new(labels))
            current = self.settings.get_strv("output")
            selected = 0
            if any(current):
                for index, monitor in enumerate(self.monitors, 1):
                    if monitor.output == list(current):
                        selected = index
                        break
            self.output.set_selected(selected)
            if load_current:
                self.aspect.set_active(self.settings.get_boolean("keep-aspect"))
                self.mapping.set_selected(
                    1 if self.settings.get_string("mapping") == "relative" else 0
                )
            self.status.set_text(f"T1161 conectada • {len(self.monitors)} telas detectadas")
        except GLib.Error as error:
            self.status.set_text(f"Não foi possível consultar as telas: {error.message}")

    def apply(self, _button):
        selected = self.output.get_selected()
        if selected > len(self.monitors):
            self.status.set_text("Atualize a lista de telas e tente novamente.")
            return
        target = ["", "", ""] if selected == 0 else self.monitors[selected - 1].output
        self.settings.set_strv("output", target)
        self.settings.set_boolean("keep-aspect", self.aspect.get_active())
        self.settings.set_string("mapping", "relative" if self.mapping.get_selected() == 1 else "absolute")
        Gio.Settings.sync()
        destination = "todos os monitores" if selected == 0 else self.monitors[selected - 1].display_name
        seconds = 0 if self.pointer_mode.get_selected() == 0 else self.idle_seconds.get_value_as_int()
        force_visible = "0"
        pressure_threshold_value = self.pressure_threshold.get_value_as_int()
        pressure_maximum_value = self.pressure_maximum.get_value_as_int()
        if pressure_maximum_value >= pressure_threshold_value:
            self.status.set_text("A força máxima deve ter um número menor que o toque mínimo.")
            return
        pressure_threshold = str(pressure_threshold_value)
        pressure_maximum = str(pressure_maximum_value)
        press_frames = str(self.press_frames.get_value_as_int())
        release_frames = str(self.release_frames.get_value_as_int())
        try:
            process = Gio.Subprocess.new(
                ["pkexec", IDLE_HELPER, str(seconds), force_visible,
                 pressure_threshold, pressure_maximum, press_frames, release_frames],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            process.wait_async(None, self.idle_applied, (process, destination, seconds))
            self.status.set_text("Aplicando configuração do ponteiro…")
        except GLib.Error as error:
            self.status.set_text(f"Tela aplicada, mas não foi possível ajustar o ponteiro: {error.message}")

    def idle_applied(self, _process, result, context):
        process, destination, seconds = context
        try:
            process.wait_finish(result)
            if process.get_successful():
                if self.get_transient_for():
                    parent = self.get_transient_for()
                    parent.reload_pressure_settings()
                    parent.calibration_stage = None
                    parent.calibrated_click = None
                    parent.calibrated_maximum = None
                self.calibration_stage = None
                pointer = ("pode ocultar após ficar parada" if seconds else "permanece visível sem movimento")
                self.status.set_text(f"Aplicado: {destination}; ponteiro {pointer}.")
            else:
                error = process.get_stderr_pipe().read_bytes(4096, None).get_data().decode().strip()
                self.status.set_text(f"Tela aplicada; ajuste do ponteiro não autorizado. {error}")
        except GLib.Error as error:
            self.status.set_text(f"Tela aplicada, mas o ponteiro não foi ajustado: {error.message}")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        window = self.props.active_window or MainWindow(self)
        window.present()


def check():
    settings = Gio.Settings.new_with_path(SCHEMA, SETTINGS_PATH)
    monitors = DisplayConfig().monitors()
    print(f"monitores={len(monitors)}")
    for monitor in monitors:
        print(monitor.label, monitor.output)
    print("output=", settings.get_strv("output"))
    print("keep-aspect=", settings.get_boolean("keep-aspect"))
    print("mapping=", settings.get_string("mapping"))


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        raise SystemExit(App().run(sys.argv))
