#ifndef FPGA_CAPTURE_H
#define FPGA_CAPTURE_H

#include <stdint.h>

typedef enum {
    FPGA_CAPTURE_OK = 0,
    FPGA_CAPTURE_TIMEOUT = 1,
    FPGA_CAPTURE_FORMAT_ERROR = 2,
    FPGA_CAPTURE_CRC_ERROR = 3
} FPGA_CaptureStatus;

FPGA_CaptureStatus FPGA_Capture_Run(
    uint8_t sequence, uint16_t *samples, uint16_t *flags);

#endif
