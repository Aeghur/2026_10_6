#include "ti_msp_dl_config.h"

#include "fpga_capture.h"
#include "keypad_control.h"
#include "measurement_config.h"
#include "msp_uart_link.h"
#include "pc_link.h"

#include <stdbool.h>
#include <stdint.h>

static bool normalizeFrequency(uint32_t *frequencyMilliHz)
{
    const uint32_t minimumMilliHz = MEASUREMENT_MIN_SPECTRUM_HZ * 1000UL;
    const uint32_t maximumMilliHz = MEASUREMENT_MAX_SPECTRUM_HZ * 1000UL;

    if (*frequencyMilliHz < minimumMilliHz) {
        if (minimumMilliHz - *frequencyMilliHz >
            MEASUREMENT_EDGE_TOLERANCE_MILLIHZ)
            return false;
        *frequencyMilliHz = minimumMilliHz;
    } else if (*frequencyMilliHz > maximumMilliHz) {
        if (*frequencyMilliHz - maximumMilliHz >
            MEASUREMENT_EDGE_TOLERANCE_MILLIHZ)
            return false;
        *frequencyMilliHz = maximumMilliHz;
    }
    return true;
}

static bool calculateDDSWords(
    uint32_t frequencyMilliHz, uint16_t phaseCentiDegrees,
    uint32_t *phaseStep, uint32_t *phaseLag)
{
    const uint64_t phaseTurn = 0x100000000ULL;
    const uint64_t rateMilliHz =
        (uint64_t) MEASUREMENT_ADC_RATE_HZ * 1000ULL;

    *phaseStep = 0U;
    *phaseLag = (uint32_t)
        (((uint64_t) phaseCentiDegrees * phaseTurn + 18000U) / 36000U);

    /* Live-lock DDS measures its own phase step inside the FPGA. */
    if (frequencyMilliHz == 0U)
        return true;
    if (!normalizeFrequency(&frequencyMilliHz))
        return false;

    *phaseStep = (uint32_t)
        (((uint64_t) frequencyMilliHz * phaseTurn + rateMilliHz / 2U) /
         rateMilliHz);
    return true;
}

int main(void)
{
    uint16_t targetPhase = MEASUREMENT_DEFAULT_PHASE_CDEG;
    uint16_t requestedPhase;
    uint32_t targetFrequencyMilliHz = 0U;
    uint32_t requestedFrequencyMilliHz;
    uint8_t requestedAmplitudeCode = 0U;
    uint8_t requestedWaveform = 0U;
    uint8_t targetMode = MEASUREMENT_MODE_DDS;
    uint8_t requestedMode;
    uint8_t targetSequence = 0U;
    uint8_t fpgaSequence = 0U;
    uint32_t activePhaseStep = 0xFFFFFFFFUL;
    uint32_t activePhaseLag = 0xFFFFFFFFUL;
    uint8_t activeMode = 0xFFU;
    bool controlChanged = true;
    bool targetAckPending = false;

    SYSCFG_DL_init();
    MSP_UART_Link_Init();
    PC_Link_Init();
    Keypad_Control_Init(targetMode, targetPhase);

    while (1) {
        if (Keypad_Control_Poll(&targetMode, &targetPhase))
            controlChanged = true;

        if (PC_Link_TakePhaseRequest(&requestedPhase)) {
            targetPhase = requestedPhase;
            controlChanged = true;
        }
        if (PC_Link_TakeModeRequest(&requestedMode)) {
            targetMode = requestedMode;
            controlChanged = true;
        }
        if (PC_Link_TakeTargetRequest(
                &targetSequence, &requestedFrequencyMilliHz,
                &requestedPhase, &requestedAmplitudeCode,
                &requestedWaveform)) {
            targetFrequencyMilliHz = requestedFrequencyMilliHz;
            targetPhase = requestedPhase;
            targetMode = (uint8_t) (
                MEASUREMENT_MODE_DDS | MEASUREMENT_MODE_FREE_RUN |
                (requestedAmplitudeCode << MEASUREMENT_MODE_AMPLITUDE_SHIFT) |
                (requestedWaveform != 0U ?
                 MEASUREMENT_MODE_WAVEFORM_SAW : 0U));
            controlChanged = true;
            targetAckPending = true;
        }

        if (controlChanged) {
            uint32_t phaseStep;
            uint32_t phaseLag;
            FPGA_CaptureStatus fpgaStatus = FPGA_CAPTURE_OK;
            uint32_t commandFrequency =
                (targetMode & MEASUREMENT_MODE_FREE_RUN) != 0U ?
                targetFrequencyMilliHz : 0U;

            if (calculateDDSWords(
                    commandFrequency, targetPhase, &phaseStep, &phaseLag)) {
                /* A PC target command must always make a fresh round trip to
                 * the FPGA.  Otherwise an identical repeated command could
                 * be acknowledged from the local cache only. */
                if (targetAckPending ||
                    activeMode != targetMode ||
                    activePhaseStep != phaseStep ||
                    activePhaseLag != phaseLag) {
                    fpgaSequence++;
                    fpgaStatus = FPGA_Capture_SetOutput(
                        fpgaSequence, targetMode, phaseStep, phaseLag);
                    if (fpgaStatus == FPGA_CAPTURE_OK) {
                        activeMode = targetMode;
                        activePhaseStep = phaseStep;
                        activePhaseLag = phaseLag;
                    }
                }
                if (targetAckPending) {
                    if (fpgaStatus == FPGA_CAPTURE_OK)
                        PC_Link_SendTargetAck(
                            targetSequence, targetFrequencyMilliHz,
                            targetPhase, requestedAmplitudeCode, targetMode);
                    else
                        PC_Link_SendError(
                            targetSequence, (uint16_t) fpgaStatus);
                    targetAckPending = false;
                }
            } else if (targetAckPending) {
                PC_Link_SendError(targetSequence, 4U);
                targetAckPending = false;
            }
            Keypad_Control_RefreshDisplay(targetMode, targetPhase);
            controlChanged = false;
        }
    }
}
