#ifndef PC_LINK_H
#define PC_LINK_H

#include "measurement_config.h"

#include <stdbool.h>
#include <stdint.h>

void PC_Link_Init(void);
bool PC_Link_TakeCaptureRequest(uint8_t *sequence);
bool PC_Link_TakePhaseRequest(uint16_t *phaseCentiDegrees);
void PC_Link_SendResult(
    uint8_t sequence,
    uint16_t flags,
    const MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT],
    const uint16_t *samples,
    const uint16_t *spectrum);
void PC_Link_SendError(uint8_t sequence, uint16_t errorCode);

#endif
