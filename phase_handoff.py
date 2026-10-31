"""Create a phase-only session after the frequency subsystem has finished.

No hardware is opened here. The caller supplies acknowledged sine settings
and subsequently drives step/retime_pending/applied on the returned controller.
"""

from phase_board.phase_control import PhaseController, Signal, Options


def start_after_frequency(
    source_frequency_hz: float,
    *,
    actual_signal: Signal,
    command_id: str,
    applied_at_s: float,
    continuous_phase: bool = False,
    mode: str = "circle",
    target_height_div: int | None = None,
    options: Options | None = None,
    output_command_frequency_hz: float | None = None,
):
    """Return (controller, first decision) using confirmed frequency and ACK.

    source_frequency_hz is the confirmed input frequency, never a rounded
    estimate. actual_signal describes the configured sine output register state;
    output_command_frequency_hz specifies a calibrated UART frequency when
    different from the nominal physical target. Infinity first doubles the
    source target and then calibrates that output target. Phase is a positive
    lead register offset. Hardware lag values must be negated modulo 360.
    continuous_phase may be true only for a verified accumulator-preserving
    hardware path. Sending a waveform command or receiving ACK alone does not
    establish this capability or its effective application timestamp.
    """
    controller = PhaseController(
        source_frequency_hz,
        mode=mode,
        options=options,
        target_height_div=target_height_div,
        continuous_phase=continuous_phase,
        output_command_frequency_hz=output_command_frequency_hz,
    )
    return controller, controller.start(actual_signal, command_id, applied_at_s)
