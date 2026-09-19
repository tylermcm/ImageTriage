"""The single source of truth for "is this AI feature actually ready?".

Settings, the toolbar, the editor mask panes, culling and every worker launch
read their answer from here. Nothing may re-derive readiness from directory
existence — that is what let Settings report success while scene masking failed
with missing ``torch`` (see ``docs/ai_runtime_failure_map.md``, root causes C
and D).

Readiness for one capability is the conjunction of:

1. a managed runtime profile that can serve the requested device,
2. every required model bundle verified against the manifest, and
3. a probe that ran *inside that profile* and did real work — imported the
   modules, listed the ONNX providers, loaded the model, ran a tensor op.

Probe results are cached per (capability, device, profile) because they involve
a subprocess. ``invalidate`` clears the cache after any install or repair.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

from .ai_env import AIRuntimeUnavailable, RuntimeSelection, build_worker_env, resolve_device, select_runtime
from .ai_manifest import (
    CAPABILITIES,
    DEFAULT_CAPABILITY_ORDER,
    MODEL_BUNDLES,
    Capability,
    unpinned_bundles,
    unverified_bundles,
)
from .ai_model_store import (
    BundleStatus,
    ModelInstallError,
    bundle_install_dir,
    bundle_status,
    install_bundle,
    repair_bundle,
)
from .ai_probe import PROBE_PROTOCOL_VERSION
from .ai_paths import (
    is_windows_arm64,
    managed_ai_root,
    managed_logs_root,
    preflight_storage,
    process_architecture,
    redact,
    runtime_tag,
    volume_kind,
)

PROBE_TIMEOUT_SECONDS = 240
PROBE_CACHE_TTL_SECONDS = 900

# Validation stages, most-fundamental first. The first one that fails is the
# one reported to the user.
STAGE_RUNTIME = "runtime"
STAGE_PACKAGES = "packages"
STAGE_MODELS = "models"
STAGE_PROBE = "probe"
STAGE_READY = "ready"

_STAGE_LABELS = {
    STAGE_RUNTIME: "AI runtime",
    STAGE_PACKAGES: "installed packages",
    STAGE_MODELS: "model files",
    STAGE_PROBE: "runtime check",
    STAGE_READY: "ready",
}

# Failure categories mapped to the action the user should take.
_ACTIONS = {
    "runtime_missing": "setup",
    "runtime_incomplete": "setup",
    "runtime_corrupt": "repair",
    "profile_missing": "setup",
    # Package-level damage needs a fresh runtime generation, which only the
    # Settings installer produces. Offering "Repair" here sent users in a loop.
    "package_missing": "setup",
    "package_broken": "setup",
    "package_shadowed": "setup",
    "package_incompatible": "setup",
    "model_missing": "download",
    "model_unloadable": "repair",
    "bundle_missing": "download",
    "bundle_partial": "repair",
    "bundle_corrupt": "repair",
    "bundle_stale": "download",
    "provider_unavailable": "cpu",
    "provider_fallback": "cpu",
    "provider_broken": "cpu",
    "probe_timeout": "retry",
    "probe_error": "diagnostics",
    "protocol_mismatch": "restart",
}


@dataclass(frozen=True)
class CapabilityHealth:
    """Everything known about one capability's readiness, ready to display."""

    key: str
    name: str
    summary: str
    ready: bool
    stage: str
    requested_device: str = "auto"
    selected_device: str = ""
    profile_id: str = ""
    runtime_root: str = ""
    site_packages: str = ""
    category: str = ""
    message: str = ""
    detail: str = ""
    remediation: str = ""
    action: str = ""
    optional: bool = True
    module_versions: dict[str, str] = field(default_factory=dict)
    module_paths: dict[str, str] = field(default_factory=dict)
    providers_available: tuple[str, ...] = ()
    providers_active: tuple[str, ...] = ()
    torch_cuda_available: bool = False
    torch_device_name: str = ""
    bundles: tuple[BundleStatus, ...] = ()
    probe_ms: int = 0
    probe_level: str = "quick"
    inference_ran: bool = False
    # Set when model repair completed but the package runtime is still broken.
    runtime_repair_required: bool = False

    @property
    def stage_label(self) -> str:
        return _STAGE_LABELS.get(self.stage, self.stage)

    def headline(self) -> str:
        """The one line a dialog leads with — never a traceback."""
        if self.ready:
            device = self.selected_device or "cpu"
            return f"{self.name} is ready on {device.upper()}."
        return self.message or f"{self.name} is not ready."

    def to_dict(self) -> dict[str, object]:
        return {
            "capability": self.key,
            "name": self.name,
            "ready": self.ready,
            "stage": self.stage,
            "requested_device": self.requested_device,
            "selected_device": self.selected_device,
            "profile": self.profile_id,
            "runtime_root": redact(self.runtime_root),
            "site_packages": redact(self.site_packages),
            "category": self.category,
            "message": redact(self.message),
            "detail": redact(self.detail),
            "remediation": self.remediation,
            "action": self.action,
            "optional": self.optional,
            "modules": self.module_versions,
            "module_paths": {name: redact(path) for name, path in self.module_paths.items()},
            "providers_available": list(self.providers_available),
            "providers_active": list(self.providers_active),
            "torch_cuda_available": self.torch_cuda_available,
            "torch_device": self.torch_device_name,
            "bundles": [
                {
                    "key": item.key,
                    "state": item.state,
                    "revision": item.revision,
                    "dir": redact(str(item.install_dir)),
                    "problems": list(item.problems),
                }
                for item in self.bundles
            ],
            "probe_ms": self.probe_ms,
            "probe_level": self.probe_level,
            "inference_ran": self.inference_ran,
            "runtime_repair_required": self.runtime_repair_required,
        }


