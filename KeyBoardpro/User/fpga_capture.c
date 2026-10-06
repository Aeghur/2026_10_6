#include "fpga_capture.h"

#include "measurement_config.h"
#include "msp_uart_link.h"

#include <stdbool.h>

#define FPGA_TYPE_CAPTURE       (0x01U)
#define FPGA_TYPE_SET_DELAY     (0x03U)
#define FPGA_TYPE_SET_OUTPUT    (0x04U)
#define FPGA_TYPE_SAMPLES       (0x81U)
#define FPGA_PAYLOAD_SIZE       (8U + MEASUREMENT_FFT_SIZE * 2U)
#define FPGA_BYTE_TIMEOUT_LOOPS (4000000UL)

static uint16_t crc16Update(uint16_t crc, uint8_t value)
{
    uint8_t bit;
    crc ^= (uint16_t) value << 8;
    for (bit = 0U; bit < 8U; bit++)
        crc = (crc & 0x8000U) ?
            (uint16_t) ((crc << 1) ^ 0x1021U) :
            (uint16_t) (crc << 1);
    return crc;
}

static bool receiveByte(uint8_t *value)
{
    uint32_t timeout = FPGA_BYTE_TIMEOUT_LOOPS;
    while (DL_UART_Main_isRXFIFOEmpty(FPGA_UART_INST)) {
        if (--timeout == 0U)
            return false;
    }
    *value = DL_UART_Main_receiveData(FPGA_UART_INST);
    return true;
}

static void sendCaptureCommand(uint8_t sequence)
{
    uint8_t frame[8];
    uint16_t crc = 0xFFFFU;
    uint8_t index;

    frame[0] = 0xA5U;
    frame[1] = 0x5AU;
    frame[2] = FPGA_TYPE_CAPTURE;
    frame[3] = sequence;
    frame[4] = 0U;
    frame[5] = 0U;
    for (index = 2U; index < 6U; index++)
        crc = crc16Update(crc, frame[index]);
    frame[6] = (uint8_t) crc;
    frame[7] = (uint8_t) (crc >> 8);
    for (index = 0U; index < sizeof(frame); index++)
        DL_UART_Main_transmitDataBlocking(FPGA_UART_INST, frame[index]);
}

void FPGA_Capture_SetDelay(uint8_t sequence, uint16_t delaySamples)
{
    uint8_t frame[10];
    uint16_t crc = 0xFFFFU;
    uint8_t index;

    frame[0] = 0xA5U;
    frame[1] = 0x5AU;
    frame[2] = FPGA_TYPE_SET_DELAY;
    frame[3] = sequence;
    frame[4] = 2U;
    frame[5] = 0U;
    frame[6] = (uint8_t) delaySamples;
    frame[7] = (uint8_t) (delaySamples >> 8);
    for (index = 2U; index < 8U; index++)
        crc = crc16Update(crc, frame[index]);
    frame[8] = (uint8_t) crc;
    frame[9] = (uint8_t) (crc >> 8);
    for (index = 0U; index < sizeof(frame); index++)
        DL_UART_Main_transmitDataBlocking(FPGA_UART_INST, frame[index]);
}

void FPGA_Capture_SetOutput(
    uint8_t sequence, uint8_t mode, uint32_t phaseStep,
    uint32_t phaseLag)
{
    uint8_t frame[17];
    uint16_t crc = 0xFFFFU;
    uint8_t index;

    frame[0] = 0xA5U;
    frame[1] = 0x5AU;
    frame[2] = FPGA_TYPE_SET_OUTPUT;
    frame[3] = sequence;
    frame[4] = 9U;
    frame[5] = 0U;
    frame[6] = mode;
    frame[7] = (uint8_t) phaseStep;
    frame[8] = (uint8_t) (phaseStep >> 8);
    frame[9] = (uint8_t) (phaseStep >> 16);
    frame[10] = (uint8_t) (phaseStep >> 24);
    frame[11] = (uint8_t) phaseLag;
    frame[12] = (uint8_t) (phaseLag >> 8);
    frame[13] = (uint8_t) (phaseLag >> 16);
    frame[14] = (uint8_t) (phaseLag >> 24);
    for (index = 2U; index < 15U; index++)
        crc = crc16Update(crc, frame[index]);
    frame[15] = (uint8_t) crc;
    frame[16] = (uint8_t) (crc >> 8);
    for (index = 0U; index < sizeof(frame); index++)
        DL_UART_Main_transmitDataBlocking(FPGA_UART_INST, frame[index]);
}

static bool readCheckedByte(uint8_t *value, uint16_t *crc)
{
    if (!receiveByte(value))
        return false;
    *crc = crc16Update(*crc, *value);
    return true;
}

FPGA_CaptureStatus FPGA_Capture_Run(
    uint8_t sequence, uint16_t *samples, uint16_t *flags)
{
    uint8_t value;
    uint8_t previous = 0U;
    uint8_t header[4];
    uint8_t metadata[8];
    uint8_t low;
    uint8_t high;
    uint16_t crc = 0xFFFFU;
    uint16_t receivedCrc;
    uint32_t index;

    while (!DL_UART_Main_isRXFIFOEmpty(FPGA_UART_INST))
        (void) DL_UART_Main_receiveData(FPGA_UART_INST);
    sendCaptureCommand(sequence);

    do {
        if (!receiveByte(&value))
            return FPGA_CAPTURE_TIMEOUT;
        if (previous == 0xA5U && value == 0x5AU)
            break;
        previous = value;
    } while (1);

    for (index = 0U; index < sizeof(header); index++) {
        if (!readCheckedByte(&header[index], &crc))
            return FPGA_CAPTURE_TIMEOUT;
    }
    if (header[0] != FPGA_TYPE_SAMPLES || header[1] != sequence ||
        ((uint16_t) header[2] | ((uint16_t) header[3] << 8)) !=
            FPGA_PAYLOAD_SIZE)
        return FPGA_CAPTURE_FORMAT_ERROR;

    for (index = 0U; index < sizeof(metadata); index++) {
        if (!readCheckedByte(&metadata[index], &crc))
            return FPGA_CAPTURE_TIMEOUT;
    }
    if (((uint32_t) metadata[0] |
         ((uint32_t) metadata[1] << 8) |
         ((uint32_t) metadata[2] << 16) |
         ((uint32_t) metadata[3] << 24)) !=
            MEASUREMENT_SAMPLE_RATE_HZ ||
        ((uint16_t) metadata[4] | ((uint16_t) metadata[5] << 8)) !=
            MEASUREMENT_FFT_SIZE)
        return FPGA_CAPTURE_FORMAT_ERROR;
    *flags = (uint16_t) metadata[6] | ((uint16_t) metadata[7] << 8);

    for (index = 0U; index < MEASUREMENT_FFT_SIZE; index++) {
        if (!readCheckedByte(&low, &crc) ||
            !readCheckedByte(&high, &crc))
            return FPGA_CAPTURE_TIMEOUT;
        samples[index] =
            ((uint16_t) low | ((uint16_t) high << 8)) & 0x0FFFU;
    }
    if (!receiveByte(&low) || !receiveByte(&high))
        return FPGA_CAPTURE_TIMEOUT;
    receivedCrc = (uint16_t) low | ((uint16_t) high << 8);
    return receivedCrc == crc ?
        FPGA_CAPTURE_OK : FPGA_CAPTURE_CRC_ERROR;
}
