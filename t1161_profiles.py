"""Modelo persistente de perfis de área da T1161."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


PROFILE_PATH = Path.home() / ".config" / "t1161-control" / "profiles.json"
ACTIVE_PROFILE_PATH = Path.home() / ".config" / "t1161-control" / "active-profile"
TABLET_WIDTH_MM = 300.0
TABLET_HEIGHT_MM = 188.0
PC_CAPTURE_MM = (254.0, 152.0)
WACOM_ONE_CAPTURE_MM = (152.0, 95.0)


@dataclass
class ScreenTarget:
    name: str
    vendor: str = ""
    product: str = ""
    serial: str = ""
    use_logical_rotation: bool = True
    logical_rotation: int = 0
    connector: str = ""
    logical_width: int = 0
    logical_height: int = 0

    @property
    def output(self) -> list[str]:
        return [self.vendor, self.product, self.serial]


@dataclass
class Zone:
    name: str
    x: float
    y: float
    width: float
    height: float
    movement_rotation: int = 0
    target: ScreenTarget = field(default_factory=lambda: ScreenTarget("Tela principal"))
    preserve_aspect: bool = True
    source_x: int = 0
    source_y: int = 0
    source_width: int = 0
    source_height: int = 0
    aspect_mode: str = "screen"
    aspect_width: float = 16.0
    aspect_height: float = 9.0
    aspect_locked: bool = True
    dominant_dimension: str = "width"
    fit_mode: str = "manual"
    allow_distortion: bool = False
    unit_mode: str = "percent"

    def clamp(self) -> None:
        self.width = max(0.08, min(1.0, self.width))
        self.height = max(0.08, min(1.0, self.height))
        self.x = max(0.0, min(1.0 - self.width, self.x))
        self.y = max(0.0, min(1.0 - self.height, self.y))
        self.movement_rotation %= 360

    @property
    def dead_area(self) -> list[float]:
        return [self.x, 1.0 - self.x - self.width, self.y, 1.0 - self.y - self.height]


@dataclass
class Profile:
    profile_id: str
    name: str
    tablet_rotation: int = 0
    zones: list[Zone] = field(default_factory=list)
    pinned: bool = True
    missing_screen: str = "preserve"

    @property
    def supported_by_current_engine(self) -> bool:
        return bool(self.zones)


def default_profiles() -> list[Profile]:
    embedded = ScreenTarget(
        "Tela principal", "BOE", "0x0b02", "0x00000000",
        connector="eDP-1", logical_width=1920, logical_height=1080,
    )
    portrait = ScreenTarget(
        "Retrato direita", "WFK", "Monitor", "demoset-1",
        logical_rotation=90, connector="DP-1", logical_width=1080, logical_height=1920,
    )
    # Área Wacom One confortável dentro da área confirmada da T1161.
    w, h = WACOM_ONE_CAPTURE_MM[0] / TABLET_WIDTH_MM, WACOM_ONE_CAPTURE_MM[1] / TABLET_HEIGHT_MM
    return [
        Profile("full", "Mesa completa", zones=[Zone("Área completa", 0, 0, 1, 1, target=embedded)]),
        Profile(
            "dual-wacom-one", "Duas telas",
            zones=[
                Zone("Wacom One esquerda", 0, 0, w, h, target=embedded),
                Zone("Wacom One retrato direita", 1 - h * 188 / 300, 0, h * 188 / 300, w * 300 / 188,
                     target=portrait),
            ],
        ),
        Profile(
            "wacom-one-right", "Wacom One direita",
            zones=[Zone("Wacom One direita", 1 - w, 0, w, h, target=embedded)],
        ),
        Profile(
            "rotated-near-buttons", "Região inferior",
            tablet_rotation=0,
            zones=[Zone("Região junto aos botões", (1 - w) / 2, 1 - h, w, h, target=embedded)],
        ),
    ]


def _target(data: dict) -> ScreenTarget:
    values = dict(data)
    # Migração dos perfis criados antes de a orientação lógica ser persistida.
    if "logical_rotation" not in values:
        values["logical_rotation"] = 90 if values.get("serial") == "demoset-1" else 0
    return ScreenTarget(**values)


def _zone(data: dict) -> Zone:
    values = dict(data)
    # `rotation` era usado por versões que misturavam rotação física e lógica.
    # Não o reutilizamos: o novo ângulo de movimento nasce em zero.
    values.pop("rotation", None)
    values.setdefault("movement_rotation", 0)
    values["target"] = _target(values.get("target", {"name": "Tela principal"}))
    zone = Zone(**values)
    zone.clamp()
    return zone


def load_profiles(path: Path = PROFILE_PATH) -> list[Profile]:
    try:
        result = []
        for item in json.loads(path.read_text()):
            values = dict(item)
            values["zones"] = [_zone(zone) for zone in values.get("zones", [])]
            # Rotação do input foi removida: o Fedora já fornece a orientação
            # lógica final das telas. Perfis antigos são normalizados.
            values["tablet_rotation"] = 0
            if values.get("profile_id") == "rotated-near-buttons" and values.get("name") == "Mesa girada":
                values["name"] = "Região inferior"
            result.append(Profile(**values))
        if result:
            return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return default_profiles()


def save_profiles(profiles: list[Profile], path: Path = PROFILE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(profile) for profile in profiles], indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def set_active_profile(profile_id: str, path: Path = ACTIVE_PROFILE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile_id + "\n")


def get_active_profile(path: Path = ACTIVE_PROFILE_PATH) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return "full"


def zones_overlap(first: Zone, second: Zone) -> bool:
    """Retorna verdadeiro somente quando as áreas internas se sobrepõem."""
    return (
        first.x < second.x + second.width
        and first.x + first.width > second.x
        and first.y < second.y + second.height
        and first.y + first.height > second.y
    )


def move_zone_without_overlap(
    profile: Profile, index: int, new_x: float, new_y: float, snap: float = 0.018
) -> tuple[float, float]:
    """Move uma zona, encaixando em vizinhas e bloqueando interseções."""
    zone = profile.zones[index]
    others = [item for position, item in enumerate(profile.zones) if position != index]
    new_x = max(0.0, min(1.0 - zone.width, new_x))
    new_y = max(0.0, min(1.0 - zone.height, new_y))

    # Encaixe visual: bordas externas e internas próximas compartilham a linha.
    for other in others:
        vertical_near = new_y < other.y + other.height and new_y + zone.height > other.y
        if vertical_near:
            candidates = (other.x - zone.width, other.x + other.width, other.x, other.x + other.width - zone.width)
            closest = min(candidates, key=lambda value: abs(value - new_x))
            if abs(closest - new_x) <= snap:
                new_x = closest
        horizontal_near = new_x < other.x + other.width and new_x + zone.width > other.x
        if horizontal_near:
            candidates = (other.y - zone.height, other.y + other.height, other.y, other.y + other.height - zone.height)
            closest = min(candidates, key=lambda value: abs(value - new_y))
            if abs(closest - new_y) <= snap:
                new_y = closest

    new_x = max(0.0, min(1.0 - zone.width, new_x))
    new_y = max(0.0, min(1.0 - zone.height, new_y))
    old_x, old_y = zone.x, zone.y

    zone.x = new_x
    if any(zones_overlap(zone, other) for other in others):
        zone.x = old_x
    zone.y = new_y
    if any(zones_overlap(zone, other) for other in others):
        zone.y = old_y
    return zone.x, zone.y


def find_free_zone_position(profile: Profile, width: float, height: float) -> tuple[float, float]:
    """Procura uma posição livre previsível para uma nova correspondência."""
    probe = Zone("", 0, 0, width, height)
    for row in range(14):
        for column in range(14):
            probe.x = min(1.0 - width, column * 0.075)
            probe.y = min(1.0 - height, row * 0.075)
            if not any(zones_overlap(probe, other) for other in profile.zones):
                return probe.x, probe.y
    return 0.0, 0.0


def resize_zone_without_overlap(
    profile: Profile, index: int, width: float, height: float
) -> tuple[float, float]:
    """Redimensiona a captura sem invadir outra correspondência."""
    zone = profile.zones[index]
    old_width, old_height = zone.width, zone.height
    zone.width = max(0.08, min(1.0 - zone.x, width))
    zone.height = max(0.08, min(1.0 - zone.y, height))
    others = [item for position, item in enumerate(profile.zones) if position != index]
    if any(zones_overlap(zone, other) for other in others):
        zone.width, zone.height = old_width, old_height
    return zone.width, zone.height


def set_zone_movement_rotation(profile: Profile, index: int, rotation: int) -> bool:
    """Define o ângulo e troca largura/altura ao mudar de eixo."""
    zone = profile.zones[index]
    rotation %= 360
    if rotation == zone.movement_rotation:
        return True
    old = (zone.x, zone.y, zone.width, zone.height, zone.movement_rotation)
    changes_axis = (zone.movement_rotation // 90) % 2 != (rotation // 90) % 2
    if changes_axis:
        center_x = zone.x + zone.width / 2
        center_y = zone.y + zone.height / 2
        physical_width = zone.width * TABLET_WIDTH_MM
        physical_height = zone.height * TABLET_HEIGHT_MM
        zone.width = physical_height / TABLET_WIDTH_MM
        zone.height = physical_width / TABLET_HEIGHT_MM
        zone.x = center_x - zone.width / 2
        zone.y = center_y - zone.height / 2
        zone.clamp()
    zone.movement_rotation = rotation
    others = [item for position, item in enumerate(profile.zones) if position != index]
    if any(zones_overlap(zone, other) for other in others):
        zone.x, zone.y, zone.width, zone.height, zone.movement_rotation = old
        return False
    return True
