"""SIG Mesh light (Generic OnOff + Light Lightness / CTL / HSL) over GATT Proxy.

Provides ``SIGMeshLight`` — a ``SIGMeshDevice`` that drives the standard
Bluetooth Mesh lighting models used by Tuya SIG Mesh bulbs (e.g. RGBCW
category 0x1015/0x5115): white via Light CTL, colour via Light HSL,
brightness via Light Lightness, power via Generic OnOff.

Also provides ``configure_sig_node`` — post-provisioning node configuration
(AppKey Add, Composition Data Get, Model App Bind for every server model,
optional status publication).

Values are native mesh units: lightness/hue/saturation 0..65535, colour
temperature in Kelvin. Conversion to Home Assistant scales lives in the
integration, not here (Rule S1).

SECURITY: Key material is NEVER logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, TypeVar

from tuya_ble_mesh.exceptions import MalformedPacketError, MeshConnectionError, SIGMeshError
from tuya_ble_mesh.logging_context import MeshLogAdapter
from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
from tuya_ble_mesh.sig_mesh_protocol_codec import (
    CTL_TEMP_MAX_K,
    CTL_TEMP_MIN_K,
    FOUNDATION_MODELS,
    MODEL_GENERIC_ONOFF_SERVER,
    MODEL_LIGHT_CTL_SERVER,
    MODEL_LIGHT_CTL_TEMP_SERVER,
    MODEL_LIGHT_HSL_SERVER,
    MODEL_LIGHT_LIGHTNESS_SERVER,
    OP_GENERIC_ONOFF_STATUS,
    OP_LIGHT_CTL_STATUS,
    OP_LIGHT_CTL_TEMP_RANGE_STATUS,
    OP_LIGHT_CTL_TEMP_STATUS,
    OP_LIGHT_HSL_STATUS,
    OP_LIGHT_LIGHTNESS_STATUS,
    CompositionData,
    generic_onoff_get,
    generic_onoff_set,
    light_ctl_get,
    light_ctl_set,
    light_ctl_temp_range_get,
    light_ctl_temperature_set,
    light_hsl_get,
    light_hsl_set,
    light_lightness_set,
    parse_composition_data,
    parse_ctl_status,
    parse_ctl_temp_range_status,
    parse_ctl_temperature_status,
    parse_hsl_status,
    parse_lightness_status,
)

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# Default CTL temperature range when the device doesn't report one (Tuya RGBCW)
DEFAULT_TEMP_MIN_K = 2700
DEFAULT_TEMP_MAX_K = 6500

# Seconds to wait for a model status after an acknowledged Set
_SET_STATUS_TIMEOUT = 3.0

# Pause between consecutive config messages (device-side processing time)
_CONFIG_STEP_DELAY = 0.3
# Reconnects allowed per configuration step when the proxy link drops
_CONFIG_RECONNECT_ATTEMPTS = 2
_CONFIG_RECONNECT_TIMEOUT = 20.0

_T = TypeVar("_T")

MODE_WHITE = "white"
MODE_COLOR = "color"


@dataclass(frozen=True)
class SIGLightState:
    """Last known light state in native mesh units."""

    is_on: bool | None = None
    lightness: int | None = None  # Light Lightness actual (0..65535)
    temperature_k: int | None = None
    hue: int | None = None  # 0..65535
    saturation: int | None = None  # 0..65535
    mode: str | None = None  # MODE_WHITE | MODE_COLOR


LightStateCallback = Callable[[SIGLightState], Any]


class SIGMeshLight(SIGMeshDevice):  # type: ignore[misc]
    """SIG Mesh lighting node (OnOff / Lightness / CTL / HSL servers)."""

    # Defined in SIGMeshDevice.__init__ (base is untyped under mypy follow_imports=skip)
    _tid: int
    _target_addr: int
    _address: str

    def __init__(
        self,
        *args: Any,
        model_elements: dict[int, int] | None = None,
        temp_range_k: tuple[int, int] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize a SIG Mesh light.

        Args:
            *args: Positional args for ``SIGMeshDevice``.
            model_elements: SIG model ID -> element index (from Composition
                Data). Missing models default to the primary element.
            temp_range_k: (min, max) CTL temperature in Kelvin.
            **kwargs: Keyword args for ``SIGMeshDevice``.
        """
        super().__init__(*args, **kwargs)
        self._model_elements = dict(model_elements or {})
        # Without a device-reported range, Tuya firmware treats the mesh
        # temperature as a linear warm..cool slider over the full 800-20000 K
        # range, so map the displayed range onto it (and back on status).
        self._scale_temperature = temp_range_k is None
        lo, hi = temp_range_k or (DEFAULT_TEMP_MIN_K, DEFAULT_TEMP_MAX_K)
        self._temp_range_k: tuple[int, int] = (
            max(int(lo), int(CTL_TEMP_MIN_K)),
            min(int(hi), int(CTL_TEMP_MAX_K)),
        )
        self._light_state = SIGLightState()
        self._light_callbacks: list[LightStateCallback] = []

    # --- Properties ---

    @property
    def light_state(self) -> SIGLightState:
        """Return the last known light state."""
        return self._light_state

    @property
    def temp_range_k(self) -> tuple[int, int]:
        """Return the (min, max) colour temperature in Kelvin."""
        return self._temp_range_k

    def _to_mesh_temp(self, temperature_k: int) -> int:
        """Displayed Kelvin -> value sent to the device."""
        lo, hi = self._temp_range_k
        k: int = max(lo, min(int(temperature_k), hi))
        if not self._scale_temperature or hi <= lo:
            return k
        mesh_lo, mesh_hi = int(CTL_TEMP_MIN_K), int(CTL_TEMP_MAX_K)
        return round(mesh_lo + (k - lo) * (mesh_hi - mesh_lo) / (hi - lo))

    def _from_mesh_temp(self, mesh_temp: int) -> int:
        """Device-reported temperature -> displayed Kelvin."""
        if not self._scale_temperature:
            return int(mesh_temp)
        lo, hi = self._temp_range_k
        mesh_lo, mesh_hi = int(CTL_TEMP_MIN_K), int(CTL_TEMP_MAX_K)
        t = max(mesh_lo, min(int(mesh_temp), mesh_hi))
        return round(lo + (t - mesh_lo) * (hi - lo) / (mesh_hi - mesh_lo))

    def register_light_callback(self, callback: LightStateCallback) -> None:
        """Register a callback for light state changes."""
        self._light_callbacks.append(callback)

    def unregister_light_callback(self, callback: LightStateCallback) -> None:
        """Remove a previously registered light state callback."""
        self._light_callbacks.remove(callback)

    def _element_addr(self, model_id: int) -> int:
        return int(self._target_addr) + self._model_elements.get(model_id, 0)

    # --- Commands ---

    async def send_onoff(self, on: bool, transition_s: float | None = None) -> None:
        """Generic OnOff Set with an optional fade (seconds)."""
        await self._send_set(
            generic_onoff_set(on, self._take_tid(), transition_s=transition_s),
            MODEL_GENERIC_ONOFF_SERVER,
            OP_GENERIC_ONOFF_STATUS,
        )

    async def send_lightness(self, lightness: int, transition_s: float | None = None) -> None:
        """Set brightness via Light Lightness Set (0..65535)."""
        await self._send_set(
            light_lightness_set(
                self._clamp16(lightness), self._take_tid(), transition_s=transition_s
            ),
            MODEL_LIGHT_LIGHTNESS_SERVER,
            OP_LIGHT_LIGHTNESS_STATUS,
        )

    async def send_ctl(
        self, lightness: int, temperature_k: int, transition_s: float | None = None
    ) -> None:
        """Switch to white mode via Light CTL Set.

        Args:
            lightness: 0..65535.
            temperature_k: Kelvin, clamped to the device range.
        """
        temp = self._to_mesh_temp(temperature_k)
        self._light_state = replace(self._light_state, mode=MODE_WHITE)
        # CTL Set switches the light to white mode and sets lightness...
        await self._send_set(
            light_ctl_set(
                self._clamp16(lightness), temp, self._take_tid(), transition_s=transition_s
            ),
            MODEL_LIGHT_CTL_SERVER,
            OP_LIGHT_CTL_STATUS,
        )
        # ...but Tuya firmware only applies colour temperature from the
        # dedicated CTL Temperature Set (Tuya DP 4 <-> 0x8264).
        await self._send_set(
            light_ctl_temperature_set(temp, self._take_tid(), transition_s=transition_s),
            MODEL_LIGHT_CTL_TEMP_SERVER,
            OP_LIGHT_CTL_TEMP_STATUS,
        )

    async def send_temperature(self, temperature_k: int, transition_s: float | None = None) -> None:
        """Set white colour temperature via Light CTL Temperature Set (Kelvin)."""
        temp = self._to_mesh_temp(temperature_k)
        await self._send_set(
            light_ctl_temperature_set(temp, self._take_tid(), transition_s=transition_s),
            MODEL_LIGHT_CTL_TEMP_SERVER,
            OP_LIGHT_CTL_TEMP_STATUS,
        )

    async def send_hsl(
        self, lightness: int, hue: int, saturation: int, transition_s: float | None = None
    ) -> None:
        """Switch to colour mode via Light HSL Set (all 0..65535)."""
        self._light_state = replace(self._light_state, mode=MODE_COLOR)
        await self._send_set(
            light_hsl_set(
                self._clamp16(lightness),
                self._clamp16(hue),
                self._clamp16(saturation),
                self._take_tid(),
                transition_s=transition_s,
            ),
            MODEL_LIGHT_HSL_SERVER,
            OP_LIGHT_HSL_STATUS,
        )

    async def get_temperature_range(self) -> tuple[int, int]:
        """Query Light CTL Temperature Range; updates and returns the range."""
        params = await self._send_access(
            light_ctl_temp_range_get(),
            use_dev_key=False,
            dst=self._element_addr(MODEL_LIGHT_CTL_SERVER),
            expect_opcode=OP_LIGHT_CTL_TEMP_RANGE_STATUS,
            response_timeout=_SET_STATUS_TIMEOUT,
        )
        rng = parse_ctl_temp_range_status(params or b"")
        if rng.status == 0 and CTL_TEMP_MIN_K <= rng.min_k < rng.max_k <= CTL_TEMP_MAX_K:
            self._temp_range_k = (int(rng.min_k), int(rng.max_k))
            self._scale_temperature = False
        _LOGGER.info(
            "CTL temperature range for %s: %d-%d K (status=%d)",
            self._address,
            self._temp_range_k[0],
            self._temp_range_k[1],
            rng.status,
        )
        return self._temp_range_k

    async def refresh_state(self) -> None:
        """Request OnOff, CTL and HSL state (best effort, results via callbacks)."""
        for payload, model, opcode in (
            (generic_onoff_get(), MODEL_GENERIC_ONOFF_SERVER, OP_GENERIC_ONOFF_STATUS),
            (light_ctl_get(), MODEL_LIGHT_CTL_SERVER, OP_LIGHT_CTL_STATUS),
            (light_hsl_get(), MODEL_LIGHT_HSL_SERVER, OP_LIGHT_HSL_STATUS),
        ):
            try:
                await self._send_access(
                    payload,
                    use_dev_key=False,
                    dst=self._element_addr(model),
                    expect_opcode=opcode,
                    response_timeout=_SET_STATUS_TIMEOUT,
                )
            except SIGMeshError:
                _LOGGER.debug("State query 0x%04X unanswered by %s", opcode, self._address)

    # --- Internals ---

    def _take_tid(self) -> int:
        tid: int = int(self._tid)
        self._tid = (tid + 1) & 0xFF
        return tid

    @staticmethod
    def _clamp16(value: int) -> int:
        return max(0, min(int(value), 0xFFFF))

    async def _send_set(self, payload: bytes, model_id: int, status_opcode: int) -> None:
        """Send an acknowledged Set; a missing Status is logged, not raised.

        Write failures still raise so the coordinator can retry/reconnect.
        """
        try:
            await self._send_access(
                payload,
                use_dev_key=False,
                dst=self._element_addr(model_id),
                expect_opcode=status_opcode,
                response_timeout=_SET_STATUS_TIMEOUT,
            )
        except SIGMeshError as exc:
            if "Timeout" not in str(exc):
                raise
            _LOGGER.warning(
                "No status 0x%04X from %s after Set (command may still have applied)",
                status_opcode,
                self._address,
            )

    def _handle_model_status(self, src: int, opcode: int, params: bytes) -> bool:
        """Update light state from Lightness / CTL / HSL / OnOff status.

        On/off comes only from Generic OnOff: Tuya bulbs keep reporting the
        last non-zero lightness/colour while switched off.
        """
        state = self._light_state
        try:
            if opcode == OP_LIGHT_LIGHTNESS_STATUS:
                st = parse_lightness_status(params)
                state = replace(state, lightness=st.lightness)
            elif opcode == OP_LIGHT_CTL_STATUS:
                ctl = parse_ctl_status(params)
                mode = state.mode or MODE_WHITE
                state = replace(
                    state, temperature_k=self._from_mesh_temp(ctl.temperature_k), mode=mode
                )
                # Only the model driving the LEDs reports meaningful lightness:
                # Tuya reports 0 on the inactive one (CTL in colour mode, HSL in white).
                if mode == MODE_WHITE:
                    state = replace(state, lightness=ctl.lightness)
            elif opcode == OP_LIGHT_CTL_TEMP_STATUS:
                st_temp = parse_ctl_temperature_status(params)
                state = replace(state, temperature_k=self._from_mesh_temp(st_temp.temperature_k))
            elif opcode == OP_LIGHT_HSL_STATUS:
                hsl = parse_hsl_status(params)
                # Both models report state regardless of which one is driving
                # the LEDs, so the mode follows the last CTL/HSL command; only
                # infer it here when still unknown (saturation 0 == white).
                inferred = MODE_COLOR if hsl.saturation > 0 and hsl.lightness > 0 else MODE_WHITE
                mode = state.mode or inferred
                state = replace(state, hue=hsl.hue, saturation=hsl.saturation, mode=mode)
                if mode == MODE_COLOR:
                    state = replace(state, lightness=hsl.lightness)
            elif opcode == OP_GENERIC_ONOFF_STATUS and params:
                state = replace(state, is_on=bool(params[0]))
            else:
                return False
        except MalformedPacketError:
            _LOGGER.debug("Malformed light status 0x%04X from 0x%04X", opcode, src)
            return True

        _LOGGER.debug("Light status 0x%04X from 0x%04X -> %s", opcode, src, state)
        self._light_state = state
        for callback in list(self._light_callbacks):
            try:
                callback(state)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Light callback error", exc_info=True)
        # OnOff status also flows to the generic onoff callbacks
        return bool(opcode != OP_GENERIC_ONOFF_STATUS)


