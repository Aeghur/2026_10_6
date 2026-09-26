#include "fft_analyzer.h"

#include "arm_math.h"

#include <stdint.h>

#define FFT_PEAK_GUARD_BINS (4U)

static q15_t gFftInput[MEASUREMENT_FFT_SIZE];
static q15_t gFftOutput[MEASUREMENT_FFT_SIZE * 2U];
static uint16_t gMagnitude[MEASUREMENT_FFT_SIZE / 2U];

static void prepareHannInput(const uint16_t *samples)
{
    int64_t previous = 1073741824LL;
    int64_t current = 1073740560LL;
    const int64_t twiceCosStep = 2147481120LL;
    int32_t sum = 0;
    int32_t mean;
    uint32_t index;

    for (index = 0U; index < MEASUREMENT_FFT_SIZE; index++)
        sum += samples[index];
    mean = sum / (int32_t) MEASUREMENT_FFT_SIZE;

    gFftInput[0] = 0;
    for (index = 1U; index < MEASUREMENT_FFT_SIZE; index++) {
        int64_t window = (1073741824LL - current) >> 16;
        int64_t next;
        int32_t centered;
        int32_t windowed;

        if (window < 0)
            window = 0;
        if (window > 32767)
            window = 32767;
        centered = ((int32_t) samples[index] - mean) * 8;
        windowed = (centered * (int32_t) window) >> 15;
        if (windowed > 32767)
            windowed = 32767;
        if (windowed < -32768)
            windowed = -32768;
        gFftInput[index] = (q15_t) windowed;
        next = ((twiceCosStep * current) >> 30) - previous;
        previous = current;
        current = next;
    }
    gFftInput[MEASUREMENT_FFT_SIZE - 1U] = 0;
}

static uint32_t interpolatedFrequencyMilliHz(uint16_t bin)
{
    int32_t left = gMagnitude[bin - 1U];
    int32_t center = gMagnitude[bin];
    int32_t right = gMagnitude[bin + 1U];
    int32_t denominator = left - 2 * center + right;
    int32_t deltaQ15 = 0;
    int64_t binQ15;

    if (denominator != 0)
        deltaQ15 = ((left - right) * 16384L) / denominator;
    if (deltaQ15 > 16384)
        deltaQ15 = 16384;
    if (deltaQ15 < -16384)
        deltaQ15 = -16384;
    binQ15 = (int64_t) bin * 32768LL + deltaQ15;
    return (uint32_t)
        ((binQ15 * MEASUREMENT_SAMPLE_RATE_HZ * 1000LL) /
         ((int64_t) MEASUREMENT_FFT_SIZE * 32768LL));
}

static void insertPeak(
    MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT],
    uint16_t bin, uint16_t magnitude)
{
    uint32_t index;
    uint32_t position;

    for (index = 0U; index < MEASUREMENT_PEAK_COUNT; index++) {
        int32_t distance = (int32_t) bin - (int32_t) peaks[index].bin;
        if (peaks[index].magnitude != 0U &&
            distance >= -(int32_t) FFT_PEAK_GUARD_BINS &&
            distance <= (int32_t) FFT_PEAK_GUARD_BINS)
            return;
    }
    if (magnitude <= peaks[MEASUREMENT_PEAK_COUNT - 1U].magnitude)
        return;
    position = MEASUREMENT_PEAK_COUNT - 1U;
    while (position > 0U &&
           magnitude > peaks[position - 1U].magnitude) {
        peaks[position] = peaks[position - 1U];
        position--;
    }
    peaks[position].bin = bin;
    peaks[position].magnitude = magnitude;
    peaks[position].frequencyMilliHz =
        interpolatedFrequencyMilliHz(bin);
}

void FFT_Analyzer_Init(void)
{
    uint32_t index;
    for (index = 0U; index < MEASUREMENT_FFT_SIZE / 2U; index++)
        gMagnitude[index] = 0U;
}

void FFT_Analyzer_Process(
    const uint16_t *samples,
    MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT])
{
    arm_rfft_instance_q15 fft;
    uint16_t firstBin;
    uint16_t lastBin;
    uint32_t index;

    for (index = 0U; index < MEASUREMENT_PEAK_COUNT; index++) {
        peaks[index].frequencyMilliHz = 0U;
        peaks[index].magnitude = 0U;
        peaks[index].bin = 0U;
    }

    prepareHannInput(samples);
    (void) arm_rfft_init_q15(&fft, MEASUREMENT_FFT_SIZE, 0U, 1U);
    arm_rfft_q15(&fft, gFftInput, gFftOutput);
    arm_cmplx_mag_q15(
        gFftOutput, (q15_t *) gMagnitude,
        MEASUREMENT_FFT_SIZE / 2U);

    firstBin = (uint16_t)
        ((MEASUREMENT_MIN_SPECTRUM_HZ * MEASUREMENT_FFT_SIZE) /
         MEASUREMENT_SAMPLE_RATE_HZ);
    lastBin = (uint16_t)
        ((MEASUREMENT_MAX_SPECTRUM_HZ * MEASUREMENT_FFT_SIZE) /
         MEASUREMENT_SAMPLE_RATE_HZ);
    for (index = firstBin; index <= lastBin; index++) {
        if (gMagnitude[index] >= gMagnitude[index - 1U] &&
            gMagnitude[index] > gMagnitude[index + 1U])
            insertPeak(peaks, (uint16_t) index, gMagnitude[index]);
    }
}

const uint16_t *FFT_Analyzer_GetSpectrum(void)
{
    return gMagnitude;
}
