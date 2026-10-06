"""ComfyUI workflow templates.

A workflow is a pair of files:

* ``<name>.json``           - a ComfyUI workflow in **API format**
                              (ComfyUI: Workflow → Export (API))
* ``<name>.manifest.json``  - describes which node inputs are parameters

The rest of the application only talks in parameter names (``prompt``,
``seed``, ``width``...) and model *roles* (``image.checkpoint``), never in
node IDs. Users can replace a workflow by dropping their own pair of files
into ``<project>/workflows/`` or ``%APPDATA%/PixelRPGAssetStudio/workflows/``.

Manifest format::

    {
      "name": "character_concept",
      "title": "Character concept (SDXL + pixel-art LoRA)",
      "kind": "image",                    # image | image_to_3d
      "description": "...",
      "parameters": {
        "prompt":  {"node": "6", "input": "text", "type": "string"},
        "seed":    {"node": "3", "input": "seed", "type": "int"},
        "lora_strength": {"node": "10", "input": ["strength_model", "strength_clip"], "type": "float", "default": 0.9},
        "init_image": {"node": "11", "input": "image", "type": "image"}
      },
      "models": {
        "image.checkpoint": {"node": "4", "input": "ckpt_name"}
      },
      "outputs": {"images": {"node": "9"}},
      "profiles": {"low": {"width": 768, "height": 768}}
    }
"""

from __future__ import annotations

import copy
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.errors import WorkflowError

log = logging.getLogger(__name__)

PARAM_TYPES = ("string", "int", "float", "bool", "image", "any")
MAX_SEED = 2**32 - 1


@dataclass
class ParameterBinding:
    name: str
    node: str
    inputs: list[str]
    type: str = "any"
    default: Any = None
    description: str = ""
    min: float | None = None
    max: float | None = None


@dataclass
class ModelBinding:
    role: str
    node: str
    input: str


@dataclass
class ValidationReport:
    workflow: str
    missing_node_types: list[str] = field(default_factory=list)
    missing_models: dict[str, str] = field(default_factory=dict)  # role -> filename
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_node_types or self.missing_models or self.errors)

    def summary(self) -> str:
        if self.ok:
            return "OK"
        parts = []
        if self.missing_node_types:
            parts.append("missing node types: " + ", ".join(sorted(self.missing_node_types)))
        if self.missing_models:
            parts.append("missing models: " + ", ".join(f"{v} ({k})" for k, v in self.missing_models.items()))
        parts.extend(self.errors)
        return "; ".join(parts)


