# Llama.cpp WebUI — Open OnDemand Application

Open OnDemand (OOD) Batch Connect application that starts `llama-server` in
an Apptainer container on a Slurm GPU node and exposes llama.cpp's bundled Web
UI through the OOD node proxy.

The application currently targets the `tillicum` cluster and supports the
`gpu-h200` and `gpu-h200-mig` partitions.

## Current defaults

| Setting | Current value |
|---|---|
| Cluster | `tillicum` |
| QoS | `normal` |
| GPUs | exactly 1 |
| Duration | 4 hours |
| Default partition | `gpu-h200` |
| Container | `/gpfs/containers/llama.cpp/llama.cpp_full-cuda13.sif` |
| Temperature | `0.8` |
| Context size | model maximum (`--ctx-size 0`) |
| Built-in llama.cpp tools | enabled (`--tools all`) |

The number of GPUs and container path are hidden, administrator-managed form
values. The generated Slurm request always asks for one GPU.

## Architecture

```text
Browser
  │
  ▼
OOD /rnode/<compute-host>/<ood-port>/
  │
  ▼
Python reverse proxy on 0.0.0.0:<ood-port>
  │ strips the OOD prefix and normalizes request headers
  ▼
llama-server on 127.0.0.1:<inference-port>
  │
  ▼
Apptainer container + allocated GPU
```

Two different random ports are allocated:

- `port` is the externally routed OOD node-proxy port.
- `llama_port` is the loopback-only llama.cpp inference port.

The proxy starts immediately. Until `llama-server` finishes loading the GGUF,
it returns a friendly auto-refreshing loading page instead of a raw upstream
error.

## Application files

| Path | Purpose |
|---|---|
| `manifest.yml` | OOD name, menu category, role, and description |
| `form.yml` | Form fields, model catalog, defaults, and dynamic OOD directives |
| `form.js` | Cost estimate, advanced-field visibility, and partition/model filtering |
| `submit.yml.erb` | Slurm resources and optional extra Slurm arguments |
| `template/before.sh.erb` | Port allocation and validation of the container, model, Python, and proxy |
| `template/script.sh.erb` | Starts `llama-server`, the proxy, watchdog, and cleanup logic |
| `template/after.sh` | Waits up to 120 seconds for the proxy port |
| `template/proxy.py` | Standard-library Python reverse proxy and WebUI subpath shim |
| `view.html.erb` | OOD session page and **Launch Chat** button |

## Slurm resources

Resources are selected from `gpu_partition` in `submit.yml.erb`:

| Partition | GPUs | CPUs per task | Memory | Estimated GPU rate |
|---|---:|---:|---:|---:|
| `gpu-h200` | 1 | 8 | 240 GB | $0.90/hour |
| `gpu-h200-mig` | 1 MIG GPU | 2 | 34 GB | $0.13/hour |

The 34 GB MIG request is intentional: the scheduler rejects requests above 34
GB per requested MIG GPU.

`bc_num_hours` is converted to minutes for Slurm. For example, 4 hours becomes:

```text
--time=240:00
```

The cost shown by `form.js` is an estimate only:

```text
1 GPU × requested hours × partition rate
```

The selected account and QoS come from OOD's `auto_accounts` and `auto_qos`
fields. OOD may restore a user's cached form selection, so the value visible in
the browser can differ from the declared `normal` QoS default.

## Partition-aware models

`form.js` filters the **Model** dropdown whenever the partition changes. Each
model option also has an OOD `data-option-for-...` directive, so native OOD
dynamic forms and the application JavaScript express the same relationship.
The JavaScript fallback remains necessary on sites where `bc_dynamic_js` is
disabled.

### Currently configured models

| Partition | Display name | GGUF path | Status |
|---|---|---|---|
| `gpu-h200` | Alibaba Qwen3.8 (27B-bf16) | `/gpfs/models/Qwen3.8-27B.gguf` | enabled |
| `gpu-h200` | NVIDIA Nemotron-3.5-Lightning (30B-A3B-bf16) | `/gpfs/models/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16.gguf` | enabled |
| `gpu-h200` | Google gemma-4 (31B-it-bf16) | `/gpfs/models/gemma-4-31B-it-GGUF/BF16/gemma-4-31B-it-BF16-00001-of-00002.gguf` | enabled |
| `gpu-h200-mig` | Google gemma-4 (31B-it-UD-IQ3_XXS) | `/gpfs/models/gemma-4-31B-it-GGUF/gemma-4-31B-it-UD-IQ3_XXS.gguf` | enabled |
| `gpu-h200-mig` | Google gemma-4 (31B-it-UD-IQ3_XXS) | same as above | disabled placeholder |

