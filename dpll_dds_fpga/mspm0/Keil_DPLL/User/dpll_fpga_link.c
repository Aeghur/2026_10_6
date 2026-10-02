#include "dpll_fpga_link.h"

#include "msp_uart_link.h"

#define DPLL_TYPE_STATUS_REQUEST (0x01U)
#define DPLL_TYPE_CONFIG         (0x10U)
#define DPLL_TYPE_STATUS         (0x90U)
#define DPLL_CONFIG_LENGTH       (11U)
#define DPLL_STATUS_LENGTH       (16U)
#define DPLL_RX_TIMEOUT_LOOPS    (4000000UL)

static uint16_t crc16Update(uint16_t crc, uint8_t value)
{
    uint8_t bit;
    crc ^= (uint16_t) value << 8;
    for (bit = 0U; bit < 8U; bit++)
        crc = (crc & 0x8000U) ?
            (uint16_t) ((crc << 1) ^ 0x1021U) : (uint16_t) (crc << 1);
    return crc;
}
static void sendByte(uint8_t value)
{
    DL_UART_Main_transmitDataBlocking(FPGA_UART_INST, value);
}

static bool receiveByte(uint8_t *value)
{
    uint32_t timeout = DPLL_RX_TIMEOUT_LOOPS;
    while (DL_UART_Main_isRXFIFOEmpty(FPGA_UART_INST)) {
        if (--timeout == 0U)
            return false;
    }
    *value = DL_UART_Main_receiveData(FPGA_UART_INST);
    return true;
}

static void writeU16(uint8_t *target, uint16_t value)
{
    target[0] = (uint8_t) value;
    target[1] = (uint8_t) (value >> 8);
}

static void writeU32(uint8_t *target, uint32_t value)
{
    target[0] = (uint8_t) value;
    target[1] = (uint8_t) (value >> 8);
    target[2] = (uint8_t) (value >> 16);
    target[3] = (uint8_t) (value >> 24);
}

static uint16_t readU16(const uint8_t *source)
{
    return (uint16_t) source[0] | ((uint16_t) source[1] << 8);
}

static uint32_t readU32(const uint8_t *source)
{
    return (uint32_t) source[0] |
        ((uint32_t) source[1] << 8) |
        ((uint32_t) source[2] << 16) |
        ((uint32_t) source[3] << 24);
}

uint32_t DPLL_FPGA_PhaseWordFromCentiDegrees(uint16_t phaseCentiDegrees)
{
    uint16_t normalized = phaseCentiDegrees % 36000U;
    return (uint32_t)
        ((((uint64_t) normalized << 32) + 18000ULL) / 36000ULL);
}

uint32_t DPLL_FPGA_FrequencyMilliHz(const DPLL_FPGA_Status *status)
{
    return (uint32_t)
        ((((uint64_t) status->phaseIncrement * status->sampleRateHz * 1000ULL) +
          0x80000000ULL) >> 32);
}

void DPLL_FPGA_SendConfig(
    uint8_t sequence,
    uint16_t lagCentiDegrees,
    uint32_t calibrationPhaseWord,
    uint16_t amplitudeCode,
    bool outputEnable)
{
    uint8_t frame[19];
    uint16_t crc = 0xFFFFU;
    uint8_t index;

    frame[0] = 0xA5U;
    frame[1] = 0x5AU;
    frame[2] = DPLL_TYPE_CONFIG;
    frame[3] = sequence;
    frame[4] = DPLL_CONFIG_LENGTH;
    frame[5] = 0U;
    writeU32(&frame[6], DPLL_FPGA_PhaseWordFromCentiDegrees(lagCentiDegrees));
    writeU32(&frame[10], calibrationPhaseWord);
    writeU16(&frame[14], amplitudeCode);
    frame[16] = outputEnable ? 1U : 0U;
    for (index = 2U; index <= 16U; index++)
        crc = crc16Update(crc, frame[index]);
    frame[17] = (uint8_t) crc;
    frame[18] = (uint8_t) (crc >> 8);
    for (index = 0U; index < sizeof(frame); index++)
        sendByte(frame[index]);
}

DPLL_FPGA_Result DPLL_FPGA_ReadStatus(
    uint8_t sequence, DPLL_FPGA_Status *status)
{
    uint8_t request[8];
    uint8_t header[4];
    uint8_t payload[DPLL_STATUS_LENGTH];
    uint8_t value;
    uint8_t previous = 0U;
    uint8_t index;
    uint16_t crc = 0xFFFFU;
    uint16_t receivedCrc;

    request[0] = 0xA5U;
    request[1] = 0x5AU;
    request[2] = DPLL_TYPE_STATUS_REQUEST;
    request[3] = sequence;
    request[4] = 0U;
    request[5] = 0U;
    for (index = 2U; index < 6U; index++)
        crc = crc16Update(crc, request[index]);
    request[6] = (uint8_t) crc;
    request[7] = (uint8_t) (crc >> 8);
    for (index = 0U; index < sizeof(request); index++)
        sendByte(request[index]);

    do {
        if (!receiveByte(&value))
            return DPLL_FPGA_TIMEOUT;
        if (previous == 0xA5U && value == 0x5AU)
            break;
        previous = value;
    } while (1);

    crc = 0xFFFFU;
    for (index = 0U; index < sizeof(header); index++) {
        if (!receiveByte(&header[index]))
            return DPLL_FPGA_TIMEOUT;
        crc = crc16Update(crc, header[index]);
    }
    if (header[0] != DPLL_TYPE_STATUS || header[1] != sequence ||
        readU16(&header[2]) != DPLL_STATUS_LENGTH)
        return DPLL_FPGA_FORMAT_ERROR;

    for (index = 0U; index < sizeof(payload); index++) {
        if (!receiveByte(&payload[index]))
            return DPLL_FPGA_TIMEOUT;
        crc = crc16Update(crc, payload[index]);
    }
    if (!receiveByte(&value))
        return DPLL_FPGA_TIMEOUT;
    receivedCrc = value;
    if (!receiveByte(&value))
        return DPLL_FPGA_TIMEOUT;
    receivedCrc |= (uint16_t) value << 8;
    if (receivedCrc != crc)
        return DPLL_FPGA_CRC_ERROR;

    status->phaseIncrement = readU32(&payload[0]);
    status->phaseErrorQ23 = (int32_t) readU32(&payload[4]);
    status->flags = readU16(&payload[8]);
    status->dacCode = readU16(&payload[10]);
    status->sampleRateHz = readU32(&payload[12]);
    return DPLL_FPGA_OK;
}

