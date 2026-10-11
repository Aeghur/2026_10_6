#ifndef PC_LINK_H
#define PC_LINK_H

#include "measurement_config.h"

#include <stdbool.h>
#include <stdint.h>

void PC_Link_Init(void);
bool PC_Link_TakePhaseRequest(uint16_t *phaseCentiDegrees);
bool PC_Link_TakeModeRequest(uint8_t *mode);
bool PC_Link_TakeTargetRequest(
    uint8_t *sequence, uint32_t *frequencyMilliHz,
    uint16_t *phaseCentiDegrees, uint8_t *amplitudeCode);
void PC_Link_SendTargetAck(
    uint8_t sequence, uint32_t frequencyMilliHz,
    uint16_t phaseCentiDegrees, uint8_t amplitudeCode, uint8_t mode);
void PC_Link_SendError(uint8_t sequence, uint16_t errorCode);

#endif
