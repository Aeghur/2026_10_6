#include "keypad_control.h"

#include "measurement_config.h"
#include "pc_link.h"
#include "spi0_oled.h"
#include "ti_msp_dl_config.h"

#define KEYPAD_SCAN_HZ           (100U)
#define KEYPAD_MAX_PHASE_DEGREES (359U)
#define KEYPAD_QUEUE_SIZE        (8U)
#define KEYPAD_QUEUE_MASK        (KEYPAD_QUEUE_SIZE - 1U)

static volatile uint8_t gKeyQueue[KEYPAD_QUEUE_SIZE];
static volatile uint8_t gKeyQueueHead;
static volatile uint8_t gKeyQueueTail;
static volatile uint32_t gMilliseconds;
static uint8_t gRawCandidateKey;
static uint8_t gRawStableKey;
static uint8_t gRawCandidateCount;
static bool gPhaseEntryActive;
static bool gPhaseEntryValid;
static uint16_t gPhaseEntryDegrees;
static uint8_t gPhaseEntryDigits;
static bool gTaskMenuActive;
static bool gTaskRequestPending;
static uint8_t gTaskRequestMode;
static bool gTaskViewDismissed;
static TaskControlSnapshot gTaskSnapshot;
static uint8_t gLastOutputMode;
static uint16_t gLastPhaseCentiDegrees;

static void showLine(uint8_t page, const char *text)
{
    uint8_t line[17];
    uint8_t index = 0U;

    while (index < 16U && text[index] != '\0') {
        line[index] = (uint8_t) text[index];
        index++;
    }
    while (index < 16U)
        line[index++] = (uint8_t) ' ';
    line[16] = 0U;
    OLED_ShowString(0U, page, line);
}

static void showThreeDigits(uint8_t *destination, uint16_t value)
{
    destination[0] = (uint8_t) ('0' + value / 100U);
    destination[1] = (uint8_t) ('0' + (value / 10U) % 10U);
    destination[2] = (uint8_t) ('0' + value % 10U);
}

static void showFiveDigits(uint8_t *destination, uint32_t value)
{
    uint32_t divisor = 10000UL;
    uint8_t index;

    if (value > 99999UL)
        value = 99999UL;
    for (index = 0U; index < 5U; index++) {
        destination[index] = (uint8_t) ('0' + value / divisor);
        value %= divisor;
        divisor /= 10UL;
    }
}

static void showMilliseconds(uint8_t page, const char *prefix, uint32_t value)
{
    uint8_t line[17] = "TIME:00000.0 SEC";
    uint32_t tenths = value / 100UL;
    uint8_t index;

    for (index = 0U; index < 5U; index++)
        line[index] = (uint8_t) prefix[index];
    showFiveDigits(&line[5], tenths / 10UL);
    line[11] = (uint8_t) ('0' + tenths % 10UL);
    OLED_ShowString(0U, page, line);
}

static const char *taskStateText(uint8_t state)
{
    switch (state) {
        case TASK_CONTROL_STATE_SENDING: return "SENDING REQUEST";
        case PC_TASK_STATE_BOOTING: return "SERVICE BOOTING";
        case PC_TASK_STATE_READY: return "SERVICE READY";
        case PC_TASK_STATE_ACCEPTED: return "TASK ACCEPTED";
        case PC_TASK_STATE_RUNNING: return "TASK RUNNING";
        case PC_TASK_STATE_SUCCEEDED: return "TASK SUCCEEDED";
        case PC_TASK_STATE_FAILED: return "TASK FAILED";
        case PC_TASK_STATE_BUSY: return "SERVICE BUSY";
        case PC_TASK_STATE_REJECTED: return "TASK REJECTED";
        case PC_TASK_STATE_FAULT: return "SERVICE FAULT";
        default: return "UNKNOWN STATE";
    }
}

static const char *taskReasonText(uint8_t reason)
{
    switch (reason) {
        case PC_TASK_REASON_NONE: return "#=TASK MENU";
        case PC_TASK_REASON_FORMAT: return "ERR:FORMAT";
        case PC_TASK_REASON_SESSION: return "ERR:SESSION";
        case PC_TASK_REASON_BUSY: return "ERR:BUSY";
        case PC_TASK_REASON_TIMEOUT: return "ERR:TIMEOUT";
        case PC_TASK_REASON_FREQUENCY: return "ERR:FREQUENCY";
        case PC_TASK_REASON_HARDWARE: return "ERR:HARDWARE";
        case PC_TASK_REASON_VERIFY_OUTPUT: return "VERIFY OUTPUT";
        case PC_TASK_REASON_CAPACITY: return "ERR:CAPACITY";
        case PC_TASK_REASON_ID_CONFLICT: return "ID CONFLICT";
        case PC_TASK_REASON_NOT_READY: return "ERR:NOT READY";
        case PC_TASK_REASON_NOT_FOUND: return "ERR:NOT FOUND";
        default: return "UNKNOWN REASON";
    }
}

