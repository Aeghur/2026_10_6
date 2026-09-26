#ifndef MEASUREMENT_CONFIG_H
#define MEASUREMENT_CONFIG_H

#include <stdint.h>

#define MEASUREMENT_FFT_SIZE           (4096U)
#define MEASUREMENT_SAMPLE_RATE_HZ     (1562500UL)
#define MEASUREMENT_MIN_SPECTRUM_HZ    (10000UL)
#define MEASUREMENT_MAX_SPECTRUM_HZ    (500000UL)
#define MEASUREMENT_SPECTRUM_COUNT     (1311U)
#define MEASUREMENT_PEAK_COUNT         (3U)

typedef struct {
    uint32_t frequencyMilliHz;
    uint16_t magnitude;
    uint16_t bin;
} MeasurementPeak;

#endif
