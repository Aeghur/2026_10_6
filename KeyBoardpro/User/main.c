#include "ti_msp_dl_config.h"

#include "fpga_capture.h"
#include "keypad_control.h"
#include "measurement_config.h"
#include "msp_uart_link.h"
#include "pc_link.h"
#include "task_control.h"

#include <stdbool.h>
#include <stdint.h>

static bool normalizeFrequency(uint32_t *frequencyMilliHz)
{
    const uint32_t minimumMilliHz = 100UL * 1000UL;
    const uint32_t maximumMilliHz = MEASUREMENT_MAX_FREE_RUN_HZ * 1000UL;

    /* Free-run commands are exact targets, not FFT-bin estimates. */
    return *frequencyMilliHz >= minimumMilliHz &&
           *frequencyMilliHz <= maximumMilliHz;
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

    /* Split before the extra six bits to avoid uint64 overflow at 210 kHz. */
    {
        const uint64_t numerator = (uint64_t) frequencyMilliHz * phaseTurn;
        *phaseStep = (uint32_t) (
            ((numerator / rateMilliHz) << 6) +
            (((numerator % rateMilliHz) << 6) + rateMilliHz / 2U) /
                rateMilliHz);
    }
    return true;
}

static uint32_t actualFrequencyMilliHz(uint32_t phaseStep)
{
    const uint64_t turn = 0x4000000000ULL; /* 2^38 */
    const uint64_t product = (uint64_t) phaseStep * MEASUREMENT_ADC_RATE_HZ;
    return (uint32_t) ((product / turn) * 1000ULL +
        ((product % turn) * 1000ULL + turn / 2U) / turn);
}

static void calculateDecayParameters(
    uint16_t decayMicroseconds, uint16_t *decayStart,
    uint32_t *decayScaleQ16)
{
    uint32_t width =
        ((uint32_t) decayMicroseconds * 65535UL + 5000UL) / 10000UL;
    if (width == 0U)
        width = 1U;
    *decayStart = (uint16_t) (65535UL - width);
    *decayScaleQ16 = (uint32_t)
        ((((uint64_t) 32767U << 16) + width / 2U) / width);
}

