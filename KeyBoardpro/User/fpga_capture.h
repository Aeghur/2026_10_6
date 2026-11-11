#ifndef FPGA_CAPTURE_H
#define FPGA_CAPTURE_H

#include <stdint.h>

typedef enum {
    FPGA_CAPTURE_OK = 0,
    FPGA_CAPTURE_TIMEOUT = 1,
    FPGA_CAPTURE_FORMAT_ERROR = 2,
    FPGA_CAPTURE_CRC_ERROR = 3
} FPGA_CaptureStatus;

typedef struct {
    uint8_t status;
    uint32_t appliedSampleCounter;
    uint32_t oldPhaseStep;
    uint32_t newPhaseStep;
    uint32_t commitPhase;
} FPGA_DoubleResult;

FPGA_CaptureStatus FPGA_Capture_Run(
    uint8_t sequence, uint16_t *samples, uint16_t *flags);
void FPGA_Capture_SetDelay(uint8_t sequence, uint16_t delaySamples);
FPGA_CaptureStatus FPGA_Capture_SetOutput(
    uint8_t sequence, uint8_t mode, uint32_t phaseStep,
    uint32_t phaseLag, uint16_t decayStart,
    uint32_t decayScaleQ16);
FPGA_CaptureStatus FPGA_Capture_ArmDouble(
    uint8_t sequence, FPGA_DoubleResult *result);

#endif
