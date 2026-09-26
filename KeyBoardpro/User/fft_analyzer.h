#ifndef FFT_ANALYZER_H
#define FFT_ANALYZER_H

#include "measurement_config.h"

void FFT_Analyzer_Init(void);
void FFT_Analyzer_Process(
    const uint16_t *samples,
    MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT]);
const uint16_t *FFT_Analyzer_GetSpectrum(void);

#endif
