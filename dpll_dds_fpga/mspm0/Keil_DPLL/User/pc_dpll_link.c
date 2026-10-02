#include "pc_dpll_link.h"

#include "msp_uart_link.h"

#define PC_TYPE_CAPTURE       (0x10U)
#define PC_TYPE_SET_PHASE     (0x11U)
#define PC_TYPE_DPLL_STATUS   (0x12U)
#define PC_TYPE_RESULT        (0x90U)
#define PC_TYPE_DPLL_RESULT   (0x91U)
#define PC_TYPE_ERROR         (0xE0U)

// Keep the existing PC result decoder compatible. The DPLL generates a new
// waveform internally, so no historical 4096-point ADC block is available;
// one center sample and one status spectrum value are returned instead.
#define COMPAT_SAMPLE_COUNT   (1U)
#define COMPAT_SPECTRUM_COUNT (1U)
#define RESULT_PAYLOAD_SIZE   (10U + 18U + 2U + 2U)

static volatile bool gStatusPending;
static volatile uint8_t gStatusSequence;
static volatile bool gRawStatusPending;
static volatile uint8_t gRawStatusSequence;
static volatile bool gPhasePending;
static volatile uint16_t gPhaseCentiDegrees;
static uint8_t gRxState;
static uint8_t gRxType;
static uint8_t gRxSequence;
static uint8_t gRxCrcLow;
static uint16_t gRxCrc;
static uint8_t gRxLength;
static uint8_t gRxPayloadLow;

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
    DL_UART_Main_transmitDataBlocking(PC_UART_INST, value);
}

static void sendCheckedByte(uint8_t value, uint16_t *crc)
{
    sendByte(value);
    *crc = crc16Update(*crc, value);
}

static void sendU16(uint16_t value, uint16_t *crc)
{
    sendCheckedByte((uint8_t) value, crc);
    sendCheckedByte((uint8_t) (value >> 8), crc);
}

static void sendU32(uint32_t value, uint16_t *crc)
{
    sendCheckedByte((uint8_t) value, crc);
    sendCheckedByte((uint8_t) (value >> 8), crc);
    sendCheckedByte((uint8_t) (value >> 16), crc);
    sendCheckedByte((uint8_t) (value >> 24), crc);
}

static void beginFrame(
    uint8_t type, uint8_t sequence, uint16_t payloadLength, uint16_t *crc)
{
    sendByte(0xA5U);
    sendByte(0x5AU);
    *crc = 0xFFFFU;
    sendCheckedByte(type, crc);
    sendCheckedByte(sequence, crc);
    sendU16(payloadLength, crc);
}

static void endFrame(uint16_t crc)
{
    sendByte((uint8_t) crc);
    sendByte((uint8_t) (crc >> 8));
}

void PC_DPLL_Link_Init(void)
{
    gStatusPending = false;
    gRawStatusPending = false;
    gPhasePending = false;
    gRxState = 0U;
    NVIC_ClearPendingIRQ(PC_UART_INST_INT_IRQN);
    NVIC_EnableIRQ(PC_UART_INST_INT_IRQN);
}

bool PC_DPLL_Link_TakePhaseRequest(uint16_t *phaseCentiDegrees)
{
    __disable_irq();
    if (!gPhasePending) {
        __enable_irq();
        return false;
    }
    *phaseCentiDegrees = gPhaseCentiDegrees;
    gPhasePending = false;
    __enable_irq();
    return true;
}

bool PC_DPLL_Link_TakeStatusRequest(uint8_t *sequence)
{
    __disable_irq();
    if (!gStatusPending) {
        __enable_irq();
        return false;
    }
    *sequence = gStatusSequence;
    gStatusPending = false;
    __enable_irq();
    return true;
}

bool PC_DPLL_Link_TakeRawStatusRequest(uint8_t *sequence)
{
    __disable_irq();
    if (!gRawStatusPending) {
        __enable_irq();
        return false;
    }
    *sequence = gRawStatusSequence;
    gRawStatusPending = false;
    __enable_irq();
    return true;
}

