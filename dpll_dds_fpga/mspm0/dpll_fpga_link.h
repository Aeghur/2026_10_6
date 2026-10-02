#ifndef DPLL_FPGA_LINK_H
#define DPLL_FPGA_LINK_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    DPLL_FPGA_OK = 0,
    DPLL_FPGA_TIMEOUT = 1,
    DPLL_FPGA_FORMAT_ERROR = 2,
    DPLL_FPGA_CRC_ERROR = 3
} DPLL_FPGA_Result;

typedef struct {
    uint32_t phaseIncrement;
    int32_t phaseErrorQ23;
    uint16_t flags;
    uint16_t dacCode;
    uint32_t sampleRateHz;
} DPLL_FPGA_Status;

uint32_t DPLL_FPGA_PhaseWordFromCentiDegrees(uint16_t phaseCentiDegrees);
uint32_t DPLL_FPGA_FrequencyMilliHz(const DPLL_FPGA_Status *status);
void DPLL_FPGA_SendConfig(
    uint8_t sequence,
    uint16_t lagCentiDegrees,
    uint32_t calibrationPhaseWord,
    uint16_t amplitudeCode,
    bool outputEnable);
DPLL_FPGA_Result DPLL_FPGA_ReadStatus(
    uint8_t sequence, DPLL_FPGA_Status *status);

#endif

