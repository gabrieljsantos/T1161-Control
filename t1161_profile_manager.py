#!/usr/bin/env python3
"""Biblioteca e editor visual de perfis da T1161."""

from __future__ import annotations

import math
import copy
import sys
import uuid
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk

from t1161_control import DriverResetButton, monitor_for_target, read_monitors, zone_runtime_issue
from t1161_input_profiles import (
    InputProfile,
    activate_input_profile,
    get_active_input_profile,
    load_input_profiles,
    save_input_profiles,
)

from t1161_profiles import (
    Profile,
    TABLET_HEIGHT_MM,
    TABLET_WIDTH_MM,
    ScreenTarget,
    Zone,
    find_free_zone_position,
    load_profiles,
    move_zone_without_overlap,
    resize_zone_without_overlap,
    save_profiles,
    set_zone_movement_rotation,
)


MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
SHORTCUT_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
SHORTCUT_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/t1161-actions/"
SHORTCUT_COMMAND = "gapplication action io.github.t1161.Actions toggle"


def configure_popup_shortcut(binding: str) -> None:
    media = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
    paths = list(media.get_strv("custom-keybindings"))
    if SHORTCUT_PATH not in paths:
        paths.append(SHORTCUT_PATH)
        media.set_strv("custom-keybindings", paths)
    shortcut = Gio.Settings.new_with_path(SHORTCUT_SCHEMA, SHORTCUT_PATH)
    shortcut.set_string("name", "Abrir ou fechar menu T1161")
    shortcut.set_string("command", SHORTCUT_COMMAND)
    shortcut.set_string("binding", binding)
    Gio.Settings.sync()


def load_popup_shortcut() -> str:
    try:
        return Gio.Settings.new_with_path(SHORTCUT_SCHEMA, SHORTCUT_PATH).get_string("binding")
    except Exception:
        return ""


class TabletCanvas(Gtk.DrawingArea):
    def __init__(self, changed):
        super().__init__()
        self.profile: Profile | None = None
        self.selected = 0
        self.drag_origin = None
        self.changed = changed
        self.set_content_width(620)
        self.set_content_height(390)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self.pressed)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.drag_begin)
        drag.connect("drag-update", self.drag_update)
        drag.connect("drag-end", self.drag_end)
        self.add_controller(drag)

    def set_profile(self, profile):
        self.profile = profile
        self.selected = 0
        self.queue_draw()

    @staticmethod
    def frame(width, height):
        margin = 34
        usable_w, usable_h = width - margin * 2, height - margin * 2
        tablet_w = min(usable_w, usable_h * 300 / 188)
        tablet_h = tablet_w * 188 / 300
        return (width - tablet_w) / 2, (height - tablet_h) / 2, tablet_w, tablet_h

    def draw(self, _area, ctx, width, height):
        x0, y0, tw, th = self.frame(width, height)
        ctx.set_source_rgb(0.12, 0.13, 0.15)
        ctx.paint()
        ctx.set_source_rgb(0.20, 0.22, 0.25)
        ctx.rectangle(x0, y0, tw, th)
        ctx.fill()
        ctx.set_source_rgb(0.55, 0.58, 0.63)
        ctx.set_line_width(2)
        ctx.rectangle(x0, y0, tw, th)
        ctx.stroke()
        if not self.profile:
            return
        ctx.set_source_rgb(0.75, 0.77, 0.80)
        for index in range(12):
            bx = x0 + 10 + index * min(28, (tw - 20) / 12)
            by = y0 + 5
            ctx.rectangle(bx, by, 18, 8)
            ctx.fill()
        for index, zone in enumerate(self.profile.zones):
            zx, zy = x0 + zone.x * tw, y0 + zone.y * th
            zw, zh = zone.width * tw, zone.height * th
            if index == self.selected:
                ctx.set_source_rgba(0.20, 0.65, 1.0, 0.42)
            else:
                ctx.set_source_rgba(0.45, 0.48, 0.52, 0.42)
            ctx.rectangle(zx, zy, zw, zh)
            ctx.fill()
            ctx.set_source_rgb(0.42, 0.78, 1.0) if index == self.selected else ctx.set_source_rgb(0.72, 0.74, 0.78)
            ctx.set_line_width(3 if index == self.selected else 1.5)
            ctx.rectangle(zx, zy, zw, zh)
            ctx.stroke()
            ctx.set_source_rgb(1, 1, 1)
            ctx.set_font_size(13)
            ctx.move_to(zx + 10, zy + 22)
            ctx.show_text(zone.name)
            ctx.save()
            ctx.translate(zx + zw / 2, zy + zh / 2)
            ctx.rotate(math.radians(zone.movement_rotation))
            ctx.set_source_rgba(1, 1, 1, 0.88)
            ctx.select_font_face("Sans", 0, 1)
            ctx.set_font_size(max(22, min(58, zw * 0.28, zh * 0.46)))
            extents = ctx.text_extents("A")
            ctx.move_to(-(extents.width / 2 + extents.x_bearing), -(extents.height / 2 + extents.y_bearing))
            ctx.show_text("A")
            ctx.restore()

    def zone_at(self, px, py):
        if not self.profile:
            return None
        x0, y0, tw, th = self.frame(self.get_width(), self.get_height())
        for index in range(len(self.profile.zones) - 1, -1, -1):
            zone = self.profile.zones[index]
            if x0 + zone.x * tw <= px <= x0 + (zone.x + zone.width) * tw and y0 + zone.y * th <= py <= y0 + (zone.y + zone.height) * th:
                return index
        return None

    def pressed(self, _gesture, _count, x, y):
        index = self.zone_at(x, y)
        if index is not None:
            self.selected = index
            self.changed(selection_only=True)
            self.queue_draw()

    def drag_begin(self, _gesture, x, y):
        index = self.zone_at(x, y)
        if index is not None and self.profile:
            self.selected = index
            zone = self.profile.zones[index]
            self.drag_origin = (zone.x, zone.y)
            self.changed(selection_only=True)

    def drag_update(self, _gesture, dx, dy):
        if self.drag_origin is None or not self.profile:
            return
        _x0, _y0, tw, th = self.frame(self.get_width(), self.get_height())
        zone = self.profile.zones[self.selected]
        move_zone_without_overlap(
            self.profile,
            self.selected,
            self.drag_origin[0] + dx / tw,
            self.drag_origin[1] + dy / th,
        )
        self.queue_draw()
        self.changed(selection_only=False)

    def drag_end(self, *_args):
        self.drag_origin = None