static bool taskInputLocked(void)
{
    return gTaskSnapshot.taskActive ||
           gTaskSnapshot.serviceState == PC_TASK_STATE_ACCEPTED ||
           gTaskSnapshot.serviceState == PC_TASK_STATE_RUNNING;
}

static void showTaskMenu(void)
{
    showLine(0U, "AUTO MEASURE");
    if (gTaskSnapshot.serviceReady) {
        showLine(2U, "A=DIAG  B=OVAL");
        showLine(4U, "C=INFINITY");
    } else {
        showLine(2U, "SERVICE NOT READY");
        showLine(4U, taskStateText(gTaskSnapshot.serviceState));
    }
    showLine(6U, "#=BACK");
}

static void showTaskStatus(void)
{
    switch (gTaskSnapshot.taskMode) {
        case PC_TASK_MODE_DIAGONAL: showLine(0U, "AUTO:DIAGONAL"); break;
        case PC_TASK_MODE_ELLIPSE: showLine(0U, "AUTO:CIRCLE/OVAL"); break;
        case PC_TASK_MODE_INFINITY: showLine(0U, "AUTO:INFINITY"); break;
        default: showLine(0U, "AUTO MEASURE"); break;
    }
    showLine(2U, taskStateText(gTaskSnapshot.taskState));
    showMilliseconds(4U, "TIME:", gTaskSnapshot.elapsedMs);
    if (gTaskSnapshot.taskState == PC_TASK_STATE_RUNNING ||
        gTaskSnapshot.taskState == PC_TASK_STATE_SUCCEEDED)
        showMilliseconds(6U, "STBL:", gTaskSnapshot.stableMs);
    else
        showLine(6U, taskReasonText(gTaskSnapshot.taskReason));
}

static uint8_t amplitudeValue(uint8_t outputMode)
{
    switch ((outputMode & MEASUREMENT_MODE_AMPLITUDE_MASK) >>
            MEASUREMENT_MODE_AMPLITUDE_SHIFT) {
        case 1U: return 2U;
        case 2U: return 4U;
        case 3U: return 6U;
        default: return 8U;
    }
}

static uint8_t amplitudeCodeForKey(uint8_t key)
{
    switch (key) {
        case '2': return 1U;
        case '4': return 2U;
        case '6': return 3U;
        default:  return 0U;
    }
}

static uint8_t scanRawKey(void)
{
    static const uint32_t rowPins[4] = {
        KeyBoard_H1_PIN, KeyBoard_H2_PIN,
        KeyBoard_H3_PIN, KeyBoard_H4_PIN
    };
    static const uint32_t columnPins[4] = {
        KeyBoard_V1_PIN, KeyBoard_V2_PIN,
        KeyBoard_V3_PIN, KeyBoard_V4_PIN
    };
    static const uint8_t keyMap[16] = {
        '1', '2', '3', 'A',
        '4', '5', '6', 'B',
        '7', '8', '9', 'C',
        '*', '0', '#', 'D'
    };
    const uint32_t allRows = KeyBoard_H1_PIN | KeyBoard_H2_PIN |
                             KeyBoard_H3_PIN | KeyBoard_H4_PIN;
    uint8_t row;
    uint8_t column;
    uint8_t key = 0U;

    for (row = 0U; row < 4U; row++) {
        DL_GPIO_setPins(KeyBoard_PORT, allRows);
        DL_GPIO_clearPins(KeyBoard_PORT, rowPins[row]);
        /* The old BSP sampled immediately.  V1 could retain the low level
         * from H4 (*) and be read as H1/V1 (1) on the next scan.
         */
        delay_cycles(64U);
        for (column = 0U; column < 4U; column++) {
            if (DL_GPIO_readPins(KeyBoard_PORT, columnPins[column]) == 0U &&
                key == 0U)
                key = keyMap[row * 4U + column];
        }
    }
    DL_GPIO_setPins(KeyBoard_PORT, allRows);
    return key;
}

static uint8_t scanDebouncedKey(void)
{
    uint8_t rawKey = scanRawKey();

    if (rawKey != gRawCandidateKey) {
        gRawCandidateKey = rawKey;
        gRawCandidateCount = 1U;
        return 0U;
    }
    if (gRawCandidateCount < 3U)
        gRawCandidateCount++;
    if (gRawCandidateCount >= 3U && gRawStableKey != rawKey) {
        gRawStableKey = rawKey;
        return rawKey;
    }
    return 0U;
}

