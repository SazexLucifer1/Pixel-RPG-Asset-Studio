"""A small fake ComfyUI server implementing the HTTP API used by the app.

It validates submitted workflows like ComfyUI does (unknown node classes,
model files not in the installed lists) and can be told to fail executions.
"""

from __future__ import annotations

import io
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

INSTALLED = {
    "checkpoints": ["sd_xl_base_1.0.safetensors", "hunyuan3d-dit-v2-mini.safetensors"],
    "loras": ["pixel-art-xl.safetensors"],
}


def object_info():
    ck = [INSTALLED["checkpoints"], {}]
    simple = {"required": {}}
    info = {
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": ck}}},
        "ImageOnlyCheckpointLoader": {"input": {"required": {"ckpt_name": ["COMBO", {"options": INSTALLED["checkpoints"]}]}}},
        "LoraLoader": {"input": {"required": {"lora_name": [INSTALLED["loras"], {}]}}},
    }
    for n in ("CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage", "LoadImage", "VAEEncode",
              "ImageScaleToTotalPixels", "CLIPVisionEncode", "Hunyuan3Dv2Conditioning", "EmptyLatentHunyuan3Dv2",
              "ModelSamplingAuraFlow", "VAEDecodeHunyuan3D", "VoxelToMeshBasic", "VoxelToMesh", "SaveGLB"):
        info[n] = {"input": simple}
    return info


class FakeComfyUI:
    def __init__(self) -> None:
        self.history: dict[str, dict] = {}
        self.uploads: list[str] = []
        self.prompts: list[dict] = []
        self.fail_execution: str | None = None  # exception message to report
        self.fail_http_500 = False
        self.missing_nodes: set[str] = set()
        self.freed = 0
        self.interrupted = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _json(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                url = urlparse(self.path)
                if server.fail_http_500:
                    self._json({"error": "boom"}, 500)
                    return
                if url.path == "/system_stats":
                    self._json({"system": {"comfyui_version": "0.3.99-fake", "python_version": "3.12.0 (fake)"},
                                "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 3060", "type": "cuda", "vram_total": 12 * 1024**3, "vram_free": 10 * 1024**3}]})
                elif url.path == "/object_info":
                    info = object_info()
                    for n in server.missing_nodes:
                        info.pop(n, None)
                    self._json(info)
                elif url.path == "/queue":
                    self._json({"queue_running": [], "queue_pending": []})
                elif url.path.startswith("/history/"):
                    pid = url.path.split("/")[-1]
                    self._json({pid: server.history[pid]} if pid in server.history else {})
                elif url.path.startswith("/models/"):
                    self._json(INSTALLED.get(url.path.split("/")[-1], []))
                elif url.path == "/view":
                    q = parse_qs(url.query)
                    name = q.get("filename", [""])[0]
                    if name.endswith(".glb"):
                        data = b"glTF" + b"\x00" * 16
                        ctype = "model/gltf-binary"
                    else:
                        buf = io.BytesIO()
                        Image.new("RGB", (64, 64), (200, 50, 50)).save(buf, "PNG")
                        data, ctype = buf.getvalue(), "image/png"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                url = urlparse(self.path)
                body = self._body()
                if url.path == "/upload/image":
                    server.uploads.append(str(len(body)))
                    self._json({"name": "ref.png", "subfolder": "pixel_rpg_studio", "type": "input"})
                elif url.path == "/prompt":
                    payload = json.loads(body)
                    graph = payload["prompt"]
                    server.prompts.append(graph)
                    info = object_info()
                    for n in server.missing_nodes:
                        info.pop(n, None)
                    node_errors = {}
                    for nid, node in graph.items():
                        ct = node["class_type"]
                        if ct not in info:
                            self._json({"error": {"type": "invalid_prompt", "message": f"Cannot execute because node {ct} does not exist.",
                                                  "details": f"Node ID '#{nid}'", "extra_info": {}}, "node_errors": {}}, 400)
                            return
                        for inp, spec in info[ct]["input"].get("required", {}).items():
                            options = spec[0] if isinstance(spec[0], list) else spec[1].get("options")
                            val = node["inputs"].get(inp)
                            if options is not None and val not in options:
                                node_errors[nid] = {"class_type": ct, "errors": [{
                                    "type": "value_not_in_list", "message": "Value not in list",
                                    "details": f"{inp}: '{val}' not in {options}",
                                    "extra_info": {"input_name": inp, "received_value": val}}]}
                    if node_errors:
                        self._json({"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                                    "node_errors": node_errors}, 400)
                        return
                    pid = uuid.uuid4().hex
                    if server.fail_execution:
                        server.history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
                            ["execution_start", {"prompt_id": pid}],
                            ["execution_error", {"prompt_id": pid, "node_id": "3", "node_type": "KSampler",
                                                 "exception_message": server.fail_execution, "exception_type": "RuntimeError",
                                                 "traceback": ["Traceback...\n"]}]]}}
                    else:
                        is_3d = any(n["class_type"] == "SaveGLB" for n in graph.values())
                        out_node = next(nid for nid, n in graph.items() if n["class_type"] in ("SaveImage", "SaveGLB"))
                        outputs = {out_node: {"3d": [{"filename": "mesh_00001_.glb", "subfolder": "mesh", "type": "output"}]}} if is_3d else \
                            {out_node: {"images": [{"filename": "concept_00001_.png", "subfolder": "", "type": "output"}]}}
                        server.history[pid] = {"outputs": outputs, "status": {"status_str": "success", "completed": True, "messages": []}}
                    self._json({"prompt_id": pid, "number": len(server.prompts), "node_errors": {}})
                elif url.path == "/free":
                    server.freed += 1
                    self._json({})
                elif url.path == "/interrupt":
                    server.interrupted += 1
                    self._json({})
                else:
                    self._json({"error": "not found"}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
