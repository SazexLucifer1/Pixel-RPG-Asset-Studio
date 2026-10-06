"""HTTP client for a local ComfyUI server.

Only documented ComfyUI server endpoints are used:

* ``GET  /system_stats``      health, version, devices/VRAM
* ``GET  /object_info``       installed node classes + model file lists
* ``POST /upload/image``      upload reference images
* ``POST /prompt``            queue a workflow (API format)
* ``GET  /queue``             queue state
* ``GET  /history/{id}``      results / execution errors
* ``GET  /view``              download output files
* ``POST /interrupt``         cancel the running prompt
* ``POST /free``              unload models / free VRAM between stages
* ``WS   /ws?clientId=``      live progress (optional, falls back to polling)

All failures are converted to :class:`StudioError` subclasses with
explanations a non-expert can act on.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from pixel_rpg_studio.comfyui.ws import SimpleWebSocket, WebSocketError
from pixel_rpg_studio.core.errors import (
    BackendUnavailableError,
    GenerationError,
    JobCancelledError,
    MissingCustomNodeError,
    MissingModelError,
    StudioError,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float | None, str], None]


@dataclass
class ServerStatus:
    reachable: bool
    url: str
    version: str = ""
    python_version: str = ""
    devices: list[dict[str, Any]] = field(default_factory=list)
    queue_running: int = 0
    queue_pending: int = 0
    error: str = ""

    @property
    def vram_total_gb(self) -> float | None:
        for d in self.devices:
            if d.get("type") == "cuda" or "cuda" in str(d.get("name", "")).lower():
                return round(d.get("vram_total", 0) / 1024**3, 1)
        return None

    @property
    def vram_free_gb(self) -> float | None:
        for d in self.devices:
            if d.get("type") == "cuda" or "cuda" in str(d.get("name", "")).lower():
                return round(d.get("vram_free", 0) / 1024**3, 1)
        return None


@dataclass
class OutputFile:
    node_id: str
    filename: str
    subfolder: str
    type: str
    kind: str  # "images", "3d", "gifs", ...


@dataclass
class PromptResult:
    prompt_id: str
    outputs: list[OutputFile]
    duration_s: float
    raw: dict[str, Any] = field(default_factory=dict)

    def files_with_extension(self, *exts: str) -> list[OutputFile]:
        exts = tuple(e.lower() for e in exts)
        return [o for o in self.outputs if o.filename.lower().endswith(exts)]


def combo_options(spec: Any) -> list[str] | None:
    """Extract the allowed values of a COMBO input from object_info.

    Supports both the classic ``[[...values...], {...}]`` format and the newer
    ``["COMBO", {"options": [...]}]`` format.
    """
    if not isinstance(spec, list) or not spec:
        return None
    first = spec[0]
    if isinstance(first, list):
        return [str(v) for v in first]
    if first == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        opts = spec[1].get("options")
        if isinstance(opts, list):
            return [str(v) for v in opts]
    return None


def explain_prompt_error(payload: dict[str, Any], workflow: dict[str, Any] | None = None) -> StudioError:
    """Translate a ComfyUI /prompt validation error into a helpful message."""
    error = payload.get("error") or {}
    node_errors = payload.get("node_errors") or {}
    detail = json.dumps(payload, indent=2)[:6000]
    missing_models: list[str] = []
    other: list[str] = []
    for node_id, info in node_errors.items():
        class_type = info.get("class_type", "?")
        for e in info.get("errors", []):
            etype = e.get("type", "")
            extra = e.get("extra_info") or {}
            input_name = extra.get("input_name", "")
            if etype == "value_not_in_list" and any(k in input_name for k in ("ckpt", "lora", "model", "vae", "clip", "unet")):
                bad = extra.get("received_value") or e.get("details", "")
                missing_models.append(f"{bad} (node {node_id} {class_type}.{input_name})")
            else:
                other.append(f"node {node_id} ({class_type}): {e.get('message', '')} {e.get('details', '')}".strip())
    if missing_models:
        return MissingModelError(
            "Required model missing: " + "; ".join(missing_models),
            hint="Install the model in ComfyUI's models folder (see the AI Models page for download links), "
            "or choose an installed model for this role in AI Models.",
            details=detail,
            code="missing_model",
        )
    etype = error.get("type", "")
    if etype in ("invalid_prompt",) and "does not exist" in str(error.get("message", "")).lower():
        return MissingCustomNodeError(
            "The workflow uses a node type that is not installed in ComfyUI.",
            hint="Update ComfyUI (newer core nodes) or install the custom node pack required by this workflow. "
            "The AI Models page lists the requirements of every workflow.",
            details=detail,
            code="missing_node",
        )
    message = error.get("message") or "ComfyUI rejected the workflow."
    return GenerationError(
        f"ComfyUI rejected the workflow: {message}",
        hint="Check that the required models and custom nodes are installed. "
        "Run the diagnostic to validate all workflows against your ComfyUI.",
        details="\n".join(other) + "\n\n" + detail,
        code="prompt_rejected",
    )


def explain_execution_error(messages: list[Any]) -> StudioError:
    for msg in messages:
        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_error":
            data = msg[1] or {}
            exc_type = str(data.get("exception_type", ""))
            exc_msg = str(data.get("exception_message", "")).strip()
            node = f"{data.get('node_type', '?')} (node {data.get('node_id', '?')})"
            tb = "".join(data.get("traceback") or [])
            low = (exc_type + exc_msg).lower()
            if "out of memory" in low or "outofmemory" in low:
                return GenerationError(
                    f"The GPU ran out of memory while running {node}.",
                    hint="Enable Low VRAM mode in Settings, lower the resolution, close other GPU programs, "
                    "and try again.",
                    details=f"{exc_type}: {exc_msg}\n{tb}",
                    code="gpu_oom",
                )
            return GenerationError(
                f"ComfyUI failed while executing the workflow at {node}: {exc_msg[:300]}",
                hint="Check that the required model is installed and that the custom nodes used by this "
                "workflow are available. 'View Log' shows the full ComfyUI error.",
                details=f"{exc_type}: {exc_msg}\n{tb}",
                code="execution_error",
            )
    return GenerationError(
        "ComfyUI reported an error while executing the selected workflow.",
        hint="Check that the required model is installed and that the custom nodes used by this workflow are available.",
        details=json.dumps(messages, indent=2)[:6000],
        code="execution_error",
    )


class ComfyUIClient:
    def __init__(self, base_url: str, request_timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.client_id = uuid.uuid4().hex
        self._session = requests.Session()
        self._session.trust_env = False  # local server: never route through system proxies
        self._object_info: dict[str, Any] | None = None

    # ------------------------------------------------------------ low level
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.request_timeout)
        try:
            response = self._session.request(method, self._url(path), **kwargs)
        except requests.ConnectionError as exc:
            raise BackendUnavailableError(
                f"ComfyUI is not reachable at {self.base_url}.",
                hint="Start ComfyUI (status bar → Start ComfyUI) or check the address in Settings.",
                details=str(exc),
                code="backend_unavailable",
            ) from exc
        except requests.Timeout as exc:
            raise BackendUnavailableError(
                f"ComfyUI at {self.base_url} did not answer within {kwargs['timeout']} s.",
                hint="ComfyUI may be busy loading a model; try again in a moment.",
                details=str(exc),
                code="backend_timeout",
            ) from exc
        if response.status_code >= 500:
            raise GenerationError(
                f"ComfyUI reported an internal error (HTTP {response.status_code}) for {path}.",
                hint="Check that the required model is installed and that the custom nodes used by this workflow "
                "are available. The ComfyUI log contains the full error.",
                details=response.text[:6000],
                code="server_error",
            )
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        if response.status_code >= 400:
            raise StudioError(
                f"ComfyUI request {path} failed (HTTP {response.status_code}).",
                details=response.text[:6000],
                code="http_error",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise StudioError(
                f"ComfyUI returned an unexpected answer for {path}.",
                hint="Is another program running on this port instead of ComfyUI? Check the address in Settings.",
                details=response.text[:2000],
                code="bad_response",
            ) from exc

    # ---------------------------------------------------------------- health
    def status(self) -> ServerStatus:
        try:
            stats = self._json("GET", "/system_stats", timeout=min(5.0, self.request_timeout))
        except StudioError as exc:
            return ServerStatus(False, self.base_url, error=exc.message)
        system = stats.get("system", {}) if isinstance(stats, dict) else {}
        st = ServerStatus(
            True, self.base_url,
            version=str(system.get("comfyui_version", "")),
            python_version=str(system.get("python_version", "")).split(" ")[0],
            devices=list(stats.get("devices", [])) if isinstance(stats, dict) else [],
        )
        try:
            q = self.queue()
            st.queue_running = len(q.get("queue_running", []))
            st.queue_pending = len(q.get("queue_pending", []))
        except StudioError:
            pass
        return st

    def is_alive(self) -> bool:
        return self.status().reachable

    def queue(self) -> dict[str, Any]:
        return self._json("GET", "/queue")

    def object_info(self, refresh: bool = False) -> dict[str, Any]:
        if self._object_info is None or refresh:
            self._object_info = self._json("GET", "/object_info", timeout=max(60.0, self.request_timeout))
        return self._object_info

    def node_classes(self) -> set[str]:
        return set(self.object_info().keys())

    def input_options(self, class_type: str, input_name: str) -> list[str] | None:
        info = self.object_info().get(class_type)
        if not info:
            return None
        inputs = info.get("input", {})
        for group in ("required", "optional"):
            spec = inputs.get(group, {}).get(input_name)
            if spec is not None:
                return combo_options(spec)
        return None

    def list_models(self, folder: str) -> list[str]:
        """List model files in a ComfyUI model folder (e.g. ``checkpoints``, ``loras``)."""
        loader_inputs = {
            "checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
            "loras": ("LoraLoader", "lora_name"),
            "vae": ("VAELoader", "vae_name"),
            "clip_vision": ("CLIPVisionLoader", "clip_name"),
            "controlnet": ("ControlNetLoader", "control_net_name"),
            "ipadapter": ("IPAdapterModelLoader", "ipadapter_file"),
            "upscale_models": ("UpscaleModelLoader", "model_name"),
        }
        if folder in loader_inputs:
            options = self.input_options(*loader_inputs[folder])
            if options is not None:
                return options
        try:
            data = self._json("GET", f"/models/{folder}")
            if isinstance(data, list):
                return [str(x) for x in data]
        except StudioError:
            pass
        return []

    # --------------------------------------------------------------- actions
    def upload_image(self, path: Path, subfolder: str = "pixel_rpg_studio", overwrite: bool = True,
                     upload_name: str | None = None) -> str:
        """Upload an image to ComfyUI's input folder; returns the name to use in LoadImage."""
        path = Path(path)
        with open(path, "rb") as fh:
            response = self._request(
                "POST", "/upload/image",
                files={"image": (upload_name or path.name, fh, "image/png")},
                data={"subfolder": subfolder, "type": "input", "overwrite": "true" if overwrite else "false"},
            )
        if response.status_code >= 400:
            raise StudioError(f"Uploading {path.name} to ComfyUI failed (HTTP {response.status_code}).", details=response.text[:2000])
        data = response.json()
        name = data.get("name", path.name)
        sub = data.get("subfolder", "")
        return f"{sub}/{name}" if sub else name

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        response = self._request("POST", "/prompt", json={"prompt": workflow, "client_id": self.client_id})
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": response.text[:2000]}}
        if response.status_code >= 400 or payload.get("error"):
            raise explain_prompt_error(payload, workflow)
        if payload.get("node_errors"):
            raise explain_prompt_error(payload, workflow)
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise GenerationError("ComfyUI did not return a prompt id.", details=json.dumps(payload)[:2000])
        return str(prompt_id)

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        data = self._json("GET", f"/history/{prompt_id}")
        if isinstance(data, dict):
            return data.get(prompt_id)
        return None

    def interrupt(self) -> None:
        try:
            self._request("POST", "/interrupt", json={})
        except StudioError:
            log.warning("Interrupt request failed", exc_info=True)

    def free_memory(self, unload_models: bool = True) -> bool:
        try:
            r = self._request("POST", "/free", json={"unload_models": unload_models, "free_memory": True})
            return r.status_code < 400
        except StudioError:
            return False

    def download(self, output: OutputFile, dest: Path) -> Path:
        response = self._request(
            "GET", "/view",
            params={"filename": output.filename, "subfolder": output.subfolder, "type": output.type},
            timeout=max(60.0, self.request_timeout),
        )
        if response.status_code >= 400:
            raise StudioError(f"Could not download {output.filename} from ComfyUI (HTTP {response.status_code}).", details=response.text[:2000])
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest

    # ------------------------------------------------------------ high level
    def run(
        self,
        workflow: dict[str, Any],
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
        timeout: float = 1800.0,
        poll_interval: float = 0.5,
    ) -> PromptResult:
        """Queue a workflow and block until it finishes; returns its outputs."""
        progress = progress or (lambda f, m: None)
        cancelled = cancelled or (lambda: False)
        started = time.time()
        listener = _ProgressListener(self.base_url, self.client_id, progress)
        listener.start()
        try:
            prompt_id = self.queue_prompt(workflow)
            listener.prompt_id = prompt_id
            progress(0.0, "Queued in ComfyUI")
            last_queue_check = 0.0
            while True:
                if cancelled():
                    self.interrupt()
                    raise JobCancelledError()
                if time.time() - started > timeout:
                    self.interrupt()
                    raise GenerationError(
                        f"ComfyUI did not finish the workflow within {int(timeout)} s.",
                        hint="Increase the generation timeout in Settings or use Low VRAM mode.",
                        code="generation_timeout",
                    )
                entry = self.history(prompt_id)
                if entry:
                    status = entry.get("status") or {}
                    if status.get("status_str") == "error":
                        raise explain_execution_error(status.get("messages", []))
                    if status.get("completed", True) or entry.get("outputs"):
                        outputs = _collect_outputs(entry.get("outputs", {}))
                        progress(1.0, "ComfyUI finished")
                        return PromptResult(prompt_id, outputs, time.time() - started, entry)
                now = time.time()
                if not listener.connected and now - last_queue_check > 2.0:
                    last_queue_check = now
                    try:
                        q = self.queue()
                        running = [i[1] for i in q.get("queue_running", []) if len(i) > 1]
                        pending = [i[1] for i in q.get("queue_pending", []) if len(i) > 1]
                        if prompt_id in running:
                            progress(None, "Generating in ComfyUI...")
                        elif prompt_id in pending:
                            progress(None, f"Waiting in ComfyUI queue ({len(pending)} pending)")
                    except StudioError:
                        pass
                time.sleep(poll_interval)
        finally:
            listener.stop()