void PC_DPLL_Link_SendStatus(
    uint8_t sequence, const DPLL_FPGA_Status *status)
{
    uint16_t crc;
    uint16_t pcFlags = 0U;
    uint16_t magnitude;
    uint32_t frequencyMilliHz = DPLL_FPGA_FrequencyMilliHz(status);

    if ((status->flags & 0x0008U) != 0U)
        pcFlags |= 0x0001U; // ADC OTR
    if ((status->flags & 0x0004U) != 0U)
        pcFlags |= 0x0002U; // DAC clipping
    if ((status->flags & 0x0003U) != 0x0003U)
        pcFlags |= 0x0004U; // no signal or not locked
    magnitude = (status->flags & 0x0002U) != 0U ? 65535U : 1U;

    beginFrame(PC_TYPE_RESULT, sequence, RESULT_PAYLOAD_SIZE, &crc);
    sendU32(status->sampleRateHz, &crc);
    sendU16(COMPAT_SAMPLE_COUNT, &crc);
    sendU16(COMPAT_SPECTRUM_COUNT, &crc);
    sendU16(pcFlags, &crc);
    sendU32(frequencyMilliHz, &crc);
    sendU16(magnitude, &crc);
    sendU32(0U, &crc);
    sendU16(0U, &crc);
    sendU32(0U, &crc);
    sendU16(0U, &crc);
    sendU16(2104U, &crc);
    sendU16(magnitude, &crc);
    endFrame(crc);
}

void PC_DPLL_Link_SendError(uint8_t sequence, uint16_t errorCode)
{
    uint16_t crc;
    beginFrame(PC_TYPE_ERROR, sequence, 2U, &crc);
    sendU16(errorCode, &crc);
    endFrame(crc);
}

void PC_DPLL_Link_SendRawStatus(
    uint8_t sequence, const DPLL_FPGA_Status *status)
{
    uint16_t crc;
    beginFrame(PC_TYPE_DPLL_RESULT, sequence, 16U, &crc);
    sendU32(status->phaseIncrement, &crc);
    sendU32((uint32_t) status->phaseErrorQ23, &crc);
    sendU16(status->flags, &crc);
    sendU16(status->dacCode, &crc);
    sendU32(status->sampleRateHz, &crc);
    endFrame(crc);
}

void UART1_IRQHandler(void)
{
    while (!DL_UART_Main_isRXFIFOEmpty(PC_UART_INST)) {
        uint8_t value = DL_UART_Main_receiveData(PC_UART_INST);
        switch (gRxState) {
            case 0:
                gRxState = value == 0xA5U ? 1U : 0U;
                break;
            case 1:
                gRxState = value == 0x5AU ? 2U :
                           value == 0xA5U ? 1U : 0U;
                break;
            case 2:
                if (value == PC_TYPE_CAPTURE || value == PC_TYPE_SET_PHASE ||
                    value == PC_TYPE_DPLL_STATUS) {
                    gRxType = value;
                    gRxCrc = crc16Update(0xFFFFU, value);
                    gRxState = 3U;
                } else {
                    gRxState = 0U;
                }
                break;
            case 3:
                gRxSequence = value;
                gRxCrc = crc16Update(gRxCrc, value);
                gRxState = 4U;
                break;
            case 4:
                gRxLength = value;
                gRxCrc = crc16Update(gRxCrc, value);
                gRxState = 5U;
                break;
            case 5:
                gRxCrc = crc16Update(gRxCrc, value);
                if (value != 0U ||
                    (gRxLength != 0U && gRxLength != 2U)) {
                    gRxState = 0U;
                } else if (gRxLength == 0U) {
                    gRxState = 8U;
                } else {
                    gRxState = 6U;
                }
                break;
            case 6:
                gRxPayloadLow = value;
                gRxCrc = crc16Update(gRxCrc, value);
                gRxState = 7U;
                break;
            case 7:
                gPhaseCentiDegrees =
                    (uint16_t) gRxPayloadLow | ((uint16_t) value << 8);
                gRxCrc = crc16Update(gRxCrc, value);
                gRxState = 8U;
                break;
            case 8:
                gRxCrcLow = value;
                gRxState = 9U;
                break;
            case 9:
                if ((((uint16_t) value << 8) | gRxCrcLow) == gRxCrc) {
                    if (gRxType == PC_TYPE_CAPTURE && gRxLength == 0U &&
                        !gStatusPending) {
                        gStatusSequence = gRxSequence;
                        gStatusPending = true;
                    } else if (gRxType == PC_TYPE_DPLL_STATUS &&
                               gRxLength == 0U && !gRawStatusPending) {
                        gRawStatusSequence = gRxSequence;
                        gRawStatusPending = true;
                    } else if (gRxType == PC_TYPE_SET_PHASE &&
                               gRxLength == 2U &&
                               gPhaseCentiDegrees < 36000U) {
                        gPhasePending = true;
                    }
                }
                gRxState = 0U;
                break;
            default:
                gRxState = 0U;
                break;
        }
    }
}

