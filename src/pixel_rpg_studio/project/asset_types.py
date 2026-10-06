"""Registry of asset categories.

Adding a new asset type = calling :func:`register_asset_type` with a new
:class:`AssetType`. Pipelines, UI pages and exporters look types up here
instead of hard-coding category lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pipeline kinds: which shared pipeline produces the asset.
PIPELINE_RIGGED_3D = "rigged_3d"  # concept -> 3D -> rig -> animate -> render -> pixel
PIPELINE_STATIC_3D = "static_3d"  # concept -> 3D -> render views -> pixel
PIPELINE_IMAGE_2D = "image_2d"  # concept -> pixel (no 3D)
PIPELINE_TILESET = "tileset"
PIPELINE_VFX = "vfx"
PIPELINE_SPRITESHEET = "spritesheet"


@dataclass(frozen=True)
class ViewDirection:
    key: str
    label: str
    yaw_deg: float  # rotation of the model around the vertical axis, 0 = facing the camera


DIRECTIONS: dict[str, ViewDirection] = {
    d.key: d
    for d in (
        ViewDirection("s", "Front (down)", 0.0),
        ViewDirection("sw", "Front-left", 45.0),
        ViewDirection("w", "Left", 90.0),
        ViewDirection("nw", "Back-left", 135.0),
        ViewDirection("n", "Back (up)", 180.0),
        ViewDirection("ne", "Back-right", 225.0),
        ViewDirection("e", "Right", 270.0),
        ViewDirection("se", "Front-right", 315.0),
    )
}

DIRECTION_SETS: dict[str, tuple[str, ...]] = {
    "1 (front only)": ("s",),
    "side (left/right)": ("w", "e"),
    "4 directions": ("s", "w", "n", "e"),
    "8 directions": ("s", "sw", "w", "nw", "n", "ne", "e", "se"),
}


@dataclass(frozen=True)
class AssetType:
    key: str
    label: str
    folder: str
    pipeline: str
    description: str = ""
    subtypes: tuple[str, ...] = ()
    default_directions: tuple[str, ...] = ("s",)
    concept_workflow: str = "object_concept"
    prompt_template: str = "{description}"
    animated: bool = False
    extra: dict = field(default_factory=dict)


_REGISTRY: dict[str, AssetType] = {}


def register_asset_type(asset_type: AssetType) -> None:
    _REGISTRY[asset_type.key] = asset_type


def get_asset_type(key: str) -> AssetType:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"Unknown asset type '{key}'. Known: {', '.join(_REGISTRY)}") from None


def all_asset_types() -> list[AssetType]:
    return list(_REGISTRY.values())


ANIMATIONS = (
    "idle",
    "walk",
    "run",
    "attack",
    "heavy_attack",
    "hit",
    "death",
    "block",
    "skill",
    "cast",
    "dodge",
)

ANIMATION_DEFAULTS: dict[str, dict] = {
    "idle": {"frames": 4, "fps": 6, "loop": True},
    "walk": {"frames": 6, "fps": 10, "loop": True},
    "run": {"frames": 6, "fps": 12, "loop": True},
    "attack": {"frames": 6, "fps": 12, "loop": False},
    "heavy_attack": {"frames": 8, "fps": 10, "loop": False},
    "hit": {"frames": 3, "fps": 10, "loop": False},
    "death": {"frames": 6, "fps": 8, "loop": False},
    "block": {"frames": 3, "fps": 8, "loop": False},
    "skill": {"frames": 6, "fps": 10, "loop": False},
    "cast": {"frames": 6, "fps": 10, "loop": False},
    "dodge": {"frames": 5, "fps": 12, "loop": False},
}


def _register_defaults() -> None:
    for t in (
        AssetType(
            "character", "Characters", "characters", PIPELINE_RIGGED_3D,
            "Playable characters, NPCs and enemies with animations.",
            subtypes=("humanoid", "creature"),
            default_directions=DIRECTION_SETS["4 directions"],
            concept_workflow="character_concept",
            prompt_template="full body character, {description}, standing in T-pose, front view, "
            "plain white background, centered",
            animated=True,
        ),
        AssetType(
            "weapon", "Weapons", "weapons", PIPELINE_STATIC_3D,
            "Swords, axes, bows, staffs, shields...",
            subtypes=("sword", "axe", "spear", "bow", "staff", "shield", "dagger", "hammer", "other"),
            prompt_template="single {subtype} weapon, {description}, isolated object, plain white background, centered",
        ),
        AssetType(
            "item", "Items", "items", PIPELINE_STATIC_3D,
            "Pickups and inventory items: potions, books, armor pieces, keys...",
            subtypes=("potion", "book", "armor", "helmet", "key", "food", "gem", "scroll", "other"),
            prompt_template="single {subtype} item, {description}, isolated object, plain white background, centered",
        ),
        AssetType(
            "prop", "Props", "props", PIPELINE_STATIC_3D,
            "World props: crates, barrels, furniture, lamps, decorations, battlefield props.",
            subtypes=("crate", "barrel", "furniture", "lamp", "decoration", "battlefield", "other"),
            prompt_template="single {subtype} prop, {description}, isolated object, plain white background, centered",
        ),
        AssetType(
            "environment", "Environment", "environment", PIPELINE_STATIC_3D,
            "Environment objects: rocks, trees, bushes, cliffs pieces.",
            subtypes=("tree", "rock", "bush", "plant", "fence", "other"),
            prompt_template="single {subtype}, {description}, isolated object, plain white background, centered",
        ),
        AssetType(
            "building", "Buildings", "buildings", PIPELINE_STATIC_3D,
            "Houses, towers, shops and other structures, rendered from several views.",
            subtypes=("house", "tower", "shop", "castle", "ruin", "other"),
            default_directions=("s", "se", "e", "n"),
            concept_workflow="building_concept",
            prompt_template="single {subtype} building, {description}, exterior, isolated, plain white background, "
            "three-quarter view, centered",
        ),
        AssetType(
            "tile", "Tiles", "tiles", PIPELINE_TILESET,
            "Single seamless tiles.",
            concept_workflow="tileset_generation",
            prompt_template="seamless texture of {description}, top-down, flat, even lighting",
        ),
        AssetType(
            "tileset", "Tilesets", "tilesets", PIPELINE_TILESET,
            "Structured tilesets with edges, corners and transitions for Godot terrains.",
            concept_workflow="tileset_generation",
            prompt_template="seamless texture of {description}, top-down, flat, even lighting",
        ),
        AssetType(
            "background", "Backgrounds", "backgrounds", PIPELINE_IMAGE_2D,
            "Large backgrounds with optional parallax layers.",
            concept_workflow="background_generation",
            prompt_template="{description}, wide landscape background, game background art",
        ),
        AssetType(
            "vfx", "VFX", "vfx", PIPELINE_VFX,
            "Frame-based effects: fire, smoke, explosions, magic, lightning, healing...",
            animated=True,
        ),
        AssetType(
            "spritesheet", "Sprite Sheets", "spritesheets", PIPELINE_SPRITESHEET,
            "Sprite sheets assembled from frames of any asset or imported images.",
            animated=True,
        ),
    ):
        register_asset_type(t)


_register_defaults()