def _collect_outputs(outputs: dict[str, Any]) -> list[OutputFile]:
    files: list[OutputFile] = []
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for kind, items in node_out.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and "filename" in item:
                    files.append(OutputFile(str(node_id), item["filename"], item.get("subfolder", ""), item.get("type", "output"), kind))
    return files


class _ProgressListener:
    """Best-effort websocket progress listener. Falls back silently to polling."""

    def __init__(self, base_url: str, client_id: str, progress: ProgressCallback) -> None:
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={client_id}"
        self.progress = progress
        self.prompt_id: str | None = None
        self.connected = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="comfyui-ws", daemon=True)
        self._ws: SimpleWebSocket | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()

    def _run(self) -> None:
        try:
            self._ws = SimpleWebSocket.connect(self.url, timeout=3.0)
            self.connected = True
        except (WebSocketError, OSError) as exc:
            log.debug("ComfyUI websocket unavailable (%s); using polling", exc)
            return
        while not self._stop.is_set():
            try:
                msg = self._ws.recv_text(timeout=0.5)
            except TimeoutError:
                continue
            except (WebSocketError, OSError):
                self.connected = False
                return
            if msg is None:
                continue
            try:
                data = json.loads(msg)
            except ValueError:
                continue
            mtype, payload = data.get("type"), data.get("data") or {}
            if self.prompt_id and payload.get("prompt_id") not in (None, self.prompt_id):
                continue
            if mtype == "progress" and payload.get("max"):
                frac = float(payload.get("value", 0)) / float(payload["max"])
                self.progress(min(0.99, frac), f"Generating: step {payload.get('value')}/{payload.get('max')}")
            elif mtype == "executing" and payload.get("node"):
                self.progress(None, f"Running node {payload.get('node')}")
