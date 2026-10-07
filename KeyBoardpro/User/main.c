#include "ti_msp_dl_config.h"

#include "fft_analyzer.h"
#include "fpga_capture.h"
#include "measurement_config.h"
#include "msp_uart_link.h"
#include "pc_link.h"

#include <stdbool.h>
#include <stdint.h>

static uint16_t gSamples[MEASUREMENT_FFT_SIZE];

static bool normalizeFrequency(uint32_t *frequencyMilliHz)
{
    const uint32_t minimumMilliHz =
        MEASUREMENT_MIN_SPECTRUM_HZ * 1000UL;
    const uint32_t maximumMilliHz =
        MEASUREMENT_MAX_SPECTRUM_HZ * 1000UL;

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

static bool calculateDelay(
    uint32_t frequencyMilliHz, uint16_t phaseCentiDegrees,
    uint16_t *delaySamples)
{
    uint64_t periodQ16;
    uint64_t targetQ16;
    uint64_t fixedQ16 =
        (uint64_t) MEASUREMENT_FIXED_LATENCY_SAMPLES << 16;
    uint64_t additional;

    // Always initialize the output.  Previously a slightly out-of-band FFT
    // interpolation just beyond a configured band edge returned here
    // with delaySamples undefined, and the caller sent that stack value to
    // the FPGA as a random delay.
    *delaySamples = 0U;

    // Hann-window peak interpolation can cross a configured band edge by a
    // fraction of a bin.  Accept up to two FFT bins and clamp to the promised
    // 0.9 kHz..110 kHz input range; reject larger excursions deterministically.
    if (!normalizeFrequency(&frequencyMilliHz))
        return false;
    periodQ16 =
        (((uint64_t) MEASUREMENT_ADC_RATE_HZ * 1000ULL) << 16) /
        frequencyMilliHz;
    targetQ16 = periodQ16 * phaseCentiDegrees / 36000U;
    if (targetQ16 < fixedQ16)
        targetQ16 += periodQ16;
    additional = targetQ16 - fixedQ16;
    additional = (additional + 32768U) >> 16;
    if (additional >= MEASUREMENT_DELAY_DEPTH) {
        // Do not leave a stale, large delay active when an exact whole-cycle
        // solution is outside RAM.  Zero additional delay is the closest
        // safe approximation for the in-phase case at low frequency.
        return false;
    }
    *delaySamples = (uint16_t) additional;
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
    *phaseLag = 0U;
    if (!normalizeFrequency(&frequencyMilliHz))
        return false;

    *phaseStep = (uint32_t)
        (((uint64_t) frequencyMilliHz * phaseTurn + rateMilliHz / 2U) /
         rateMilliHz);
    *phaseLag = (uint32_t)
        (((uint64_t) phaseCentiDegrees * phaseTurn + 18000U) / 36000U);
    return true;
}

int main(void)
{
    MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT];
    FPGA_CaptureStatus status;
    uint16_t flags;
    uint16_t targetPhase = MEASUREMENT_DEFAULT_PHASE_CDEG;
    uint16_t requestedPhase;
    uint8_t targetMode = MEASUREMENT_MODE_DDS;
    uint8_t requestedMode;
    uint16_t delaySamples;
    uint16_t activeDelay = 0xFFFFU;
    uint8_t activeMode = 0xFFU;
    uint32_t activePhaseStep = 0xFFFFFFFFUL;
    uint32_t activePhaseLag = 0xFFFFFFFFUL;
    uint8_t pcSequence;
    uint8_t fpgaSequence = 0U;
    uint32_t trackedFrequency = 0U;

    SYSCFG_DL_init();
    MSP_UART_Link_Init();
    FFT_Analyzer_Init();
    PC_Link_Init();

    while (1) {
        if (PC_Link_TakePhaseRequest(&requestedPhase))
            targetPhase = requestedPhase;
        if (PC_Link_TakeModeRequest(&requestedMode))
            targetMode = requestedMode;

        fpgaSequence++;
        status = FPGA_Capture_Run(fpgaSequence, gSamples, &flags);
        if (status != FPGA_CAPTURE_OK) {
            if (PC_Link_TakeCaptureRequest(&pcSequence))
                PC_Link_SendError(pcSequence, (uint16_t) status);
            continue;
        }

        FFT_Analyzer_Process(gSamples, peaks);
        if (peaks[0].frequencyMilliHz != 0U) {
            if (trackedFrequency == 0U)
                trackedFrequency = peaks[0].frequencyMilliHz;
            else {
                uint32_t frequencyDifference =
                    trackedFrequency > peaks[0].frequencyMilliHz ?
                    trackedFrequency - peaks[0].frequencyMilliHz :
                    peaks[0].frequencyMilliHz - trackedFrequency;
                uint32_t stepThreshold = trackedFrequency / 200U;
                uint32_t twoBinsMilliHz =
                    (2UL * MEASUREMENT_SAMPLE_RATE_HZ * 1000UL) /
                    MEASUREMENT_FFT_SIZE;

                if (stepThreshold < twoBinsMilliHz)
                    stepThreshold = twoBinsMilliHz;
                if (frequencyDifference > stepThreshold)
                    trackedFrequency = peaks[0].frequencyMilliHz;
                else
                    trackedFrequency = (uint32_t)
                        (((uint64_t) trackedFrequency * 7U +
                          peaks[0].frequencyMilliHz + 4U) / 8U);
            }
            peaks[0].frequencyMilliHz = trackedFrequency;
        }
        if ((targetMode & MEASUREMENT_MODE_DDS_MASK) != 0U) {
            uint32_t phaseStep;
            uint32_t phaseLag;
            bool phaseInRange = calculateDDSWords(
                peaks[0].frequencyMilliHz, targetPhase,
                &phaseStep, &phaseLag);

            if (!phaseInRange)
                flags |= MEASUREMENT_FLAG_PHASE_RANGE;
            if (activeMode != targetMode ||
                activePhaseStep != phaseStep ||
                activePhaseLag != phaseLag) {
                FPGA_Capture_SetOutput(
                    fpgaSequence, targetMode,
                    phaseStep, phaseLag);
                activeMode = targetMode;
                activePhaseStep = phaseStep;
                activePhaseLag = phaseLag;
            }
        } else {
            bool phaseInRange = calculateDelay(
                peaks[0].frequencyMilliHz, targetPhase, &delaySamples);

            if (!phaseInRange)
                flags |= MEASUREMENT_FLAG_PHASE_RANGE;

            uint16_t difference = activeDelay > delaySamples ?
                activeDelay - delaySamples : delaySamples - activeDelay;
            if (activeDelay == 0xFFFFU || difference != 0U) {
                FPGA_Capture_SetDelay(fpgaSequence, delaySamples);
                activeDelay = delaySamples;
            }
            if (activeMode != MEASUREMENT_MODE_PIPELINE) {
                FPGA_Capture_SetOutput(
                    fpgaSequence, MEASUREMENT_MODE_PIPELINE, 0U, 0U);
                activeMode = MEASUREMENT_MODE_PIPELINE;
                activePhaseStep = 0U;
                activePhaseLag = 0U;
            }
        }
        // The capture metadata was sampled before the just-issued mode
        // command.  Report the requested mode immediately; lock remains a
        // real FPGA status bit and will assert after the next valid crossing.
        if ((targetMode & MEASUREMENT_MODE_DDS_MASK) != 0U)
            flags |= MEASUREMENT_FLAG_DDS_MODE;
        else
            flags &= (uint16_t) ~(MEASUREMENT_FLAG_DDS_MODE |
                                  MEASUREMENT_FLAG_DDS_LOCKED);
        if ((targetMode & MEASUREMENT_MODE_X2_MASK) != 0U)
            flags |= MEASUREMENT_FLAG_DDS_X2;
        else
            flags &= (uint16_t) ~MEASUREMENT_FLAG_DDS_X2;
        if (PC_Link_TakeCaptureRequest(&pcSequence))
            PC_Link_SendResult(
                pcSequence, flags, peaks, gSamples,
                FFT_Analyzer_GetSpectrum());
    }
}