void Keypad_Control_RefreshDisplay(
    uint8_t outputMode, uint16_t phaseCentiDegrees)
{
    uint8_t line[17] = "PHASE:000.00 DEG";
    uint16_t degrees = phaseCentiDegrees / 100U;
    uint8_t amplitude = amplitudeValue(outputMode);

    gLastOutputMode = outputMode;
    gLastPhaseCentiDegrees = phaseCentiDegrees;

    if (gPhaseEntryActive) {
        uint8_t entry[17] = "VALUE:--- DEG   ";

        showLine(0U, "PHASE INPUT");
        if (gPhaseEntryValid)
            showThreeDigits(&entry[6], gPhaseEntryDegrees);
        OLED_ShowString(0U, 2U, entry);
        showLine(4U, "A=0    B=90");
        showLine(6U, "#=OK   C=CANCEL");
        return;
    }

    if (gTaskMenuActive) {
        showTaskMenu();
        return;
    }

    if (gTaskSnapshot.hasTask && !gTaskViewDismissed) {
        showTaskStatus();
        return;
    }

    if ((outputMode & MEASUREMENT_MODE_DDS_MASK) == 0U) {
        showLine(0U, "MODE:PIPELINE");
        showLine(2U, "AMP:---");
    } else {
        if ((outputMode & MEASUREMENT_MODE_WAVEFORM_SAW) != 0U)
            showLine(0U, "MODE:SAWTOOTH");
        else
            showLine(0U, (outputMode & MEASUREMENT_MODE_X2_MASK) != 0U ?
                            "MODE:DDS X2" : "MODE:DDS X1");
        line[0] = 'A';
        line[1] = 'M';
        line[2] = 'P';
        line[3] = ':';
        line[4] = (uint8_t) ('0' + amplitude);
        line[5] = '/';
        line[6] = '8';
        line[7] = 0U;
        showLine(2U, (const char *) line);
    }

    line[0] = 'P';
    line[1] = 'H';
    line[2] = 'A';
    line[3] = 'S';
    line[4] = 'E';
    line[5] = ':';
    showThreeDigits(&line[6], degrees);
    line[9] = '.';
    line[10] = (uint8_t) ('0' + (phaseCentiDegrees / 10U) % 10U);
    line[11] = (uint8_t) ('0' + phaseCentiDegrees % 10U);
    line[12] = ' ';
    line[13] = 'D';
    line[14] = 'E';
    line[15] = 'G';
    line[16] = 0U;
    OLED_ShowString(0U, 4U, line);

    showLine(6U, "#=AUTO  *=PHASE");
}

void Keypad_Control_Init(
    uint8_t outputMode, uint16_t phaseCentiDegrees)
{
    gKeyQueueHead = 0U;
    gKeyQueueTail = 0U;
    gMilliseconds = 0U;
    gRawCandidateKey = 0U;
    gRawStableKey = 0U;
    gRawCandidateCount = 0U;
    gPhaseEntryActive = false;
    gPhaseEntryValid = false;
    gPhaseEntryDegrees = 0U;
    gPhaseEntryDigits = 0U;
    gTaskMenuActive = false;
    gTaskRequestPending = false;
    gTaskRequestMode = PC_TASK_MODE_SERVICE;
    gTaskViewDismissed = false;
    gTaskSnapshot.serviceKnown = false;
    gTaskSnapshot.serviceReady = false;
    gTaskSnapshot.serviceState = PC_TASK_STATE_BOOTING;
    gTaskSnapshot.sessionId = 0U;
    gTaskSnapshot.hasTask = false;
    gTaskSnapshot.taskActive = false;

    OLED_Init();
    Keypad_Control_RefreshDisplay(outputMode, phaseCentiDegrees);

    SysTick->LOAD = CPUCLK_FREQ / KEYPAD_SCAN_HZ - 1U;
    SysTick->VAL = 0U;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk |
                    SysTick_CTRL_TICKINT_Msk |
                    SysTick_CTRL_ENABLE_Msk;
}

void SysTick_Handler(void)
{
    uint8_t key = scanDebouncedKey();
    uint8_t nextHead;

    gMilliseconds += 1000U / KEYPAD_SCAN_HZ;

    if (key == 0U)
        return;
    nextHead = (uint8_t) ((gKeyQueueHead + 1U) & KEYPAD_QUEUE_MASK);
    if (nextHead != gKeyQueueTail) {
        gKeyQueue[gKeyQueueHead] = key;
        gKeyQueueHead = nextHead;
    }
}