@dataclass(frozen=True)
class _CacheEntry:
    result: dict[str, object]
    stored_at: float


class AIHealthService:
    """Computes and caches capability readiness for the whole application."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._probe_cache: dict[tuple[str, str, str], _CacheEntry] = {}

    # -- cache ---------------------------------------------------------------

    def invalidate(self, capability_key: str | None = None) -> None:
        """Drop cached probe results after an install, repair or device change."""
        with self._lock:
            if capability_key is None:
                self._probe_cache.clear()
                return
            for key in [k for k in self._probe_cache if k[0] == capability_key]:
                self._probe_cache.pop(key, None)

    # -- readiness -----------------------------------------------------------

    def check(
        self,
        capability_key: str,
        *,
        device: str = "auto",
        use_cache: bool = True,
        deep_models: bool = False,
        thorough: bool = False,
        probe: bool = True,
    ) -> CapabilityHealth:
        """Readiness for one capability.

        ``probe=False`` answers from the runtime selection and the model bundle
        alone — the fast path used to gate a feature. ``probe=True`` also runs
        the out-of-process capability probe, and ``thorough=True`` makes that
        probe load model weights and run a forward pass.
        """
        capability = CAPABILITIES[capability_key]
        requested = resolve_device(device)

        try:
            selection = select_runtime(requested, require_torch=capability.requires_torch)
        except AIRuntimeUnavailable as exc:
            return self._failure(
                capability,
                stage=STAGE_RUNTIME,
                category=exc.category,
                message=str(exc),
                remediation=exc.remediation,
                requested_device=requested,
            )

        bundles = tuple(
            bundle_status(key, deep=deep_models) for key in capability.model_bundles
        )
        broken = tuple(item for item in bundles if not item.is_ready)
        if broken:
            first = broken[0]
            return self._failure(
                capability,
                stage=STAGE_MODELS,
                category=f"bundle_{first.state}",
                message=first.describe(),
                detail="; ".join(first.problems),
                remediation=(
                    f"Download {first.name} from Settings."
                    if first.state == "missing"
                    else f"Use Repair AI to reinstall {first.name}."
                ),
                requested_device=requested,
                selection=selection,
                bundles=bundles,
            )

        if not probe:
            return CapabilityHealth(
                key=capability.key,
                name=capability.name,
                summary=capability.summary,
                ready=True,
                stage=STAGE_READY,
                requested_device=requested,
                selected_device=selection.device,
                profile_id=selection.profile_id,
                runtime_root=str(selection.root),
                site_packages=str(selection.primary_site_packages),
                optional=capability.optional,
                bundles=bundles,
                probe_level="none",
            )

        payload = self._probe(
            capability,
            selection,
            requested,
            use_cache=use_cache,
            level="full" if thorough else "quick",
        )
        if not payload.get("ok"):
            category = str(payload.get("category") or "probe_error")
            return self._failure(
                capability,
                stage=STAGE_PROBE if category != "package_missing" else STAGE_PACKAGES,
                category=category,
                message=str(payload.get("message") or f"{capability.name} failed its runtime check."),
                detail=str(payload.get("detail") or ""),
                remediation=self._remediation(capability, category),
                requested_device=requested,
                selection=selection,
                bundles=bundles,
                payload=payload,
            )

        return CapabilityHealth(
            key=capability.key,
            name=capability.name,
            summary=capability.summary,
            ready=True,
            stage=STAGE_READY,
            requested_device=requested,
            selected_device=str(payload.get("selected_device") or selection.device),
            profile_id=selection.profile_id,
            runtime_root=str(selection.root),
            site_packages=str(selection.primary_site_packages),
            optional=capability.optional,
            module_versions=dict(payload.get("modules") or {}),
            module_paths=dict(payload.get("module_paths") or {}),
            providers_available=tuple(payload.get("providers_available") or ()),
            providers_active=tuple(payload.get("providers_active") or ()),
            torch_cuda_available=bool(payload.get("torch_cuda_available")),
            torch_device_name=str(payload.get("torch_device_name") or ""),
            bundles=bundles,
            probe_ms=int(payload.get("duration_ms") or 0),
            probe_level=str(payload.get("probe_level") or "quick"),
            inference_ran=bool(payload.get("inference_ran")),
        )

    def check_all(
        self,
        keys: Iterable[str] | None = None,
        *,
        device: str = "auto",
        use_cache: bool = True,
        deep_models: bool = False,
        thorough: bool = False,
        probe: bool = True,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, CapabilityHealth]:
        ordered = tuple(keys) if keys is not None else DEFAULT_CAPABILITY_ORDER
        results: dict[str, CapabilityHealth] = {}
        for index, key in enumerate(ordered):
            if progress_callback is not None:
                progress_callback(CAPABILITIES[key].name, index, len(ordered))
            results[key] = self.check(
                key,
                device=device,
                use_cache=use_cache,
                deep_models=deep_models,
                thorough=thorough,
                probe=probe,
            )
        return results

    # -- repair --------------------------------------------------------------

    def repair(
        self,
        capability_key: str,
        *,
        device: str = "auto",
        progress_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> CapabilityHealth:
        """Reinstall whatever this capability is missing, then re-check it.

        Repair covers model bundles directly. A package-level failure needs a
        fresh runtime generation, which only the Settings installer can produce;
        rather than reporting "use Repair" a second time, the result carries
        ``action == "setup"`` and ``runtime_repair_required`` so the caller can
        run the installer. See ``repair_all``.
        """
        capability = CAPABILITIES[capability_key]
        for bundle_key in capability.model_bundles:
            status = bundle_status(bundle_key, deep=True)
            if status.is_ready:
                continue
            if progress_callback is not None:
                progress_callback(f"Repairing {status.name}...")
            try:
                repair_bundle(
                    bundle_key,
                    progress_callback=(
                        (lambda name, done, total: progress_callback(  # type: ignore[misc]
                            f"Downloading {Path(name).name}"
                            + (f" — {done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else "")
                        ))
                        if progress_callback is not None
                        else None
                    ),
                    cancel_check=cancel_check,
                )
            except ModelInstallError as exc:
                self.invalidate(capability_key)
                return self._failure(
                    capability,
                    stage=STAGE_MODELS,
                    category=f"bundle_{exc.category}",
                    message=str(exc),
                    detail=exc.detail,
                    remediation=_install_remediation(exc.category),
                    requested_device=resolve_device(device),
                )
        self.invalidate(capability_key)
        health = self.check(
            capability_key, device=device, use_cache=False, deep_models=False, thorough=True
        )
        if health.ready or not _needs_runtime_reinstall(health.category):
            return health
        # Model repair cannot fix a broken package runtime. Say so, and point at
        # the operation that can, instead of looping the user back to Repair.
        return replace(
            health,
            action="setup",
            runtime_repair_required=True,
            remediation=(
                "The managed AI runtime packages are damaged. Run Set Up AI in "
                "Settings to reinstall them; your downloaded models are kept."
            ),
        )

    def install_missing_bundles(
        self,
        capability_keys: Iterable[str],
        *,
        progress_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[str, ...]:
        """Download every model bundle the given capabilities need.

        Returns the bundle keys that were installed. Already-verified bundles
        are skipped, so this is safe to run after a partial setup.
        """
        wanted: list[str] = []
        for key in capability_keys:
            for bundle_key in CAPABILITIES[key].model_bundles:
                if bundle_key not in wanted:
                    wanted.append(bundle_key)

        installed: list[str] = []
        for bundle_key in wanted:
            if cancel_check is not None and cancel_check():
                break
            if bundle_status(bundle_key).is_ready:
                continue
            name = MODEL_BUNDLES[bundle_key].name
            if progress_callback is not None:
                progress_callback(f"Downloading {name}...")

            def report(filename: str, done: int, total: int, _name: str = name) -> None:
                if progress_callback is None:
                    return
                label = Path(filename).name
                if total > 0:
                    progress_callback(
                        f"{_name}: {label} {done / 1e6:.0f} / {total / 1e6:.0f} MB"
                    )
                else:
                    progress_callback(f"{_name}: {label}...")

            install_bundle(bundle_key, progress_callback=report, cancel_check=cancel_check)
            installed.append(bundle_key)
        if installed:
            self.invalidate()
        return tuple(installed)

    def repair_all(
        self,
        capability_keys: Iterable[str],
        *,
        device: str = "auto",
        progress_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, CapabilityHealth], bool]:
        """Repair each capability. Returns the results and whether the managed
        package runtime still needs reinstalling by the Settings installer."""
        results: dict[str, CapabilityHealth] = {}
        runtime_required = False
        for key in capability_keys:
            if cancel_check is not None and cancel_check():
                break
            health = self.repair(
                key,
                device=device,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            results[key] = health
            runtime_required = runtime_required or health.runtime_repair_required
        return results, runtime_required

    # -- diagnostics ---------------------------------------------------------

    def diagnostics(
        self,
        results: dict[str, CapabilityHealth] | None = None,
        *,
        device: str = "auto",
    ) -> dict[str, object]:
        """A redacted support bundle: enough to diagnose a remote machine."""
        checks = results if results is not None else self.check_all(device=device)
        root = managed_ai_root()
        preflight = preflight_storage(root)
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "app_frozen": bool(getattr(sys, "frozen", False)),
            "executable": redact(sys.executable),
            "python": sys.version.split()[0],
            "runtime_tag": runtime_tag(),
            "architecture": process_architecture(),
            "unsupported_architecture": is_windows_arm64(),
            "managed_root": redact(str(root)),
            "managed_root_volume": volume_kind(root),
            "managed_root_writable": preflight.writable,
            "managed_root_free_gb": round(preflight.free_bytes / 1e9, 1),
            "long_paths_enabled": preflight.long_paths_enabled,
            "requested_device": resolve_device(device),
            "runtime_locks": _runtime_lock_report(),
            "unverified_model_bundles": list(unverified_bundles()),
            "unpinned_model_bundles": list(unpinned_bundles()),
            "capabilities": [health.to_dict() for health in checks.values()],
        }

    def write_diagnostics(
        self,
        results: dict[str, CapabilityHealth] | None = None,
        *,
        device: str = "auto",
    ) -> Path:
        payload = self.diagnostics(results, device=device)
        logs = managed_logs_root()
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"ai-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def diagnostics_text(
        self,
        results: dict[str, CapabilityHealth] | None = None,
        *,
        device: str = "auto",
    ) -> str:
        """The same content as a paste-ready block for a support message."""
        payload = self.diagnostics(results, device=device)
        lines = [
            "Image Triage AI diagnostics",
            f"  generated       {payload['generated_at']}",
            f"  python          {payload['python']} ({payload['runtime_tag']})",
            f"  frozen          {payload['app_frozen']}",
            f"  managed root    {payload['managed_root']} "
            f"[{payload['managed_root_volume']}, {payload['managed_root_free_gb']} GB free]",
            f"  requested dev   {payload['requested_device']}",
            "",
        ]
        for item in payload["capabilities"]:  # type: ignore[index]
            state = "READY" if item["ready"] else f"FAILED at {item['stage']}"
            lines.append(f"  [{state}] {item['name']}")
            if item["ready"]:
                providers = ", ".join(item["providers_active"] or item["providers_available"]) or "-"
                lines.append(
                    f"      device={item['selected_device']} profile={item['profile']} providers={providers}"
                )
            else:
                lines.append(f"      {item['category']}: {item['message']}")
                if item["detail"]:
                    lines.append(f"      detail: {item['detail']}")
                lines.append(f"      action: {item['remediation']}")
        unlocked = [
            item["variant"] for item in payload["runtime_locks"] if not item["locked"]  # type: ignore[index]
        ]
        if unlocked:
            lines.append("")
            lines.append(
                "  runtime installed WITHOUT a pinned lock for: " + ", ".join(unlocked)
            )
        if payload["unverified_model_bundles"]:
            lines.append("")
            lines.append(
                "  model bundles without a published hash: "
                + ", ".join(payload["unverified_model_bundles"])  # type: ignore[arg-type]
            )
        return "\n".join(lines)

    # -- internals -----------------------------------------------------------

    def _probe(
        self,
        capability: Capability,
        selection: RuntimeSelection,
        requested_device: str,
        *,
        use_cache: bool,
        level: str = "quick",
    ) -> dict[str, object]:
        # Probe the device the workers will actually be pinned to. Passing the
        # original "auto" let a GPU profile pass via CPU fallback and then fail
        # once a real worker was forced onto CUDA.
        del requested_device
        probe_device = selection.device
        cache_key = (capability.key, probe_device, selection.profile_id, level)
        if use_cache:
            with self._lock:
                entry = self._probe_cache.get(cache_key)
            if entry is not None and time.monotonic() - entry.stored_at < PROBE_CACHE_TTL_SECONDS:
                return entry.result

        payload = self._run_probe_subprocess(capability, selection, probe_device, level=level)
        with self._lock:
            self._probe_cache[cache_key] = _CacheEntry(payload, time.monotonic())
        return payload

    def _run_probe_subprocess(
        self,
        capability: Capability,
        selection: RuntimeSelection,
        requested_device: str,
        *,
        level: str = "quick",
    ) -> dict[str, object]:
        command = _probe_command(capability, selection, requested_device, level=level)
        env = build_worker_env(selection, metrics_enabled=False)
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                env=env,
                cwd=str(selection.root),
                **_no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "category": "probe_timeout",
                "message": (
                    f"The {capability.name} check did not finish within "
                    f"{PROBE_TIMEOUT_SECONDS} seconds."
                ),
                "detail": "The AI runtime may be loading from a slow or network drive.",
            }
        except OSError as exc:
            return {
                "ok": False,
                "category": "probe_error",
                "message": f"Image Triage could not start the {capability.name} check.",
                "detail": redact(str(exc)),
            }

        payload = _parse_probe_output(process.stdout)
        if payload is not None:
            mismatch = _probe_identity_problem(
                payload,
                capability=capability,
                selection=selection,
                requested_device=requested_device,
                returncode=process.returncode,
            )
            if mismatch is None:
                return payload
            return {
                "ok": False,
                "category": "probe_error",
                "message": f"The {capability.name} check returned an unusable result.",
                "detail": mismatch,
            }
        tail = "\n".join((process.stderr or process.stdout or "").strip().splitlines()[-6:])
        return {
            "ok": False,
            "category": "probe_error",
            "message": f"The {capability.name} check ended without a result.",
            "detail": redact(tail) or f"exit code {process.returncode}",
        }

    def _failure(
        self,
        capability: Capability,
        *,
        stage: str,
        category: str,
        message: str,
        remediation: str,
        requested_device: str,
        detail: str = "",
        selection: RuntimeSelection | None = None,
        bundles: tuple[BundleStatus, ...] = (),
        payload: dict[str, object] | None = None,
    ) -> CapabilityHealth:
        data = payload or {}
        return CapabilityHealth(
            key=capability.key,
            name=capability.name,
            summary=capability.summary,
            ready=False,
            stage=stage,
            requested_device=requested_device,
            selected_device=str(data.get("selected_device") or (selection.device if selection else "")),
            profile_id=selection.profile_id if selection else "",
            runtime_root=str(selection.root) if selection else "",
            site_packages=str(selection.primary_site_packages) if selection else "",
            category=category,
            message=message,
            detail=detail,
            remediation=remediation,
            action=_ACTIONS.get(category, "diagnostics"),
            optional=capability.optional,
            module_versions=dict(data.get("modules") or {}),
            module_paths=dict(data.get("module_paths") or {}),
            providers_available=tuple(data.get("providers_available") or ()),
            providers_active=tuple(data.get("providers_active") or ()),
            torch_cuda_available=bool(data.get("torch_cuda_available")),
            torch_device_name=str(data.get("torch_device_name") or ""),
            bundles=bundles,
            probe_ms=int(data.get("duration_ms") or 0),
        )

    @staticmethod
    def _remediation(capability: Capability, category: str) -> str:
        if category in {"provider_unavailable", "provider_fallback", "provider_broken"}:
            return (
                "Switch this feature to CPU, or update the NVIDIA driver and "
                "reinstall the GPU runtime from Settings."
            )
        if category == "package_shadowed":
            return (
                "Another Python installation is shadowing the managed runtime. "
                "Use Repair AI, and remove PYTHONPATH from your system environment."
            )
        if category.startswith("package_"):
            # Repair AI cannot rebuild the package runtime; Set Up AI can.
            return (
                "Run Set Up AI in Settings to reinstall the AI runtime packages. "
                "Your downloaded models are kept."
            )
        if category.startswith("model_"):
            return f"Use Repair AI to re-download the {capability.name} model."
        return capability.remediation


_RUNTIME_REINSTALL_CATEGORIES = frozenset(
    {
        "package_missing",
        "package_broken",
        "package_shadowed",
        "package_incompatible",
        "runtime_corrupt",
        "runtime_incomplete",
    }
)


def _needs_runtime_reinstall(category: str) -> bool:
    return category in _RUNTIME_REINSTALL_CATEGORIES


def _runtime_lock_report() -> list[dict[str, object]]:
    """Whether each installed variant came from a pinned, hash-verified lock."""
    from .ai_runtime_packages import AI_RUNTIME_VARIANTS, lock_status

    return [
        {"variant": variant, **lock_status(variant)}
        for variant in AI_RUNTIME_VARIANTS
    ]


def _install_remediation(category: str) -> str:
    return {
        "disk": "Free up disk space and try again.",
        "network": "Check the network connection and try again.",
        "dns": "Check the network connection and try again.",
        "certificate": (
            "A proxy is inspecting HTTPS traffic. Ask IT to allow huggingface.co, "
            "or install on a different network."
        ),
        "intercepted": "A proxy or captive portal blocked the download. Try a different network.",
        "auth": "The model host refused the download. Try again later.",
        "rate_limit": "The model host is rate limiting. Wait a few minutes and try again.",
        "not_found": "This build's pinned model revision is no longer published. Update Image Triage.",
        "locked": "Close any running AI operation, then try Repair AI again.",
        "hash_mismatch": "The downloaded file did not match its published hash. Try again on a different network.",
        "truncated": "The download was cut short. Try again.",
    }.get(category, "Open Settings and run Repair AI.")


def _probe_command(
    capability: Capability,
    selection: RuntimeSelection,
    requested_device: str,
    *,
    level: str = "quick",
) -> list[str]:
    """Launch the probe through the *same* frozen helper the app ships."""
    if getattr(sys, "frozen", False):
        runtime_root = Path(sys.executable).resolve().parent
        installer = runtime_root / ("ai_runtime_installer.exe" if os.name == "nt" else "ai_runtime_installer")
        command = [str(installer), "probe"]
    else:
        installer = Path(__file__).resolve().parents[1] / "packaging" / "ai_runtime_installer.py"
        command = [sys.executable, str(installer), "probe"]
    command.extend(
        [
            "--capability",
            capability.key,
            "--site-packages",
            str(selection.primary_site_packages),
            "--device",
            requested_device,
            "--profile",
            selection.profile_id,
            "--level",
            level,
        ]
    )
    for bundle_key in capability.model_bundles:
        command.extend(["--model-dir", f"{bundle_key}={bundle_install_dir(bundle_key)}"])
    return command


def _parse_probe_output(stdout: str) -> dict[str, object] | None:
    """Read the probe's JSON line, tolerating library chatter around it."""
    for line in reversed((stdout or "").splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "capability" in payload:
            return payload
    return None


def _probe_identity_problem(
    payload: dict[str, object],
    *,
    capability: Capability,
    selection: RuntimeSelection,
    requested_device: str,
    returncode: int,
) -> str | None:
    """Reject a result that did not come from *this* probe invocation.

    Without this a stale or mismatched helper — an executable left over from a
    previous application version, or a probe that silently answered for a
    different profile — is accepted as proof of health.
    """
    protocol = payload.get("protocol_version")
    if protocol != PROBE_PROTOCOL_VERSION:
        return (
            f"the AI helper speaks probe protocol {protocol!r}, expected "
            f"{PROBE_PROTOCOL_VERSION}. Restart Image Triage."
        )
    if payload.get("capability") != capability.key:
        return f"the helper answered for {payload.get('capability')!r}, not {capability.key!r}"
    reported_profile = str(payload.get("profile") or "")
    if reported_profile and reported_profile != selection.profile_id:
        return (
            f"the helper loaded profile {reported_profile!r} but "
            f"{selection.profile_id!r} was selected"
        )
    reported_site = str(payload.get("site_packages") or "")
    if reported_site and Path(reported_site) != selection.primary_site_packages:
        return "the helper ran against a different managed runtime directory"
    if str(payload.get("requested_device") or "") != requested_device:
        return (
            f"the helper was asked for {payload.get('requested_device')!r} but "
            f"{requested_device!r} was selected"
        )
    if bool(payload.get("ok")) and returncode != 0:
        return f"the helper reported success but exited with code {returncode}"
    return None


def _no_window_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


_SERVICE: AIHealthService | None = None
_SERVICE_LOCK = threading.Lock()


def ai_health() -> AIHealthService:
    """The process-wide health service."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = AIHealthService()
        return _SERVICE


def invalidate_ai_health(capability_key: str | None = None) -> None:
    ai_health().invalidate(capability_key)


def require_capability(
    capability_key: str,
    *,
    device: str = "auto",
    probe: bool = False,
) -> CapabilityHealth:
    """Raise with an actionable message unless the capability is ready.

    Defaults to the fast gate: the caller is normally about to start the real
    worker, which produces its own captured failure if the packages are broken.
    Pass ``probe=True`` where the answer itself is the product (Demo Ready).
    """
    health = ai_health().check(capability_key, device=device, probe=probe)
    if health.ready:
        return health
    raise AIRuntimeUnavailable(
        health.headline(),
        category=health.category or "unavailable",
        remediation=health.remediation,
    )


__all__ = [
    "AIHealthService",
    "CapabilityHealth",
    "PROBE_TIMEOUT_SECONDS",
    "STAGE_MODELS",
    "STAGE_PACKAGES",
    "STAGE_PROBE",
    "STAGE_READY",
    "STAGE_RUNTIME",
    "ai_health",
    "invalidate_ai_health",
    "require_capability",
]