async def _with_reconnect(
    device: SIGMeshDevice,
    step: Callable[[], Awaitable[_T]],
    attempts: int = _CONFIG_RECONNECT_ATTEMPTS,
) -> _T:
    """Run a configuration step, reconnecting and retrying if the link drops.

    Errors raised while the device is still connected (e.g. a rejected or
    unanswered request) are re-raised unchanged.
    """
    for attempt in range(attempts + 1):
        if not device.is_connected:
            await device.connect(timeout=_CONFIG_RECONNECT_TIMEOUT, max_retries=3)
        try:
            return await step()
        except (SIGMeshError, MeshConnectionError):
            if device.is_connected or attempt >= attempts:
                raise
            _LOGGER.warning(
                "Link to %s lost during configuration — reconnecting (%d/%d)",
                device.address,
                attempt + 1,
                attempts,
            )
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class NodeConfigResult:
    """Outcome of ``configure_sig_node``."""

    composition: CompositionData | None
    bound_models: tuple[tuple[int, int], ...]  # (element index, model id)
    failed_models: tuple[tuple[int, int], ...]


async def configure_sig_node(
    device: SIGMeshDevice,
    app_key: bytes,
    *,
    fallback_models: tuple[int, ...] = (MODEL_GENERIC_ONOFF_SERVER,),
    bind_vendor_models: bool = True,
    publish_to: int | None = None,
) -> NodeConfigResult:
    """Configure a freshly provisioned node over an open proxy connection.

    1. Config AppKey Add (must succeed).
    2. Config Composition Data Get (page 0).
    3. Model App Bind for every non-foundation SIG model on every element,
       plus vendor models. If composition is unavailable, binds
       ``fallback_models`` on the primary element.
    4. Optionally, Model Publication Set for the primary OnOff/CTL/HSL servers
       so state changes are pushed to ``publish_to``.

    Args:
        device: Connected ``SIGMeshDevice`` (device key loaded).
        app_key: The application key to add.
        fallback_models: SIG models to bind when composition is unavailable.
        bind_vendor_models: Also bind vendor models (e.g. Tuya 0x07D0).
        publish_to: Unicast address to publish status to, or None.

    Returns:
        NodeConfigResult.

    Raises:
        SIGMeshError: If AppKey Add fails or times out.
        MeshConnectionError: If the link drops and cannot be re-established.
    """
    if not await _with_reconnect(device, lambda: device.send_config_appkey_add(app_key)):
        msg = "AppKey Add was rejected by the device"
        raise SIGMeshError(msg)
    await asyncio.sleep(_CONFIG_STEP_DELAY)

    composition: CompositionData | None = None
    try:
        params = await _with_reconnect(device, device.get_composition_data)
        composition = parse_composition_data(params)
    except (SIGMeshError, MalformedPacketError):
        _LOGGER.warning("Composition Data unavailable; binding fallback models", exc_info=True)

    primary = device._target_addr
    bound: list[tuple[int, int]] = []
    failed: list[tuple[int, int]] = []

    targets: list[tuple[int, int | tuple[int, int]]] = []
    if composition is not None and composition.elements:
        for element in composition.elements:
            for model in element.sig_models:
                if model not in FOUNDATION_MODELS:
                    targets.append((element.index, model))
            if bind_vendor_models:
                for vendor in element.vendor_models:
                    targets.append((element.index, vendor))
    else:
        targets = [(0, model) for model in fallback_models]

    for index, model_ref in targets:
        await asyncio.sleep(_CONFIG_STEP_DELAY)
        addr = primary + index
        try:
            if isinstance(model_ref, tuple):
                cid, mid = model_ref
                key = (index, (cid << 16) | mid)
                ok = await _with_reconnect(
                    device, partial(device.send_config_model_app_bind_vendor, addr, 0, cid, mid)
                )
            else:
                key = (index, model_ref)
                ok = await _with_reconnect(
                    device, partial(device.send_config_model_app_bind, addr, 0, model_ref)
                )
        except SIGMeshError:
            ok = False
            key = (index, model_ref if isinstance(model_ref, int) else model_ref[1])
        (bound if ok else failed).append(key)

    if publish_to is not None:
        for model in (MODEL_GENERIC_ONOFF_SERVER, MODEL_LIGHT_CTL_SERVER, MODEL_LIGHT_HSL_SERVER):
            index = composition.find_model(model) if composition is not None else 0
            if index is None:
                continue
            await asyncio.sleep(_CONFIG_STEP_DELAY)
            try:
                await _with_reconnect(
                    device,
                    partial(
                        device.send_config_model_pub_set, primary + index, publish_to, 0, model
                    ),
                )
            except SIGMeshError:
                _LOGGER.warning("Publication Set failed for model 0x%04X", model)

    _LOGGER.info(
        "Node 0x%04X configured: %d model(s) bound, %d failed",
        primary,
        len(bound),
        len(failed),
    )
    return NodeConfigResult(
        composition=composition, bound_models=tuple(bound), failed_models=tuple(failed)
    )


def model_elements_from_composition(composition: CompositionData) -> dict[int, int]:
    """Map each SIG model ID to the first element index that hosts it."""
    mapping: dict[int, int] = {}
    for element in composition.elements:
        for model in element.sig_models:
            mapping.setdefault(model, element.index)
    return mapping