class ProfileManager(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Perfis — MT500–T1161 Linux Graphics Tablet Driver")
        self.set_default_size(1080, 860)
        self.profiles = load_profiles()
        try:
            self.monitors = read_monitors()
        except Exception:
            self.monitors = []
        self.profile_index = 0
        self.updating = False

        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Perfis de área e telas"))
        save = Gtk.Button(label="Salvar perfis")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.save)
        header.pack_end(save)
        header.pack_end(DriverResetButton())
        self.shortcut_button = Gtk.Button()
        self.shortcut_button.connect("clicked", self.capture_shortcut)
        header.pack_start(self.shortcut_button)
        input_profiles = Gtk.Button(label="Perfis de ações")
        input_profiles.connect("clicked", self.open_input_profiles)
        header.pack_start(input_profiles)
        self.update_shortcut_label()
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.set_child(root)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_size_request(260, -1)
        sidebar.set_margin_top(16); sidebar.set_margin_bottom(16)
        sidebar.set_margin_start(16); sidebar.set_margin_end(16)
        sidebar.append(Gtk.Label(label="Biblioteca de perfis", xalign=0, css_classes=["title-3"]))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self.profile_selected)
        self.listbox.set_vexpand(True)
        sidebar.append(self.listbox)
        new_profile = Gtk.Button(label="＋ Novo perfil")
        new_profile.connect("clicked", self.add_profile)
        sidebar.append(new_profile)
        duplicate_profile = Gtk.Button(label="Duplicar perfil")
        duplicate_profile.connect("clicked", self.duplicate_profile)
        sidebar.append(duplicate_profile)
        root.append(sidebar)

        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        editor.set_margin_top(16); editor.set_margin_bottom(16)
        editor.set_margin_start(18); editor.set_margin_end(18)
        root.append(editor)
        zone_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        zone_actions.append(Gtk.Label(label="Correspondências deste perfil", xalign=0, hexpand=True,
                                      css_classes=["title-3"]))
        add_zone = Gtk.Button(label="Adicionar tela à mesa")
        add_zone.connect("clicked", self.add_zone)
        zone_actions.append(add_zone)
        self.remove_zone_button = Gtk.Button(label="Remover tela")
        self.remove_zone_button.add_css_class("destructive-action")
        self.remove_zone_button.connect("clicked", self.remove_zone)
        zone_actions.append(self.remove_zone_button)
        editor.append(zone_actions)
        self.canvas = TabletCanvas(self.canvas_changed)
        editor.append(self.canvas)

        controls = Gtk.Grid(column_spacing=12, row_spacing=10)
        editor.append(controls)
        self.name = Gtk.Entry()
        self.name.connect("changed", self.fields_changed)
        controls.attach(Gtk.Label(label="Nome do perfil", xalign=0), 0, 0, 1, 1)
        controls.attach(self.name, 1, 0, 3, 1)
        self.zone_name = Gtk.Entry()
        self.zone_name.connect("changed", self.fields_changed)
        controls.attach(Gtk.Label(label="Região selecionada", xalign=0), 0, 1, 1, 1)
        controls.attach(self.zone_name, 1, 1, 1, 1)
        self.target_name = Gtk.Entry()
        self.target_name.connect("changed", self.fields_changed)
        controls.attach(Gtk.Label(label="Tela correspondente", xalign=0), 2, 1, 1, 1)
        controls.attach(self.target_name, 3, 1, 1, 1)
        self.target_orientation = Gtk.Label(xalign=0)
        self.target_orientation.add_css_class("dim-label")
        controls.attach(self.target_orientation, 2, 2, 2, 1)
        self.capture_width = Gtk.SpinButton.new_with_range(8, 100, 1)
        self.capture_width.connect("value-changed", lambda widget: self.dimension_changed("width"))
        self.width_label = Gtk.Label(label="Largura da captura (%)", xalign=0)
        controls.attach(self.width_label, 0, 2, 1, 1)
        controls.attach(self.capture_width, 1, 2, 1, 1)
        self.capture_height = Gtk.SpinButton.new_with_range(8, 100, 1)
        self.capture_height.connect("value-changed", lambda widget: self.dimension_changed("height"))
        self.height_label = Gtk.Label(label="Altura da captura (%)", xalign=0)
        controls.attach(self.height_label, 0, 3, 1, 1)
        controls.attach(self.capture_height, 1, 3, 1, 1)
        self.movement_rotation = Gtk.DropDown.new_from_strings(["0°", "90°", "180°", "270°"])
        self.movement_rotation.connect("notify::selected", self.fields_changed)
        controls.attach(Gtk.Label(label="Ângulo do movimento", xalign=0), 0, 4, 1, 1)
        controls.attach(self.movement_rotation, 1, 4, 1, 1)
        self.geometry = Gtk.Label(xalign=0)
        self.geometry.add_css_class("dim-label")
        controls.attach(self.geometry, 2, 4, 2, 1)
        self.unit_mode = Gtk.DropDown.new_from_strings(["Percentual da mesa", "Milímetros"])
        self.unit_mode.connect("notify::selected", self.unit_changed)
        controls.attach(Gtk.Label(label="Unidade", xalign=0), 0, 5, 1, 1)
        controls.attach(self.unit_mode, 1, 5, 1, 1)
        self.aspect_lock = Gtk.CheckButton(label="Travar proporção da tela")
        self.aspect_lock.connect("toggled", self.fields_changed)
        controls.attach(self.aspect_lock, 2, 5, 2, 1)

        self.monitor_picker = Gtk.DropDown.new_from_strings(
            ["Manter identificação atual"] + [monitor.label for monitor in self.monitors]
        )
        self.monitor_picker.connect("notify::selected", self.monitor_selected)
        controls.attach(Gtk.Label(label="Monitor detectado", xalign=0), 0, 6, 1, 1)
        controls.attach(self.monitor_picker, 1, 6, 3, 1)

        self.source_x = Gtk.SpinButton.new_with_range(0, 32768, 1)
        self.source_y = Gtk.SpinButton.new_with_range(0, 32768, 1)
        self.source_width = Gtk.SpinButton.new_with_range(0, 32768, 1)
        self.source_height = Gtk.SpinButton.new_with_range(0, 32768, 1)
        for widget in (self.source_x, self.source_y, self.source_width, self.source_height):
            widget.connect("value-changed", self.fields_changed)
        source_origin = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        source_origin.append(Gtk.Label(label="X")); source_origin.append(self.source_x)
        source_origin.append(Gtk.Label(label="Y")); source_origin.append(self.source_y)
        controls.attach(Gtk.Label(label="Origem na tela (px)", xalign=0), 0, 7, 1, 1)
        controls.attach(source_origin, 1, 7, 3, 1)
        source_size = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        source_size.append(Gtk.Label(label="Largura")); source_size.append(self.source_width)
        source_size.append(Gtk.Label(label="Altura")); source_size.append(self.source_height)
        controls.attach(Gtk.Label(label="Recorte da tela (px)", xalign=0), 0, 8, 1, 1)
        controls.attach(source_size, 1, 8, 3, 1)
        self.aspect_mode = Gtk.DropDown.new_from_strings(
            ["Proporção da tela/recorte", "16:9", "16:10", "4:3", "21:9", "Personalizada", "Livre"]
        )
        self.aspect_mode.connect("notify::selected", self.aspect_changed)
        controls.attach(Gtk.Label(label="Proporção", xalign=0), 0, 9, 1, 1)
        controls.attach(self.aspect_mode, 1, 9, 1, 1)
        custom_ratio = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.aspect_width = Gtk.SpinButton.new_with_range(1, 100, 0.1)
        self.aspect_height = Gtk.SpinButton.new_with_range(1, 100, 0.1)
        self.aspect_width.connect("value-changed", self.aspect_value_changed)
        self.aspect_height.connect("value-changed", self.aspect_value_changed)
        custom_ratio.append(self.aspect_width); custom_ratio.append(Gtk.Label(label=":")); custom_ratio.append(self.aspect_height)
        controls.attach(custom_ratio, 2, 9, 2, 1)
        self.fit_mode = Gtk.DropDown.new_from_strings(
            ["Tamanho manual", "Ajustar à largura máxima", "Ajustar à altura máxima", "Caber nos dois limites"]
        )
        self.fit_mode.connect("notify::selected", self.fit_changed)
        controls.attach(Gtk.Label(label="Dimensionamento", xalign=0), 0, 10, 1, 1)
        controls.attach(self.fit_mode, 1, 10, 3, 1)
        self.status = Gtk.Label(xalign=0, wrap=True)
        editor.append(self.status)
        self.rebuild_list()
        self.listbox.select_row(self.listbox.get_row_at_index(0))

    @property
    def profile(self):
        return self.profiles[self.profile_index]

    @property
    def zone(self):
        return self.profile.zones[self.canvas.selected]

    def rebuild_list(self):
        while row := self.listbox.get_row_at_index(0):
            self.listbox.remove(row)
        for profile in self.profiles:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(10); box.set_margin_bottom(10)
            box.set_margin_start(10); box.set_margin_end(10)
            box.append(Gtk.Label(label=profile.name, xalign=0))
            engine = "Pronto para aplicar" if profile.supported_by_current_engine else "Requer motor multizona"
            label = Gtk.Label(label=engine, xalign=0)
            label.add_css_class("dim-label")
            box.append(label)
            row.set_child(box)
            self.listbox.append(row)

    def profile_selected(self, _listbox, row):
        if row is None:
            return
        self.profile_index = row.get_index()
        self.canvas.set_profile(self.profile)
        self.load_fields()

    def load_fields(self):
        self.updating = True
        self.name.set_text(self.profile.name)
        if self.profile.zones:
            self.zone_name.set_text(self.zone.name)
            self.target_name.set_text(self.zone.target.name)
            self.capture_width.set_value(
                self.zone.width * (TABLET_WIDTH_MM if self.zone.unit_mode == "mm" else 100)
            )
            self.capture_height.set_value(
                self.zone.height * (TABLET_HEIGHT_MM if self.zone.unit_mode == "mm" else 100)
            )
            self.unit_mode.set_selected(1 if self.zone.unit_mode == "mm" else 0)
            self.aspect_lock.set_active(self.zone.aspect_locked)
            self.source_x.set_value(self.zone.source_x)
            self.source_y.set_value(self.zone.source_y)
            self.source_width.set_value(self.zone.source_width)
            self.source_height.set_value(self.zone.source_height)
            aspect_indexes = {"screen": 0, "16:9": 1, "16:10": 2, "4:3": 3, "21:9": 4,
                              "custom": 5, "free": 6}
            self.aspect_mode.set_selected(aspect_indexes.get(self.zone.aspect_mode, 0))
            self.aspect_width.set_value(self.zone.aspect_width)
            self.aspect_height.set_value(self.zone.aspect_height)
            fit_indexes = {"manual": 0, "width": 1, "height": 2, "bounds": 3}
            self.fit_mode.set_selected(fit_indexes.get(self.zone.fit_mode, 0))
            self.movement_rotation.set_selected((self.zone.movement_rotation // 90) % 4)
            orientation = {
                0: "Tela detectada em paisagem pelo GNOME",
                90: "Tela detectada em retrato à direita pelo GNOME",
                180: "Tela detectada invertida pelo GNOME",
                270: "Tela detectada em retrato à esquerda pelo GNOME",
            }.get(self.zone.target.logical_rotation, "Orientação lógica personalizada")
            self.target_orientation.set_text(orientation)
            self.geometry.set_text(
                f"x {self.zone.x:.3f} · y {self.zone.y:.3f} · "
                f"{self.zone.width * TABLET_WIDTH_MM:.0f} × "
                f"{self.zone.height * TABLET_HEIGHT_MM:.0f} mm"
            )
        self.updating = False
        self._configure_dimension_inputs()
        self.update_status()
        self.remove_zone_button.set_sensitive(len(self.profile.zones) > 1)

    def update_status(self, preferred: str = ""):
        if preferred:
            self.status.set_text(preferred)
            return
        problems = []
        for zone in self.profile.zones:
            issue = zone_runtime_issue(zone, monitor_for_target(zone.target, self.monitors))
            if issue:
                problems.append(f"{zone.name}: {issue}.")
        if problems:
            self.status.set_text(" ".join(problems))
        elif self.profile.supported_by_current_engine:
            self.status.set_text("Perfil válido e pronto para aplicar.")
        else:
            self.status.set_text("Adicione pelo menos uma correspondência válida.")

    def fields_changed(self, *_args):
        if self.updating or not self.profile.zones:
            return
        self.profile.name = self.name.get_text().strip() or self.profile.name
        self.profile.tablet_rotation = 0
        self.zone.name = self.zone_name.get_text().strip() or self.zone.name
        self.zone.target.name = self.target_name.get_text().strip() or self.zone.target.name
        self.zone.aspect_locked = self.aspect_lock.get_active()
        self.zone.source_x = self.source_x.get_value_as_int()
        self.zone.source_y = self.source_y.get_value_as_int()
        self.zone.source_width = self.source_width.get_value_as_int()
        self.zone.source_height = self.source_height.get_value_as_int()
        requested_rotation = self.movement_rotation.get_selected() * 90
        if requested_rotation != self.zone.movement_rotation:
            accepted = set_zone_movement_rotation(
                self.profile, self.canvas.selected, requested_rotation
            )
            self.updating = True
            if not accepted:
                self.movement_rotation.set_selected((self.zone.movement_rotation // 90) % 4)
                self.status.set_text(
                    "A rotação foi bloqueada porque a área girada sobreporia outra tela."
                )
            self.capture_width.set_value(self.zone.width * 100)
            self.capture_height.set_value(self.zone.height * 100)
            self.updating = False
        width_value = self.capture_width.get_value()
        height_value = self.capture_height.get_value()
        if self.zone.unit_mode == "mm":
            requested_width = width_value / TABLET_WIDTH_MM
            requested_height = height_value / TABLET_HEIGHT_MM
        else:
            requested_width = width_value / 100
            requested_height = height_value / 100
        width, height = resize_zone_without_overlap(
            self.profile,
            self.canvas.selected,
            requested_width,
            requested_height,
        )
        shown_width = width * (TABLET_WIDTH_MM if self.zone.unit_mode == "mm" else 100)
        shown_height = height * (TABLET_HEIGHT_MM if self.zone.unit_mode == "mm" else 100)
        if abs(shown_width - self.capture_width.get_value()) > 0.01 or abs(shown_height - self.capture_height.get_value()) > 0.01:
            self.updating = True
            self.capture_width.set_value(shown_width)
            self.capture_height.set_value(shown_height)
            self.updating = False
        self.canvas.queue_draw()
        self.update_status()

    def _configure_dimension_inputs(self):
        millimeters = self.zone.unit_mode == "mm"
        self.width_label.set_text("Largura da captura (mm)" if millimeters else "Largura da captura (%)")
        self.height_label.set_text("Altura da captura (mm)" if millimeters else "Altura da captura (%)")
        self.capture_width.set_range(8, TABLET_WIDTH_MM if millimeters else 100)
        self.capture_height.set_range(8, TABLET_HEIGHT_MM if millimeters else 100)
        self.updating = True
        self.capture_width.set_value(self.zone.width * (TABLET_WIDTH_MM if millimeters else 100))
        self.capture_height.set_value(self.zone.height * (TABLET_HEIGHT_MM if millimeters else 100))
        self.updating = False

    def unit_changed(self, *_args):
        if self.updating:
            return
        self.zone.unit_mode = "mm" if self.unit_mode.get_selected() == 1 else "percent"
        self._configure_dimension_inputs()

    def dimension_changed(self, dimension):
        if self.updating:
            return
        self.zone.dominant_dimension = dimension
        if self.aspect_lock.get_active():
            ratio = self.aspect_ratio()
            self.updating = True
            if self.zone.unit_mode == "mm":
                if dimension == "width":
                    self.capture_height.set_value(self.capture_width.get_value() / ratio)
                else:
                    self.capture_width.set_value(self.capture_height.get_value() * ratio)
            else:
                width_mm = self.capture_width.get_value() / 100 * TABLET_WIDTH_MM
                height_mm = self.capture_height.get_value() / 100 * TABLET_HEIGHT_MM
                if dimension == "width":
                    self.capture_height.set_value(width_mm / ratio / TABLET_HEIGHT_MM * 100)
                else:
                    self.capture_width.set_value(height_mm * ratio / TABLET_WIDTH_MM * 100)
            self.updating = False
        self.fields_changed()

    def monitor_selected(self, dropdown, _property):
        if self.updating or dropdown.get_selected() == 0:
            return
        monitor = self.monitors[dropdown.get_selected() - 1]
        self.zone.target.name = monitor.label
        self.zone.target.vendor = monitor.vendor
        self.zone.target.product = monitor.product
        self.zone.target.serial = monitor.serial
        self.zone.target.connector = monitor.connector
        self.zone.target.logical_width = monitor.logical_width
        self.zone.target.logical_height = monitor.logical_height
        self.target_name.set_text(monitor.label)
        self.source_x.set_value(0); self.source_y.set_value(0)
        self.source_width.set_value(monitor.logical_width)
        self.source_height.set_value(monitor.logical_height)

    def aspect_ratio(self):
        modes = {
            1: (16, 9), 2: (16, 10), 3: (4, 3), 4: (21, 9),
            5: (self.aspect_width.get_value(), self.aspect_height.get_value()),
        }
        if self.aspect_mode.get_selected() == 0:
            width = self.source_width.get_value() or self.zone.target.logical_width or 16
            height = self.source_height.get_value() or self.zone.target.logical_height or 9
        else:
            width, height = modes.get(self.aspect_mode.get_selected(), (1, 1))
        ratio = width / max(0.001, height)
        return 1 / ratio if self.zone.movement_rotation in (90, 270) else ratio

    def aspect_changed(self, dropdown, _property):
        if self.updating:
            return
        modes = ["screen", "16:9", "16:10", "4:3", "21:9", "custom", "free"]
        self.zone.aspect_mode = modes[dropdown.get_selected()]
        self.aspect_lock.set_active(self.zone.aspect_mode != "free")
        if self.zone.aspect_mode != "free":
            self.dimension_changed(self.zone.dominant_dimension)

    def aspect_value_changed(self, _widget):
        if self.updating:
            return
        self.zone.aspect_width = self.aspect_width.get_value()
        self.zone.aspect_height = self.aspect_height.get_value()
        if self.zone.aspect_mode == "custom" and self.aspect_lock.get_active():
            self.dimension_changed(self.zone.dominant_dimension)

    def fit_changed(self, dropdown, _property):
        if self.updating:
            return
        modes = ["manual", "width", "height", "bounds"]
        self.zone.fit_mode = modes[dropdown.get_selected()]
        if self.zone.fit_mode == "manual":
            return
        ratio = self.aspect_ratio()
        max_width = 1.0 - self.zone.x
        max_height = 1.0 - self.zone.y
        if self.zone.fit_mode == "width":
            width = max_width
            height = width * TABLET_WIDTH_MM / ratio / TABLET_HEIGHT_MM
        elif self.zone.fit_mode == "height":
            height = max_height
            width = height * TABLET_HEIGHT_MM * ratio / TABLET_WIDTH_MM
        else:
            width = max_width
            height = width * TABLET_WIDTH_MM / ratio / TABLET_HEIGHT_MM
            if height > max_height:
                height = max_height
                width = height * TABLET_HEIGHT_MM * ratio / TABLET_WIDTH_MM
        requested = (width, height)
        applied = resize_zone_without_overlap(self.profile, self.canvas.selected, width, height)
        self._configure_dimension_inputs()
        self.canvas.queue_draw()
        if any(abs(left - right) > 0.0001 for left, right in zip(requested, applied)):
            self.update_status(
                "O ajuste foi bloqueado porque invadiria outra região. Mova a região ou escolha outro limite."
            )
        else:
            self.update_status()

    def canvas_changed(self, selection_only=False):
        self.load_fields()
        if not selection_only:
            self.fields_changed()

    def save(self, _button):
        save_profiles(self.profiles)
        self.rebuild_list()
        self.listbox.select_row(self.listbox.get_row_at_index(self.profile_index))
        self.status.set_text("Perfis salvos. O popup recarregará a biblioteca na próxima abertura.")

    def update_shortcut_label(self):
        binding = load_popup_shortcut()
        if binding:
            keyval, modifiers = Gtk.accelerator_parse(binding)
            label = Gtk.accelerator_get_label(keyval, modifiers)
            self.shortcut_button.set_label(f"Atalho do popup: {label}")
        else:
            self.shortcut_button.set_label("Definir atalho do popup")

    def open_input_profiles(self, _button):
        window = InputProfileManager(self.get_application())
        window.set_transient_for(self)
        window.present()

    def capture_shortcut(self, _button):
        dialog = Gtk.Dialog(title="Atalho global do popup", transient_for=self, modal=True)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        box = dialog.get_content_area()
        box.set_spacing(12)
        box.set_margin_top(24); box.set_margin_bottom(24)
        box.set_margin_start(24); box.set_margin_end(24)
        box.append(Gtk.Label(
            label=("Pressione uma combinação ou uma tecla funcional. "
                   "Backspace remove o atalho."),
            wrap=True,
        ))
        keys = Gtk.EventControllerKey()

        def captured(_controller, keyval, _keycode, state):
            if keyval == Gdk.KEY_Escape:
                dialog.destroy()
                return True
            if keyval == Gdk.KEY_BackSpace:
                configure_popup_shortcut("")
                dialog.destroy()
                self.update_shortcut_label()
                return True
            modifiers = state & Gtk.accelerator_get_default_mod_mask()
            binding = Gtk.accelerator_name(keyval, modifiers)
            configure_popup_shortcut(binding)
            dialog.destroy()
            self.update_shortcut_label()
            return True

        keys.connect("key-pressed", captured)
        dialog.add_controller(keys)
        dialog.connect("response", lambda window, _response: window.destroy())
        dialog.present()

    def add_zone(self, _button):
        width, height = 0.34, 0.34
        x, y = find_free_zone_position(self.profile, width, height)
        number = len(self.profile.zones) + 1
        self.profile.zones.append(
            Zone(
                f"Região {number}", x, y, width, height,
                target=ScreenTarget(f"Tela {number}"),
            )
        )
        self.canvas.selected = len(self.profile.zones) - 1
        self.canvas.queue_draw()
        self.load_fields()

    def add_profile(self, _button):
        profile = Profile(
            profile_id=f"profile-{uuid.uuid4().hex[:8]}",
            name=f"Novo perfil {len(self.profiles) + 1}",
            zones=[Zone("Tela 1", 0.0, 0.0, 0.5, 0.5, target=ScreenTarget("Tela principal"))],
        )
        self.profiles.append(profile)
        self.rebuild_list()
        self.listbox.select_row(self.listbox.get_row_at_index(len(self.profiles) - 1))

    def duplicate_profile(self, _button):
        self.fields_changed()
        duplicate = copy.deepcopy(self.profile)
        duplicate.profile_id = f"profile-{uuid.uuid4().hex[:8]}"
        duplicate.name = f"{self.profile.name} — cópia"
        self.profiles.append(duplicate)
        self.rebuild_list()
        self.listbox.select_row(self.listbox.get_row_at_index(len(self.profiles) - 1))

    def remove_zone(self, _button):
        if len(self.profile.zones) <= 1:
            return
        self.profile.zones.pop(self.canvas.selected)
        self.canvas.selected = min(self.canvas.selected, len(self.profile.zones) - 1)
        self.canvas.queue_draw()
        self.load_fields()


class InputProfileManager(Gtk.ApplicationWindow):
    ACTIONS = ["none", "left", "right", "middle"]
    ACTION_LABELS = ["Não fazer nada", "Clique esquerdo", "Clique direito", "Clique do meio/scroll"]
    GESTURES = ["none", "scroll-vertical", "scroll-horizontal"]
    GESTURE_LABELS = ["Não fazer nada", "Rolagem vertical proporcional", "Rolagem horizontal proporcional"]
    CONTROL_ACTIONS = ["none", "open-screen-profiles", "open-input-profiles", "left", "middle",
                       "right", "pen-scroll", "dual-touch", "triple-touch", "mirror-zoom",
                       "reverse-expand", "reverse-contract"]
    CONTROL_LABELS = ["Nenhuma ação", "Abrir perfis de tela", "Abrir perfis de ações",
                      "Clique esquerdo", "Segurar botão do meio", "Clique direito",
                      "Rolagem pela caneta", "Dois contatos", "Três contatos", "Zoom espelhado",
                      "Espelhamento — expandir", "Espelhamento — contrair"]
    MENU_ACTIONS = ["none", "previous", "next", "confirm"]
    MENU_LABELS = ["Usar ação normal", "Anterior", "Próximo", "Confirmar"]

    def __init__(self, app):
        super().__init__(application=app, title="Editor de perfis de ações")
        self.set_default_size(1040, 760)
        self.profiles = load_input_profiles()
        self.index = 0
        self.updating = False
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Editor de perfis de ações"))
        save = Gtk.Button(label="Salvar perfis")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.save)
        header.pack_end(save)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        root.set_margin_top(18); root.set_margin_bottom(18)
        root.set_margin_start(18); root.set_margin_end(18)
        self.set_child(root)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.set_size_request(235, -1)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.set_vexpand(True)
        self.listbox.connect("row-selected", self.selected)
        side.append(self.listbox)
        add = Gtk.Button(label="＋ Novo perfil de entrada")
        add.connect("clicked", self.add)
        side.append(add)
        duplicate = Gtk.Button(label="Duplicar perfil")
        duplicate.connect("clicked", self.duplicate)
        side.append(duplicate)
        root.append(side)

        form = Gtk.Grid(column_spacing=14, row_spacing=14, hexpand=True)
        form_scroll = Gtk.ScrolledWindow()
        form_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroll.set_hexpand(True); form_scroll.set_vexpand(True)
        form_scroll.set_child(form)
        root.append(form_scroll)
        self.name = Gtk.Entry()
        self.name.connect("changed", self.changed)
        form.attach(Gtk.Label(label="Nome", xalign=0), 0, 0, 1, 1)
        form.attach(self.name, 1, 0, 1, 1)
        self.pressure = self._dropdown(self.ACTION_LABELS, self.changed)
        self.pen04 = self._dropdown(self.ACTION_LABELS, self.changed)
        self.pen06 = self._dropdown(self.ACTION_LABELS, self.changed)
        self.gesture = self._dropdown(self.GESTURE_LABELS, self.changed)
        fields = [
            ("Pressionar a ponta", self.pressure),
            ("Botão 0x04", self.pen04),
            ("Botão 0x06", self.pen06),
            ("0x04 + pressão + movimento", self.gesture),
        ]
        for row, (label, widget) in enumerate(fields, 1):
            form.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            form.attach(widget, 1, row, 1, 1)
        self.speed = Gtk.SpinButton.new_with_range(0.1, 8.0, 0.1)
        self.speed.connect("value-changed", self.changed)
        form.attach(Gtk.Label(label="Velocidade proporcional", xalign=0), 0, 5, 1, 1)
        form.attach(self.speed, 1, 5, 1, 1)
        note = Gtk.Label(
            label=("Durante a rolagem, o clique e o traço ficam suspensos. "
                   "Mover para cima/baixo controla a rolagem vertical; "
                   "mover para os lados controla a horizontal."),
            xalign=0, wrap=True,
        )
        note.add_css_class("dim-label")
        form.attach(note, 0, 6, 2, 1)
        self.status = Gtk.Label(xalign=0)
        form.attach(self.status, 0, 7, 2, 1)
        heading = Gtk.Label(label="Ações completas deste perfil", xalign=0)
        heading.add_css_class("title-3")
        form.attach(heading, 0, 8, 3, 1)
        form.attach(Gtk.Label(label="Controle", xalign=0), 0, 9, 1, 1)
        form.attach(Gtk.Label(label="Fora do menu", xalign=0), 1, 9, 1, 1)
        form.attach(Gtk.Label(label="Com menu aberto", xalign=0), 2, 9, 1, 1)
        self.control_dropdowns = {}
        self.menu_dropdowns = {}
        controls = [(f"tablet-{i}", f"Botão físico {i}") for i in range(1, 13)]
        for row, (control, label) in enumerate(controls, 10):
            form.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            normal = self._dropdown(self.CONTROL_LABELS, self.changed)
            menu = self._dropdown(self.MENU_LABELS, self.changed)
            menu.set_sensitive(control.startswith("tablet-"))
            form.attach(normal, 1, row, 1, 1); form.attach(menu, 2, row, 1, 1)
            self.control_dropdowns[control] = normal
            self.menu_dropdowns[control] = menu
        settings_row = 25
        self.touch_spacing = Gtk.SpinButton.new_with_range(10, 500, 5)
        self.touch_spacing.connect("value-changed", self.changed)
        self.direction_threshold = Gtk.SpinButton.new_with_range(5, 200, 5)
        self.direction_threshold.connect("value-changed", self.changed)
        form.attach(Gtk.Label(label="Distância entre contatos", xalign=0), 0, settings_row, 1, 1)
        form.attach(self.touch_spacing, 1, settings_row, 1, 1)
        form.attach(Gtk.Label(label="Limiar de direção", xalign=0), 0, settings_row + 1, 1, 1)
        form.attach(self.direction_threshold, 1, settings_row + 1, 1, 1)
        self.rebuild()
        self.listbox.select_row(self.listbox.get_row_at_index(0))

    @staticmethod
    def _dropdown(labels, callback):
        widget = Gtk.DropDown.new_from_strings(labels)
        widget.connect("notify::selected", callback)
        return widget

    def rebuild(self):
        while row := self.listbox.get_row_at_index(0):
            self.listbox.remove(row)
        for profile in self.profiles:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=profile.name, xalign=0,
                                    margin_top=10, margin_bottom=10,
                                    margin_start=10, margin_end=10))
            self.listbox.append(row)

    def selected(self, _listbox, row):
        if row is None:
            return
        self.index = row.get_index()
        profile = self.profiles[self.index]
        self.updating = True
        self.name.set_text(profile.name)
        self.pressure.set_selected(self.ACTIONS.index(profile.pressure))
        self.pen04.set_selected(self.ACTIONS.index(profile.pen_04))
        self.pen06.set_selected(self.ACTIONS.index(profile.pen_06))
        self.gesture.set_selected(self.GESTURES.index(profile.pen_04_pressure_move))
        self.speed.set_value(profile.scroll_speed)
        for control, dropdown in self.control_dropdowns.items():
            value = profile.control_actions[control]
            dropdown.set_selected(self.CONTROL_ACTIONS.index(value["normal"]))
            self.menu_dropdowns[control].set_selected(self.MENU_ACTIONS.index(value["menu"]))
        self.touch_spacing.set_value(profile.touch_spacing)
        self.direction_threshold.set_value(profile.direction_threshold)
        self.updating = False

    def changed(self, *_args):
        if self.updating or not self.profiles:
            return
        profile = self.profiles[self.index]
        profile.name = self.name.get_text().strip() or profile.name
        profile.pressure = self.ACTIONS[self.pressure.get_selected()]
        profile.pen_04 = self.ACTIONS[self.pen04.get_selected()]
        profile.pen_06 = self.ACTIONS[self.pen06.get_selected()]
        profile.pen_04_pressure_move = self.GESTURES[self.gesture.get_selected()]
        profile.scroll_speed = self.speed.get_value()
        for control, dropdown in self.control_dropdowns.items():
            profile.control_actions[control] = {
                "normal": self.CONTROL_ACTIONS[dropdown.get_selected()],
                "menu": self.MENU_ACTIONS[self.menu_dropdowns[control].get_selected()],
            }
        profile.touch_spacing = self.touch_spacing.get_value_as_int()
        profile.direction_threshold = self.direction_threshold.get_value_as_int()

    def add(self, _button):
        self.profiles.append(InputProfile(
            f"input-{uuid.uuid4().hex[:8]}",
            f"Novo perfil {len(self.profiles) + 1}",
        ))
        self.rebuild()
        self.listbox.select_row(self.listbox.get_row_at_index(len(self.profiles) - 1))

    def duplicate(self, _button):
        self.changed()
        duplicate = copy.deepcopy(self.profiles[self.index])
        duplicate.profile_id = f"input-{uuid.uuid4().hex[:8]}"
        duplicate.name = f"{duplicate.name} — cópia"
        self.profiles.append(duplicate)
        self.rebuild()
        self.listbox.select_row(self.listbox.get_row_at_index(len(self.profiles) - 1))

    def save(self, _button):
        self.changed()
        save_input_profiles(self.profiles)
        active_id = get_active_input_profile()
        active_profile = next(
            (profile for profile in self.profiles if profile.profile_id == active_id),
            None,
        )
        if active_profile is not None:
            activate_input_profile(active_profile)
        self.rebuild()
        self.listbox.select_row(self.listbox.get_row_at_index(self.index))
        self.status.set_text("Perfis salvos; o perfil ativo e o popup foram atualizados.")


class ProfileApp(Gtk.Application):
    def __init__(self):
        self.input_only = "--input-profiles" in sys.argv
        application_id = (
            "io.github.t1161.InputProfileManager"
            if self.input_only else "io.github.t1161.ProfileManager"
        )
        super().__init__(application_id=application_id)

    def do_activate(self):
        window_type = InputProfileManager if self.input_only else ProfileManager
        (self.props.active_window or window_type(self)).present()


if __name__ == "__main__":
    raise SystemExit(ProfileApp().run([arg for arg in sys.argv if arg != "--input-profiles"]))
