"""Perfis configuráveis para pressão, botões e gestos da caneta."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


INPUT_PROFILE_PATH = Path.home() / ".config" / "t1161-control" / "input-profiles.json"
ACTIVE_INPUT_PROFILE_PATH = Path.home() / ".config" / "t1161-control" / "active-input-profile"
RUNTIME_INPUT_PROFILE_PATH = Path("/dev/shm/t1161-active-input-profile")
RUNTIME_ACTIONS_PATH = Path("/dev/shm/t1161-control-actions")
CONTROL_ACTIONS_PATH = Path.home() / ".config" / "t1161-control" / "control-actions.json"
BUTTON_MAP_PATH = Path.home() / ".config" / "t1161-control" / "buttons.json"
DEFAULT_BUTTON_MAP = (8, 10, 12, 11, 9, 7, 5, 3, 4, 1, 2, 6)
RAW_BUTTON_BITS = tuple(range(10)) + (12, 13)

VALID_ACTIONS = {"none", "left", "right", "middle"}
VALID_GESTURES = {"none", "scroll-vertical", "scroll-horizontal"}
CONTROL_ACTIONS = {"none", "open-screen-profiles", "open-input-profiles", "previous", "next",
                   "confirm", "left", "middle", "right", "pen-scroll", "dual-touch",
                   "triple-touch", "mirror-zoom", "reverse-expand", "reverse-contract"}


def base_control_actions() -> dict[str, dict[str, str]]:
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


@dataclass
class InputProfile:
    profile_id: str
    name: str
    pressure: str = "left"
    pen_04: str = "right"
    pen_06: str = "middle"
    pen_04_pressure_move: str = "scroll-vertical"
    scroll_speed: float = 1.0
    pinned: bool = True
    control_actions: dict[str, dict[str, str]] = field(default_factory=base_control_actions)
    touch_spacing: int = 180
    direction_threshold: int = 20

    def normalize(self) -> None:
        if self.pressure not in VALID_ACTIONS:
            self.pressure = "none"
        if self.pen_04 not in VALID_ACTIONS:
            self.pen_04 = "none"
        if self.pen_06 not in VALID_ACTIONS:
            self.pen_06 = "none"
        if self.pen_04_pressure_move not in VALID_GESTURES:
            self.pen_04_pressure_move = "none"
        self.scroll_speed = max(0.1, min(8.0, float(self.scroll_speed)))
        defaults = base_control_actions()
        if isinstance(self.control_actions, dict):
            for control, fallback in defaults.items():
                value = self.control_actions.get(control, fallback)
                if isinstance(value, str):
                    value = {"normal": value, "menu": "none"}
                normal = value.get("normal", fallback["normal"])
                menu = value.get("menu", fallback["menu"])
                defaults[control] = {
                    "normal": normal if normal in CONTROL_ACTIONS else "none",
                    "menu": menu if menu in {"none", "previous", "next", "confirm"} else "none",
                }
        # A ponta e os botoes da caneta possuem campos proprios no perfil;
        # nao mantenha uma segunda configuracao divergente no mapa geral.
        defaults["tip"]["normal"] = self.pressure
        defaults["pen-04"]["normal"] = self.pen_04
        defaults["pen-06"]["normal"] = self.pen_06
        self.control_actions = defaults
        self.touch_spacing = max(10, min(500, int(self.touch_spacing)))
        self.direction_threshold = max(5, min(200, int(self.direction_threshold)))


def default_input_profiles() -> list[InputProfile]:
    standard = InputProfile("standard", "Padrão e menus")
    gestures = InputProfile("gestures", "Gestos multitoque", pen_04_pressure_move="scroll-horizontal")
    for button, action in ((6, "pen-scroll"), (7, "dual-touch"), (8, "triple-touch"),
                           (9, "mirror-zoom"), (10, "reverse-expand"),
                           (11, "reverse-contract"), (12, "middle")):
        gestures.control_actions[f"tablet-{button}"]["normal"] = action
    mouse = InputProfile("mouse", "Mouse e rolagem", pen_04_pressure_move="none")
    for button, action in ((6, "left"), (7, "right"), (8, "middle"), (9, "pen-scroll")):
        mouse.control_actions[f"tablet-{button}"]["normal"] = action
    creative = InputProfile("creative", "Criação, zoom e navegação")
    for button, action in ((6, "dual-touch"), (7, "triple-touch"), (8, "mirror-zoom"),
                           (9, "reverse-expand"), (10, "reverse-contract"),
                           (11, "pen-scroll"), (12, "right")):
        creative.control_actions[f"tablet-{button}"]["normal"] = action
    disabled = InputProfile("disabled", "Desativado", pressure="none", pen_04="none", pen_06="none",
                            pen_04_pressure_move="none")
    return [standard, gestures, mouse, creative, disabled]


def load_input_profiles(path: Path = INPUT_PROFILE_PATH) -> list[InputProfile]:
    try:
        profiles = [InputProfile(**item) for item in json.loads(path.read_text())]
        for profile in profiles:
            profile.normalize()
        if profiles:
            return profiles
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return default_input_profiles()


def save_input_profiles(profiles: list[InputProfile], path: Path = INPUT_PROFILE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(item) for item in profiles], indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def get_active_input_profile(path: Path = ACTIVE_INPUT_PROFILE_PATH) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return "standard"


def activate_input_profile(profile: InputProfile) -> None:
    profile.normalize()
    ACTIVE_INPUT_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_INPUT_PROFILE_PATH.write_text(profile.profile_id + "\n")
    temporary = RUNTIME_INPUT_PROFILE_PATH.with_suffix(".tmp")
    temporary.write_text(
        f"pressure {profile.pressure}\n"
        f"pen04 {profile.pen_04}\n"
        f"pen06 {profile.pen_06}\n"
        f"gesture04 {profile.pen_04_pressure_move}\n"
        f"scroll_speed {profile.scroll_speed:.3f}\n"
    )
    temporary.replace(RUNTIME_INPUT_PROFILE_PATH)
    try:
        button_map = tuple(json.loads(BUTTON_MAP_PATH.read_text()))
        if len(button_map) != 12:
            button_map = DEFAULT_BUTTON_MAP
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        button_map = DEFAULT_BUTTON_MAP
    lines = [f"touch-spacing {profile.touch_spacing}",
             f"direction-threshold {profile.direction_threshold}"]
    lines += [f"{control} {value['normal']}" for control, value in profile.control_actions.items()]
    for physical in range(1, 13):
        bit = RAW_BUTTON_BITS[button_map[physical - 1] - 1]
        value = profile.control_actions[f"tablet-{physical}"]
        lines.append(f"tablet-bit {bit} {value['normal']}")
        lines.append(f"tablet-menu-bit {bit} {value['menu']}")
    action_temp = RUNTIME_ACTIONS_PATH.with_suffix(".tmp")
    action_temp.write_text("\n".join(lines) + "\n")
    CONTROL_ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_temp = CONTROL_ACTIONS_PATH.with_suffix(".tmp")
    config_temp.write_text(json.dumps(profile.control_actions, ensure_ascii=False, indent=2) + "\n")
    config_temp.replace(CONTROL_ACTIONS_PATH)
    action_temp.replace(RUNTIME_ACTIONS_PATH)