bool Keypad_Control_HandleKey(
    uint8_t key, uint8_t *outputMode, uint16_t *phaseCentiDegrees)
{
    if (gPhaseEntryActive) {
        if (key >= '0' && key <= '9') {
            uint16_t nextValue;

            if (gPhaseEntryDigits >= 3U)
                return false;
            nextValue = (uint16_t)
                (gPhaseEntryDegrees * 10U + (uint16_t) (key - '0'));
            if (nextValue > KEYPAD_MAX_PHASE_DEGREES)
                return false;
            gPhaseEntryDegrees = nextValue;
            gPhaseEntryDigits++;
            gPhaseEntryValid = true;
            return false;
        }
        if (key == 'A' || key == 'B') {
            gPhaseEntryDegrees = key == 'A' ? 0U : 90U;
            gPhaseEntryDigits = key == 'A' ? 0U : 2U;
            gPhaseEntryValid = true;
            return false;
        }
        if (key == '#') {
            if (!gPhaseEntryValid)
                return false;
            *phaseCentiDegrees =
                (uint16_t) (gPhaseEntryDegrees * 100U);
            gPhaseEntryActive = false;
            return true;
        }
        if (key == 'C') {
            gPhaseEntryActive = false;
            gPhaseEntryValid = false;
        }
        return false;
    }

    if (taskInputLocked())
        return false;

    if (gTaskMenuActive) {
        if (gTaskSnapshot.serviceReady &&
            (key == 'A' || key == 'B' || key == 'C')) {
            gTaskRequestMode = key == 'A' ? PC_TASK_MODE_DIAGONAL :
                               key == 'B' ? PC_TASK_MODE_ELLIPSE :
                                            PC_TASK_MODE_INFINITY;
            gTaskRequestPending = true;
            gTaskMenuActive = false;
        } else if (key == '#') {
            gTaskMenuActive = false;
        }
        return false;
    }

    if (key == '#') {
        gTaskMenuActive = true;
        gTaskViewDismissed = true;
        return false;
    }

    if (key == '*') {
        gPhaseEntryActive = true;
        gPhaseEntryValid = false;
        gPhaseEntryDegrees = 0U;
        gPhaseEntryDigits = 0U;
        return false;
    }

    if (key == 'D') {
        *outputMode &= (uint8_t) ~(MEASUREMENT_MODE_FREE_RUN |
                                   MEASUREMENT_MODE_WAVEFORM_SAW |
                                   MEASUREMENT_MODE_SAW_REVERSE);
        *outputMode |= MEASUREMENT_MODE_DDS_MASK;
        *outputMode ^= MEASUREMENT_MODE_X2_MASK;
        return true;
    }

    if (key == '2' || key == '4' || key == '6' || key == '8') {
        *outputMode &= (uint8_t) ~(MEASUREMENT_MODE_WAVEFORM_SAW |
                                   MEASUREMENT_MODE_SAW_REVERSE);
        *outputMode &= (uint8_t) ~MEASUREMENT_MODE_AMPLITUDE_MASK;
        *outputMode |= (uint8_t)
            (amplitudeCodeForKey(key) <<
             MEASUREMENT_MODE_AMPLITUDE_SHIFT);
        *outputMode |= MEASUREMENT_MODE_DDS_MASK;
        return true;
    }

    return false;
}

uint32_t Keypad_Control_Millis(void)
{
    uint32_t milliseconds;

    __disable_irq();
    milliseconds = gMilliseconds;
    __enable_irq();
    return milliseconds;
}

bool Keypad_Control_TakeTaskRequest(uint8_t *mode)
{
    if (!gTaskRequestPending)
        return false;
    *mode = gTaskRequestMode;
    gTaskRequestPending = false;
    return true;
}

void Keypad_Control_UpdateTask(const TaskControlSnapshot *snapshot)
{
    bool taskChanged = snapshot->hasTask &&
        (!gTaskSnapshot.hasTask ||
         snapshot->taskRequestId != gTaskSnapshot.taskRequestId ||
         snapshot->taskState != gTaskSnapshot.taskState ||
         snapshot->taskReason != gTaskSnapshot.taskReason);

    gTaskSnapshot = *snapshot;
    if (taskChanged)
        gTaskViewDismissed = false;
    Keypad_Control_RefreshDisplay(
        gLastOutputMode, gLastPhaseCentiDegrees);
}

bool Keypad_Control_Poll(
    uint8_t *outputMode, uint16_t *phaseCentiDegrees)
{
    uint8_t key;
    bool changed = false;
    bool hadEvent = false;

    while (1) {
        __disable_irq();
        if (gKeyQueueTail == gKeyQueueHead) {
            __enable_irq();
            break;
        }
        key = gKeyQueue[gKeyQueueTail];
        gKeyQueueTail =
            (uint8_t) ((gKeyQueueTail + 1U) & KEYPAD_QUEUE_MASK);
        __enable_irq();
        hadEvent = true;
        if (Keypad_Control_HandleKey(
                key, outputMode, phaseCentiDegrees))
            changed = true;
    }
    if (!hadEvent)
        return false;
    Keypad_Control_RefreshDisplay(*outputMode, *phaseCentiDegrees);
    return changed;
}
