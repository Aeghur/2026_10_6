#include "pc_link.h"

#include "msp_uart_link.h"

#define FRAME_TYPE_CAPTURE (0x10U)
#define FRAME_TYPE_RESULT  (0x90U)
#define FRAME_TYPE_ERROR   (0xE0U)
#define RESULT_PAYLOAD_SIZE \
    (10U + 18U + MEASUREMENT_FFT_SIZE * 2U + \
     MEASUREMENT_SPECTRUM_COUNT * 2U)

static volatile bool gCapturePending;
static volatile uint8_t gCaptureSequence;
static uint8_t gRxState;
static uint8_t gRxSequence;
static uint8_t gRxCrcLow;
static uint16_t gRxCrc;

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
    gCapturePending = false;
    gRxState = 0U;
    NVIC_ClearPendingIRQ(PC_UART_INST_INT_IRQN);
    NVIC_EnableIRQ(PC_UART_INST_INT_IRQN);
}

bool PC_Link_TakeCaptureRequest(uint8_t *sequence)
{
    __disable_irq();
    if (!gCapturePending) {
        __enable_irq();
        return false;
    }
    *sequence = gCaptureSequence;
    gCapturePending = false;
    __enable_irq();
    return true;
}

void PC_Link_SendResult(
    uint8_t sequence,
    uint16_t flags,
    const MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT],
    const uint16_t *samples,
    const uint16_t *spectrum)
{
    uint16_t crc;
    uint32_t index;

    beginFrame(FRAME_TYPE_RESULT, sequence, RESULT_PAYLOAD_SIZE, &crc);
    sendU32(MEASUREMENT_SAMPLE_RATE_HZ, &crc);
    sendU16(MEASUREMENT_FFT_SIZE, &crc);
    sendU16(MEASUREMENT_SPECTRUM_COUNT, &crc);
    sendU16(flags, &crc);
    for (index = 0U; index < MEASUREMENT_PEAK_COUNT; index++) {
        sendU32(peaks[index].frequencyMilliHz, &crc);
        sendU16(peaks[index].magnitude, &crc);
    }
    for (index = 0U; index < MEASUREMENT_FFT_SIZE; index++)
        sendU16(samples[index], &crc);
    for (index = 0U; index < MEASUREMENT_SPECTRUM_COUNT; index++)
        sendU16(spectrum[index], &crc);
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
                if (value == FRAME_TYPE_CAPTURE) {
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
                gRxCrc = crc16Update(gRxCrc, value);
                gRxState = value == 0U ? 5U : 0U;
                break;
            case 5:
                gRxCrc = crc16Update(gRxCrc, value);
                gRxState = value == 0U ? 6U : 0U;
                break;
            case 6:
                gRxCrcLow = value;
                gRxState = 7U;
                break;
            case 7:
                if ((((uint16_t) value << 8) | gRxCrcLow) == gRxCrc &&
                    !gCapturePending) {
                    gCaptureSequence = gRxSequence;
                    gCapturePending = true;
                }
                gRxState = 0U;
                break;
            default:
                gRxState = 0U;
                break;
        }
    }
}
