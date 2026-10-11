#include "pc_link.h"

#include "msp_uart_link.h"

#define FRAME_TYPE_SET_PHASE (0x11U)
#define FRAME_TYPE_SET_MODE  (0x12U)
#define FRAME_TYPE_SET_TARGET (0x13U)
#define FRAME_TYPE_TARGET_ACK (0x93U)
#define FRAME_TYPE_ERROR   (0xE0U)

static volatile bool gPhasePending;
static volatile uint16_t gPhaseCentiDegrees;
static volatile bool gModePending;
static volatile uint8_t gOutputMode;
static volatile bool gTargetPending;
static volatile uint8_t gTargetSequence;
static volatile uint32_t gTargetFrequencyMilliHz;
static volatile uint16_t gTargetPhaseCentiDegrees;
static volatile uint8_t gTargetAmplitudeCode;
static uint8_t gRxState;
static uint8_t gRxType;
static uint8_t gRxSequence;
static uint8_t gRxCrcLow;
static uint16_t gRxCrc;
static uint8_t gRxLength;
static uint8_t gRxPayload[7];
static uint8_t gRxPayloadIndex;

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
    uint8_t type, uint8_t sequence, uint16_t payloadLength,
    uint16_t *crc)
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

void PC_Link_Init(void)
{
    gPhasePending = false;
    gModePending = false;
    gTargetPending = false;
    gRxState = 0U;
    NVIC_ClearPendingIRQ(PC_UART_INST_INT_IRQN);
    NVIC_EnableIRQ(PC_UART_INST_INT_IRQN);
}

bool PC_Link_TakeTargetRequest(
    uint8_t *sequence, uint32_t *frequencyMilliHz,
    uint16_t *phaseCentiDegrees, uint8_t *amplitudeCode)
{
    __disable_irq();
    if (!gTargetPending) {
        __enable_irq();
        return false;
    }
    *sequence = gTargetSequence;
    *frequencyMilliHz = gTargetFrequencyMilliHz;
    *phaseCentiDegrees = gTargetPhaseCentiDegrees;
    *amplitudeCode = gTargetAmplitudeCode;
    gTargetPending = false;
    __enable_irq();
    return true;
}

bool PC_Link_TakeModeRequest(uint8_t *mode)
{
    __disable_irq();
    if (!gModePending) {
        __enable_irq();
        return false;
    }
    *mode = gOutputMode;
    gModePending = false;
    __enable_irq();
    return true;
}

bool PC_Link_TakePhaseRequest(uint16_t *phaseCentiDegrees)
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

void PC_Link_SendTargetAck(
    uint8_t sequence, uint32_t frequencyMilliHz,
    uint16_t phaseCentiDegrees, uint8_t amplitudeCode, uint8_t mode)
{
    uint16_t crc;

    beginFrame(FRAME_TYPE_TARGET_ACK, sequence, 8U, &crc);
    sendU32(frequencyMilliHz, &crc);
    sendU16(phaseCentiDegrees, &crc);
    sendCheckedByte(amplitudeCode, &crc);
    sendCheckedByte(mode, &crc);
    endFrame(crc);
}

void PC_Link_SendError(uint8_t sequence, uint16_t errorCode)
{
    uint16_t crc;
    beginFrame(FRAME_TYPE_ERROR, sequence, 2U, &crc);
    sendU16(errorCode, &crc);
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
                if (value == FRAME_TYPE_SET_PHASE ||
                    value == FRAME_TYPE_SET_MODE ||
                    value == FRAME_TYPE_SET_TARGET) {
                    gRxType = value;
                    gRxCrc = crc16Update(0xFFFFU, value);
                    gRxLength = 0U;
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
                gRxCrc = crc16Update(gRxCrc, value);
                gRxLength = value;
                gRxState = 5U;
                break;
            case 5:
                gRxCrc = crc16Update(gRxCrc, value);
                if (value != 0U ||
                    (gRxType == FRAME_TYPE_SET_PHASE &&
                     gRxLength != 2U) ||
                    (gRxType == FRAME_TYPE_SET_MODE &&
                     gRxLength != 1U) ||
                    (gRxType == FRAME_TYPE_SET_TARGET &&
                     gRxLength != 7U)) {
                    gRxState = 0U;
                } else {
                    gRxState = 6U;
                }
                break;
            case 6:
                gRxCrc = crc16Update(gRxCrc, value);
                if (gRxType == FRAME_TYPE_SET_MODE &&
                    gRxLength == 1U) {
                    gOutputMode = value;
                    gRxState = 8U;
                } else {
                    gRxPayloadIndex = 0U;
                    gRxPayload[0] = value;
                    gRxState = gRxLength == 1U ? 8U : 7U;
                }
                break;
            case 7:
                gRxCrc = crc16Update(gRxCrc, value);
                gRxPayloadIndex++;
                gRxPayload[gRxPayloadIndex] = value;
                if (gRxPayloadIndex + 1U == gRxLength)
                    gRxState = 8U;
                break;
            case 8:
                gRxCrcLow = value;
                gRxState = 9U;
                break;
            case 9:
                if ((((uint16_t) value << 8) | gRxCrcLow) == gRxCrc) {
                    if (gRxType == FRAME_TYPE_SET_PHASE &&
                               gRxLength == 2U &&
                               ((uint16_t) gRxPayload[0] |
                                ((uint16_t) gRxPayload[1] << 8)) < 36000U) {
                        gPhaseCentiDegrees =
                            (uint16_t) gRxPayload[0] |
                            ((uint16_t) gRxPayload[1] << 8);
                        gPhasePending = true;
                    } else if (gRxType == FRAME_TYPE_SET_MODE &&
                               gRxLength == 1U &&
                               (gOutputMode == MEASUREMENT_MODE_PIPELINE ||
                                ((gOutputMode & MEASUREMENT_MODE_DDS_MASK) != 0U &&
                                 (gOutputMode & 0xE0U) == 0U))) {
                        gModePending = true;
                    } else if (gRxType == FRAME_TYPE_SET_TARGET &&
                               gRxLength == 7U) {
                        uint32_t frequency =
                            (uint32_t) gRxPayload[0] |
                            ((uint32_t) gRxPayload[1] << 8) |
                            ((uint32_t) gRxPayload[2] << 16) |
                            ((uint32_t) gRxPayload[3] << 24);
                        uint16_t phase =
                            (uint16_t) gRxPayload[4] |
                            ((uint16_t) gRxPayload[5] << 8);
                        uint8_t amplitude = gRxPayload[6];
                        if (frequency >=
                                MEASUREMENT_MIN_SPECTRUM_HZ * 1000UL &&
                            frequency <=
                                MEASUREMENT_MAX_SPECTRUM_HZ * 1000UL &&
                            phase < 36000U && amplitude < 4U) {
                            gTargetFrequencyMilliHz = frequency;
                            gTargetPhaseCentiDegrees = phase;
                            gTargetAmplitudeCode = amplitude;
                            gTargetSequence = gRxSequence;
                            gTargetPending = true;
                        }
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