The final disabled entry currently duplicates the enabled MIG entry. It is only
a placeholder and should be replaced with a different compatible model or
removed.

The `model_path` YAML default is intentionally empty. On page initialization,
`form.js` selects the first enabled model compatible with the current
partition. If a partition has no enabled models, it clears the selection,
marks the required field invalid, and displays a warning.

### Adding another model

Add an option under `attributes.model_path.options` in `form.yml`.

Full H200 example:

```yaml
- ["Model name", "/gpfs/models/model.gguf",
   {data-partition: "gpu-h200",
    data-option-for-gpu-partition-gpu-h200-mig: false}]
```

MIG example:

```yaml
- ["Model name", "/gpfs/models/model.gguf",
   {data-partition: "gpu-h200-mig",
    data-option-for-gpu-partition-gpu-h200: false}]
```

For a non-selectable future placeholder, add these attributes to the option:

```yaml
disabled: true
data-model-placeholder: true
```

Keep all option HTML attributes in one hash in the third array position. The
model path must be readable from compute nodes and the model must fit within
the selected partition's GPU allocation.

Any model whose path is under `/gpfs/models/gemma-4-31B-it-GGUF/` also receives:

```text
--mmproj /gpfs/models/gemma-4-31B-it-GGUF/mmproj-BF16.gguf
```

The script bind-mounts both the model directory and the projector directory
into the container.

## Advanced options

The **Explore advanced options?** checkbox controls these visible fields:

| Field | Effective behavior |
|---|---|
| Temperature | Uses the selected value when advanced options are enabled; otherwise forced to `0.8` |
| Extra llama-server arguments | Passed only when advanced options are enabled |
| Extra Slurm command-line options | Added only when advanced options are enabled |

`api_key` is currently a hidden field with an empty administrator-defined
value; users cannot edit it in the launch form. If configured, it is encoded
during ERB rendering and supplied to `llama-server` as `--api-key`.

The current form value for extra llama-server arguments is:

```text
--jinja --webui-mcp-proxy
```

Because advanced arguments are gated, those flags are passed only after the
checkbox is selected. In the currently deployed llama.cpp container, Jinja is
enabled by default; `--webui-mcp-proxy` is needed only when using the WebUI MCP
CORS proxy.

Extra argument strings are split on whitespace. Shell-style quoting inside the
form value is not preserved, so this field is best suited to simple flags and
single-token values.

### Adding another advanced field

1. Define the attribute in `form.yml` and add it to the `form` list.
2. Add its underscore-form name to `ADVANCED_FIELDS` in `form.js`.
3. Under `enable_advanced_args.html_options.data`, add an OOD directive using
   hyphens instead of underscores:

```yaml
hide-context-size-when-un-checked: true
```

Hiding a field does not clear its submitted value. If an option must have no
effect while advanced settings are disabled, gate it in `submit.yml.erb` or
`template/script.sh.erb`, as is currently done for temperature and extra
arguments.

## llama-server invocation

The effective command is equivalent to:

```bash
apptainer run --nv \
  --bind "$(dirname "$MODEL_PATH")" \
  "$LLAMA_SIF" serve \
  -m "$MODEL_PATH" \
  --host 127.0.0.1 \
  --port "$LLAMA_PORT" \
  --threads "$SLURM_CPUS_PER_TASK" \
  --ctx-size 0 \
  --temp "$TEMPERATURE" \
  --tools all \
  [--mmproj ...] \
  [--api-key ...] \
  [extra llama-server arguments]
```

`--tools all` enables the built-in tools supplied by this llama.cpp build. It
does not inherently give the model a dedicated web-search or URL-fetch tool.
Tool availability also depends on the WebUI settings, model chat template, and
user approval. Enabling all tools can expose filesystem and shell operations on
the compute node; treat this application as trusted-user functionality.

## Reverse proxy behavior

`template/proxy.py` is a threaded, standard-library-only HTTP proxy. It:

1. strips `/rnode/<host>/<port>` before forwarding requests;
2. rewrites `Host`, `Origin`, and `Referer` for llama.cpp's localhost CORS
   policy;
3. injects a small JavaScript shim into HTML before the WebUI scripts run;
4. prefixes root-absolute WebUI endpoints such as `/v1/models`, `/models/*`,
   `/tools`, `/mcp-servers`, and `/cors-proxy`;