class WorkflowTemplate:
    def __init__(self, name: str, graph: dict[str, Any], manifest: dict[str, Any], source: Path | None = None) -> None:
        self.name = name
        self.graph = graph
        self.manifest = manifest
        self.source = source
        self.title = manifest.get("title", name)
        self.kind = manifest.get("kind", "image")
        self.description = manifest.get("description", "")
        self.requirements: list[str] = list(manifest.get("requirements", []))
        self.parameters: dict[str, ParameterBinding] = {}
        for pname, spec in (manifest.get("parameters") or {}).items():
            inputs = spec.get("input")
            inputs = [inputs] if isinstance(inputs, str) else list(inputs or [])
            self.parameters[pname] = ParameterBinding(
                name=pname, node=str(spec.get("node")), inputs=inputs, type=spec.get("type", "any"),
                default=spec.get("default"), description=spec.get("description", ""),
                min=spec.get("min"), max=spec.get("max"),
            )
        self.models: dict[str, ModelBinding] = {
            role: ModelBinding(role, str(spec["node"]), spec["input"]) for role, spec in (manifest.get("models") or {}).items()
        }
        self.outputs: dict[str, str] = {k: str(v.get("node")) for k, v in (manifest.get("outputs") or {}).items()}
        self.profiles: dict[str, dict[str, Any]] = manifest.get("profiles") or {}

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, workflow_path: Path) -> "WorkflowTemplate":
        workflow_path = Path(workflow_path)
        manifest_path = workflow_path.with_name(workflow_path.stem + ".manifest.json")
        try:
            graph = json.loads(workflow_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkflowError(f"Workflow file not found: {workflow_path}") from exc
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Workflow {workflow_path.name} is not valid JSON.", details=str(exc)) from exc
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkflowError(
                f"Workflow {workflow_path.name} has no manifest ({manifest_path.name}).",
                hint="Every workflow needs a .manifest.json describing its parameters. See docs/workflows.md.",
            ) from exc
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Manifest {manifest_path.name} is not valid JSON.", details=str(exc)) from exc
        if "nodes" in graph and "links" in graph:
            raise WorkflowError(
                f"Workflow {workflow_path.name} is in ComfyUI's UI format, not API format.",
                hint="In ComfyUI use 'Workflow → Export (API)' and save that file instead.",
            )
        template = cls(manifest.get("name", workflow_path.stem), graph, manifest, workflow_path)
        problems = template.static_problems()
        if problems:
            raise WorkflowError(f"Workflow {workflow_path.name} is inconsistent with its manifest.", details="\n".join(problems))
        return template

    def static_problems(self) -> list[str]:
        """Check the manifest against the graph (no server needed)."""
        problems = []
        for node_id, node in self.graph.items():
            if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
                problems.append(f"Node {node_id} is not a valid API-format node.")
        for p in self.parameters.values():
            if p.type not in PARAM_TYPES:
                problems.append(f"Parameter '{p.name}' has unknown type '{p.type}'.")
            node = self.graph.get(p.node)
            if node is None:
                problems.append(f"Parameter '{p.name}' refers to missing node {p.node}.")
                continue
            for inp in p.inputs:
                if inp not in node.get("inputs", {}):
                    problems.append(f"Parameter '{p.name}': node {p.node} ({node.get('class_type')}) has no input '{inp}'.")
        for m in self.models.values():
            node = self.graph.get(m.node)
            if node is None:
                problems.append(f"Model role '{m.role}' refers to missing node {m.node}.")
            elif m.input not in node.get("inputs", {}):
                problems.append(f"Model role '{m.role}': node {m.node} has no input '{m.input}'.")
        for out, node_id in self.outputs.items():
            if node_id not in self.graph:
                problems.append(f"Output '{out}' refers to missing node {node_id}.")
        return problems

    # ------------------------------------------------------------ building
    def node_types(self) -> set[str]:
        return {n["class_type"] for n in self.graph.values() if isinstance(n, dict) and "class_type" in n}

    def default_model(self, role: str) -> str:
        m = self.models[role]
        return str(self.graph[m.node]["inputs"][m.input])

    def build(self, params: dict[str, Any], models: dict[str, str] | None = None, profile: str | None = None) -> dict[str, Any]:
        """Return a filled API-format graph ready for ``/prompt``.

        Unknown parameter names raise; parameters not given use the manifest
        default or the value already in the workflow file. Profile overrides
        (e.g. ``low`` VRAM) are applied before explicit params.
        """
        graph = copy.deepcopy(self.graph)
        values: dict[str, Any] = {}
        for name, binding in self.parameters.items():
            if binding.default is not None:
                values[name] = binding.default
        if profile and profile in self.profiles:
            values.update(self.profiles[profile])
        for name, value in params.items():
            if value is None:
                continue
            if name not in self.parameters:
                raise WorkflowError(
                    f"Workflow '{self.name}' has no parameter '{name}'.",
                    hint=f"Available parameters: {', '.join(sorted(self.parameters))}",
                )
            values[name] = value
        for name, value in values.items():
            if name not in self.parameters:
                continue
            binding = self.parameters[name]
            coerced = _coerce(value, binding)
            for inp in binding.inputs:
                graph[binding.node]["inputs"][inp] = coerced
        for role, filename in (models or {}).items():
            if role in self.models and filename:
                m = self.models[role]
                graph[m.node]["inputs"][m.input] = filename
        return graph

    def resolved_models(self, models: dict[str, str] | None) -> dict[str, str]:
        result = {}
        for role in self.models:
            result[role] = (models or {}).get(role) or self.default_model(role)
        return result

    # ---------------------------------------------------------- validation
    def validate_against_server(self, object_info: dict[str, Any], models: dict[str, str] | None = None) -> ValidationReport:
        from pixel_rpg_studio.comfyui.client import combo_options

        report = ValidationReport(self.name)
        available = set(object_info.keys())
        report.missing_node_types = sorted(t for t in self.node_types() if t not in available)
        for role, filename in self.resolved_models(models).items():
            m = self.models[role]
            class_type = self.graph[m.node]["class_type"]
            info = object_info.get(class_type)
            if not info:
                continue  # already reported as missing node type
            spec = None
            for group in ("required", "optional"):
                spec = info.get("input", {}).get(group, {}).get(m.input) or spec
            options = combo_options(spec) if spec is not None else None
            if options is not None and filename not in options:
                # tolerate path-separator differences (Windows sub folders)
                normalized = {o.replace("\\", "/") for o in options}
                if filename.replace("\\", "/") not in normalized:
                    report.missing_models[role] = filename
        return report


def _coerce(value: Any, binding: ParameterBinding) -> Any:
    try:
        if binding.type == "int":
            v = int(value)
        elif binding.type == "float":
            v = float(value)
        elif binding.type == "bool":
            return bool(value)
        elif binding.type in ("string", "image"):
            return str(value)
        else:
            return value
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"Parameter '{binding.name}' must be {binding.type}, got {value!r}.") from exc
    if binding.min is not None and v < binding.min:
        v = type(v)(binding.min)
    if binding.max is not None and v > binding.max:
        v = type(v)(binding.max)
    return v


def random_seed() -> int:
    return random.SystemRandom().randint(0, MAX_SEED)


class WorkflowLibrary:
    """Finds workflows: project folder > user folder > bundled defaults."""

    def __init__(self, extra_dirs: list[Path] | None = None) -> None:
        self.search_dirs: list[Path] = [Path(p) for p in (extra_dirs or [])]
        self.search_dirs.append(paths.app_data_dir() / "workflows")
        self.search_dirs.append(paths.bundled_workflows_dir())
        self._cache: dict[str, WorkflowTemplate] = {}

    def find_path(self, name: str) -> Path:
        for d in self.search_dirs:
            candidate = d / f"{name}.json"
            if candidate.is_file():
                return candidate
        raise WorkflowError(
            f"Workflow '{name}' was not found.",
            hint="Reinstall the application or restore the workflows folder. Searched: "
            + "; ".join(str(d) for d in self.search_dirs),
            code="workflow_missing",
        )

    def get(self, name: str) -> WorkflowTemplate:
        path = self.find_path(name)
        cached = self._cache.get(name)
        if cached is not None and cached.source == path:
            return cached
        template = WorkflowTemplate.load(path)
        self._cache[name] = template
        return template

    def names(self) -> list[str]:
        found: dict[str, Path] = {}
        for d in self.search_dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                if f.name.endswith(".manifest.json"):
                    continue
                if f.with_name(f.stem + ".manifest.json").exists():
                    found.setdefault(f.stem, f)
        return sorted(found)

    def all(self) -> list[WorkflowTemplate]:
        result = []
        for n in self.names():
            try:
                result.append(self.get(n))
            except WorkflowError as exc:
                log.warning("Skipping invalid workflow %s: %s", n, exc.message)
        return result
