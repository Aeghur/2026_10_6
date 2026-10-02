#ifndef PC_DPLL_LINK_H
#define PC_DPLL_LINK_H

#include "dpll_fpga_link.h"

#include <stdbool.h>
#include <stdint.h>

void PC_DPLL_Link_Init(void);
bool PC_DPLL_Link_TakePhaseRequest(uint16_t *phaseCentiDegrees);
bool PC_DPLL_Link_TakeStatusRequest(uint8_t *sequence);
bool PC_DPLL_Link_TakeRawStatusRequest(uint8_t *sequence);
void PC_DPLL_Link_SendStatus(
    uint8_t sequence, const DPLL_FPGA_Status *status);
void PC_DPLL_Link_SendRawStatus(
    uint8_t sequence, const DPLL_FPGA_Status *status);
void PC_DPLL_Link_SendError(uint8_t sequence, uint16_t errorCode);

#endif
