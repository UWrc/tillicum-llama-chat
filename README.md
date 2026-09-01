# Llama.cpp Web UI — OpenOnDemand Application

Browser chat portal for the llama.cpp server (`llama-server`) running in
the `llama.sif` Apptainer container on a GPU node.

```
apptainer run --nv ~/hyakteam/sif/llama.sif serve \
    -m /gpfs/models/Qwen3.8-27B.gguf \
    --port <random> --threads <cpus> --ctx-size 0 --temp 0.8 --tools all
```

This app launches that server inside a Slurm job and exposes its built-in
Web UI — a SvelteKit chat interface for the OpenAI-compatible API —
through OpenOnDemand's node proxy.

## How it works

```
Browser ──► OOD /rnode/<host>/<port>/ ──► :$port (python proxy, compute node)
                                                │ strips /rnode/<host>/<port>
                                                ▼
                              127.0.0.1:<inference-port> (llama-server in apptainer, GPU)
```

* **`template/script.sh.erb`** — the job. Starts the llama server in the
  Apptainer container (loopback-only), starts the proxy on the OOD port,
  and keeps the job alive until teardown. A watchdog stops the job (and
  prints the last 30 server log lines) if the server dies.
* **`template/proxy.py`** — a small stdlib-only Python 3 reverse proxy
  (no Node.js needed on compute nodes). It:
  1. strips the `/rnode/<host>/<port>` prefix from incoming requests,
  2. rewrites `Origin`/`Referer` to `localhost` so the server's CORS
     policy (restricted to localhost when `--tools` is enabled) accepts
     the browser's same-origin requests,
  3. injects one small `<script>` into HTML responses that rewrites the
     few **root-absolute** API paths in the UI's JavaScript
     (`/v1/models`, `/models/load`, `/models/sse`, `/models/unload`,
     `/tools`, `/mcp-servers`, `/cors-proxy`) under the OOD prefix and
     disables service-worker registration,
  4. **streams** all non-HTML responses (SSE chat streams) without
     buffering — including the pre-gzipped UI assets, which are passed
     through untouched (this build serves its UI assets only as gzip and
     answers 415 "Error: gzip is not supported by this browser" to any
     request without `gzip` in `Accept-Encoding`, so the proxy passes the
     browser's `Accept-Encoding` through and only decompresses HTML
     responses in order to patch them), and
  5. shows an auto-refreshing "model loading" page while the server is
     still loading the GGUF file.

### Why a proxy at all?

The llama.cpp UI is mostly subpath-native (asset refs and the SvelteKit
router base are relative/dynamic, chat/stream/slots API calls are
relative). Only the handful of root-absolute API constants above would
miss the OOD prefix in the browser — a transparent reverse proxy (Caddy,
nginx) cannot rewrite those, which is why a small content-aware proxy is
needed. The shim approach was verified against the UI actually embedded
in `llama.sif` (extracted from the binary and unit-tested).

## Form options

| Field | Default | Notes |
|---|---|---|
| QoS | normal | `auto_qos`, default selection | 
| Number of GPUs | 1 | shown for information only; the job **always requests exactly 1 GPU** (`--gpus 1`) regardless of the field |
| Partition | gpu-h200 | `gpu_partition` dropdown → `--partition` (also `gpu-h200-mig`; `partition` is a reserved name in OOD). Drives hidden Slurm defaults: `gpu-h200` → `--cpus-per-task=8 --mem=240G`; `gpu-h200-mig` → `--cpus-per-task=2 --mem=36G` |
| Duration | 4 h | `bc_num_hours` → `--time=MM:00`. A live cost-estimate notice appears under the field (1 GPU × hours × $0.90 on `gpu-h200`, $0.13 on `gpu-h200-mig`), updated by `form.js` |
| Apptainer Image Path | `/gpfs/home/npho/hyakteam/sif/llama.sif` | **hidden field** (admin-managed, not shown to users); must be readable from compute nodes |
| Model | Qwen3.8-27B | dropdown of GGUF models under `/gpfs/models/`; the full on-disk path is selected automatically. Selecting **gemma-4-31B-it** also passes `--mmproj /gpfs/models/gemma-4-31B-it-GGUF/mmproj-BF16.gguf` and bind-mounts its directory |

**Advanced (Optional) section** (collapsed under a form header):

| Field | Default | Notes |
|---|---|---|
| Temperature | 0.80 | `--temp`, 0.00–1.00 in 0.01 steps |
| API Key | empty | if set, server requires `Authorization: Bearer <key>`; enter in the UI under Settings |
| Extra llama-server arguments | `-ngl 99` | e.g. `-ngl 99 --jinja` |
| Slurm args | — | optional extra `sbatch` options |

Always-on server flags (not form options):

* `--port` — random free port chosen by `find_port` in `before.sh.erb`
  (kept distinct from the OOD proxy port).
* `--threads` — taken from `$SLURM_CPUS_PER_TASK` (now 8 on `gpu-h200`, 2 on `gpu-h200-mig`, matching the partition-based CPU request; fallback: `nproc`).
* `--ctx-size 0` — let the model use its full context.
* `--tools all` — built-in agent tools are always enabled.

## Deploying

Copy (or symlink) this folder into the OpenOnDemand apps root, e.g.:

```
ln -s /gpfs/home/npho/ondemand/dev/llama-webui /path/to/ood/apps/llama-webui
```

It appears under **Interactive Apps → LLM Tools → Llama.cpp Web UI**.

The proxy script is referenced by absolute path
(`/gpfs/home/npho/ondemand/dev/llama-webui/template/proxy.py`) so the
deployed copy and the development folder can differ; override with the
`LLAMA_PROXY_PY` environment variable if you move it.

## Verifying

After launching a session from the OOD portal:

* The session page shows **Connected** and a "Launch Chat UI" button.
* The chat UI loads; while the model is loading you see a
  "Starting llama.cpp server" page that refreshes automatically.
* Once loaded, the model appears and chat works (token streaming included).
* Job output / logs live in `/tmp/llama-webui-<jobid>/` on the compute
  node (`llama-server.log`, `proxy.log`); set `PROXY_DEBUG=1` in the
  job environment for verbose proxy logging.

## Troubleshooting

* **UI shows "Error: gzip is not supported by this browser"** — the
  llama.cpp build in the SIF ships its UI assets pre-gzipped and requires
  `gzip` in `Accept-Encoding`. This is served by the *server* (HTTP 415),
  not the browser. It means the request reached the server without
  `gzip` — check that the deployed `proxy.py` is the version that passes
  `Accept-Encoding` through (and restart the session so the new proxy
  code is picked up).
* **Session connects but chat 404s / spins** — check the browser console;
  any request that misses the prefix shows up as a request to the OOD
  root. `proxy.log` (with `PROXY_DEBUG=1`) shows what is being forwarded.
* **Model never appears** — `llama-server.log`: model load failures
  (file not readable from the compute node, OOM, GPU not visible —
  `--nv` + `--gpus` must match) are printed there.
* **Port conflicts** — both the OOD port and the internal inference
  port are allocated by `find_port`; the inference port falls back to a
  scan of 15000–15099 if its allocation was lost.
* **Stale UI after a server update** — rebuild/re-tag the SIF; the app
  always reads the UI from the running container.
