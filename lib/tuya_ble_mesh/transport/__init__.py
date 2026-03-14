"""Transport layer for mesh command dispatch.

Provides:
- CommandRequest: Structured command request with correlation tracking
- CommandResult: Command execution result with status and metrics
- AsyncCommandDispatcher: Priority queue-based command dispatcher
- CorrelationEngine: Request/response matching
- TransportMetrics: Lightweight metrics tracking
- RetryPolicy: Configurable retry behavior

Example usage:

    from tuya_ble_mesh.transport import (
        AsyncCommandDispatcher,
        CommandRequest,
        RetryPolicy,
    )

    # Create dispatcher
    dispatcher = AsyncCommandDispatcher(
        send_callback=my_send_function,
        next_sequence_callback=my_sequence_generator,
        per_device_limit=3,
        total_limit=32,
    )
    dispatcher.start()

    # Enqueue a command
    request = CommandRequest(
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
        params=b"\\x01\\x00",
        expected_response_opcode=0xDC,
        ttl=5.0,
        retry_policy=RetryPolicy(max_retries=3, backoff_base=0.5),
        context="light.turn_on",
        priority=1,
    )

    result_future = await dispatcher.enqueue(request)
    result = await result_future

    if result.is_successful():
        print(f"Success in {result.latency_ms}ms")
    else:
        print(f"Failed: {result.status}")

    # Check metrics
    metrics = dispatcher.metrics
    print(f"Success rate: {metrics.success_rate():.1%}")
    print(f"p50 latency: {metrics.p50:.1f}ms")
"""

from tuya_ble_mesh.transport.correlation import CorrelationEngine, CorrelationKey
from tuya_ble_mesh.transport.dispatcher import AsyncCommandDispatcher
from tuya_ble_mesh.transport.metrics import OpcodeStats, TransportMetrics
from tuya_ble_mesh.transport.request import CommandRequest, RetryPolicy
from tuya_ble_mesh.transport.result import CommandResult

__all__ = [
    "CommandRequest",
    "CommandResult",
    "RetryPolicy",
    "AsyncCommandDispatcher",
    "CorrelationEngine",
    "CorrelationKey",
    "TransportMetrics",
    "OpcodeStats",
]