5. patches root-absolute module-preload links;
6. disables service-worker registration to avoid stale per-port PWA caches;
7. streams SSE, downloads, and compressed assets without buffering; and
8. returns an auto-refreshing loading page while the upstream server is not
   ready.

The container's WebUI assets are pre-compressed. The proxy preserves the
browser's `Accept-Encoding` header and passes non-HTML gzip responses through
unchanged. It decompresses HTML only when necessary to inject the subpath shim.

## Deployment

A development app can be linked into the OOD development app directory. A
production deployment is normally cloned or copied into the site's system app
root according to local OOD administration policy.

This installation currently expects the proxy source at:

```text
/gpfs/home/npho/ondemand/dev/llama-webui/template/proxy.py
```

That absolute fallback appears in both `before.sh.erb` and `script.sh.erb`. If
the app is moved, either update those paths or set `LLAMA_PROXY_PY` to a proxy
file readable from compute nodes.

The manifest places the application under:

```text
Interactive Apps → LLM Tools → Llama.cpp WebUI
```

### Application version shown by OOD

OOD displays the root `VERSION` file when one exists. Otherwise it uses:

```bash
git describe --always --tags
```

Tagging a release therefore produces a friendlier version label:

```bash
git tag -a 1.0 -m "Llama WebUI release 1.0"
git push origin 1.0
```

Without a `VERSION` file or matching tag, OOD displays an abbreviated commit
hash.

## Logs and validation

Before launch, the application checks that:

- the Apptainer image is readable;
- the selected GGUF is readable;
- `python3` is available; and
- the proxy script is readable.

OOD's primary session output is stored beneath its Batch Connect session
output directory. Runtime component logs are written on the compute node to:

```text
${TMPDIR:-/tmp}/llama-webui-${SLURM_JOB_ID}/llama-server.log
${TMPDIR:-/tmp}/llama-webui-${SLURM_JOB_ID}/proxy.log
```

Set `PROXY_DEBUG=1` in the job environment for verbose proxy request logging.
The watchdog prints the final 30 llama-server log lines to OOD output if the
server process exits unexpectedly.

## Verification checklist

After submitting a session:

1. Confirm the session reaches **Running** and then **Connected**.
2. Click **Launch Chat**.
3. Expect the loading page while the model initializes.
4. Confirm the llama.cpp WebUI appears and lists the selected model.
5. Send a prompt and verify that token streaming works.
6. If using tools, confirm they appear in the WebUI and test a harmless,
   read-only tool first.

## Troubleshooting

### Slurm rejects the MIG request

Confirm the generated request uses:

```text
--gpus 1 --cpus-per-task=2 --mem=34G --partition=gpu-h200-mig
```

Requests above 34 GB per MIG GPU are rejected by the current scheduler policy.
Also check account, QoS, reservation, and node availability in the scheduler's
actual error message.

### The wrong model remains selected after changing partitions

Hard-refresh the OOD launch form. Inspect each model option's `data-partition`
and `data-option-for-...` attributes. Browser-cached form values can be
restored by OOD, but `form.js` should replace an incompatible selection during
initialization.

### The generated job uses old form values

Close or hard-refresh an already-open launch page before resubmitting. OOD may
also restore cacheable user selections. Inspect the session's
`user_defined_context.json` and `job_script_options.json` to see the exact form
values and Slurm request used for that launch.

### `Error: gzip is not supported by this browser`

The request reached llama.cpp without an acceptable gzip encoding. Verify that
the deployed `proxy.py` preserves `Accept-Encoding`, then start a new session
so the updated proxy is copied into the session directory.

### The session connects but chat requests return 404 or spin

Check the browser developer console and `proxy.log`. Root-absolute API requests
must be rewritten beneath `/rnode/<host>/<port>`. A stale proxy or WebUI change
that introduces a new root-absolute endpoint may require updating the shim.

### The model never becomes available

Inspect `llama-server.log` for an unreadable GGUF, invalid projector, GPU
visibility failure, unsupported model architecture, or out-of-memory error.
The proxy can be healthy and display the loading page even when llama-server
has failed; the watchdog should then terminate the proxy and copy the final
server log lines into OOD output.

### Port conflicts

Both ports are initially selected with OOD's `find_port`. The inference port is
rechecked before startup and falls back to scanning ports 15000–15099 if
needed.

### Stale bundled WebUI

The WebUI is embedded in the llama.cpp container. Updating this OOD repository
does not update that UI; rebuild or replace the SIF and launch a new session.