int main(void)
{
    uint16_t targetPhase = MEASUREMENT_DEFAULT_PHASE_CDEG;
    uint16_t requestedPhase;
    uint32_t targetFrequencyMilliHz = 0U;
    uint32_t requestedFrequencyMilliHz;
    uint8_t requestedAmplitudeCode = 0U;
    uint8_t requestedWaveform = 0U;
    uint16_t targetDecayMicroseconds = 1000U;
    uint16_t requestedDecayMicroseconds = 1000U;
    uint8_t targetMode = MEASUREMENT_MODE_DDS;
    uint8_t requestedMode;
    uint8_t targetSequence = 0U;
    uint8_t fpgaSequence = 0U;
    uint8_t doubleSequence = 0U;
    uint32_t activePhaseStep = 0xFFFFFFFFUL;
    uint32_t activePhaseLag = 0xFFFFFFFFUL;
    uint8_t activeMode = 0xFFU;
    uint16_t activeDecayStart = 0xFFFFU;
    uint32_t activeDecayScaleQ16 = 0xFFFFFFFFUL;
    bool controlChanged = true;
    bool targetAckPending = false;
    bool doubleRequestPending = false;
    uint8_t requestedTaskMode;
    TaskControlSnapshot taskSnapshot;

    SYSCFG_DL_init();
    MSP_UART_Link_Init();
    PC_Link_Init();
    Keypad_Control_Init(targetMode, targetPhase);
    Task_Control_Init(Keypad_Control_Millis());

    while (1) {
        if (Keypad_Control_Poll(&targetMode, &targetPhase))
            controlChanged = true;

        if (Keypad_Control_TakeTaskRequest(&requestedTaskMode)) {
            Task_Control_Start(
                requestedTaskMode, Keypad_Control_Millis());
            Task_Control_GetSnapshot(&taskSnapshot);
            Keypad_Control_UpdateTask(&taskSnapshot);
        }
        if (Task_Control_Poll(Keypad_Control_Millis())) {
            Task_Control_GetSnapshot(&taskSnapshot);
            Keypad_Control_UpdateTask(&taskSnapshot);
        }

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
                &requestedWaveform, &requestedDecayMicroseconds)) {
            targetFrequencyMilliHz = requestedFrequencyMilliHz;
            targetPhase = requestedPhase;
            targetDecayMicroseconds = requestedDecayMicroseconds;
            targetMode = (uint8_t) (
                MEASUREMENT_MODE_DDS | MEASUREMENT_MODE_FREE_RUN |
                (requestedAmplitudeCode << MEASUREMENT_MODE_AMPLITUDE_SHIFT) |
                (requestedWaveform == 1U || requestedWaveform == 2U ?
                 MEASUREMENT_MODE_WAVEFORM_SAW : 0U) |
                (requestedWaveform == 2U ?
                 MEASUREMENT_MODE_SAW_REVERSE : 0U) |
                (requestedWaveform == 3U ?
                 MEASUREMENT_MODE_WAVEFORM_DECAY : 0U));
            controlChanged = true;
            targetAckPending = true;
        }
        if (PC_Link_TakeDoubleRequest(&doubleSequence))
            doubleRequestPending = true;

        if (controlChanged) {
            uint32_t phaseStep;
            uint32_t phaseLag;
            uint16_t decayStart;
            uint32_t decayScaleQ16;
            FPGA_CaptureStatus fpgaStatus = FPGA_CAPTURE_OK;
            uint32_t commandFrequency =
                (targetMode & MEASUREMENT_MODE_FREE_RUN) != 0U ?
                targetFrequencyMilliHz : 0U;
            calculateDecayParameters(
                targetDecayMicroseconds, &decayStart, &decayScaleQ16);

            if (calculateDDSWords(
                    commandFrequency, targetPhase, &phaseStep, &phaseLag)) {
                /* A PC target command must always make a fresh round trip to
                 * the FPGA.  Otherwise an identical repeated command could
                 * be acknowledged from the local cache only. */
                if (targetAckPending ||
                    activeMode != targetMode ||
                    activePhaseStep != phaseStep ||
                    activePhaseLag != phaseLag ||
                    activeDecayStart != decayStart ||
                    activeDecayScaleQ16 != decayScaleQ16) {
                    fpgaSequence++;
                    fpgaStatus = FPGA_Capture_SetOutput(
                        fpgaSequence, targetMode, phaseStep, phaseLag,
                        decayStart, decayScaleQ16);
                    if (fpgaStatus == FPGA_CAPTURE_OK) {
                        activeMode = targetMode;
                        activePhaseStep = phaseStep;
                        activePhaseLag = phaseLag;
                        activeDecayStart = decayStart;
                        activeDecayScaleQ16 = decayScaleQ16;
                    }
                }
                if (targetAckPending) {
                    if (fpgaStatus == FPGA_CAPTURE_OK)
                        PC_Link_SendTargetAck(
                            targetSequence,
                            actualFrequencyMilliHz(phaseStep),
                            targetPhase, requestedAmplitudeCode, targetMode,
                            requestedDecayMicroseconds);
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

        if (doubleRequestPending) {
            FPGA_DoubleResult result;
            FPGA_CaptureStatus fpgaStatus;
            fpgaSequence++;
            fpgaStatus = FPGA_Capture_ArmDouble(fpgaSequence, &result);
            if (fpgaStatus != FPGA_CAPTURE_OK) {
                PC_Link_SendError(doubleSequence, (uint16_t) fpgaStatus);
            } else if (result.status != 0U) {
                /* FPGA status 1/2/3 maps to host BUSY/INVALID/TIMEOUT 5/6/7. */
                PC_Link_SendError(
                    doubleSequence, (uint16_t) (4U + result.status));
            } else if (result.newPhaseStep !=
                       (uint32_t) (result.oldPhaseStep << 1)) {
                PC_Link_SendError(
                    doubleSequence, (uint16_t) FPGA_CAPTURE_FORMAT_ERROR);
            } else {
                activePhaseStep = result.newPhaseStep;
                targetFrequencyMilliHz =
                    actualFrequencyMilliHz(result.newPhaseStep);
                targetMode |= MEASUREMENT_MODE_X2_MASK;
                activeMode = targetMode;
                PC_Link_SendDoubleAck(
                    doubleSequence, result.appliedSampleCounter,
                    result.oldPhaseStep, result.newPhaseStep,
                    result.commitPhase);
                Keypad_Control_RefreshDisplay(targetMode, targetPhase);
            }
            doubleRequestPending = false;
        }
    }
}
