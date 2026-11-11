#include "task_control.h"

#include "pc_link.h"

#define TASK_START_RETRY_MS   (300UL)
#define TASK_ACTIVE_QUERY_MS  (500UL)
#define TASK_SERVICE_QUERY_MS (750UL)

static TaskControlSnapshot gSnapshot;
static uint32_t gNextRequestId;
static uint32_t gLastStartMs;
static uint32_t gLastQueryMs;
static uint8_t gSequence;
static bool gStartAwaiting;
static bool gTaskTracking;
static bool gChanged;

static uint8_t nextSequence(void)
{
    gSequence++;
    if (gSequence == 0U)
        gSequence = 1U;
    return gSequence;
}

static bool isTerminalState(uint8_t state)
{
    return state == PC_TASK_STATE_SUCCEEDED ||
           state == PC_TASK_STATE_FAILED ||
           state == PC_TASK_STATE_BUSY ||
           state == PC_TASK_STATE_REJECTED ||
           state == PC_TASK_STATE_FAULT;
}

static bool serviceCanStart(uint8_t state)
{
    return state == PC_TASK_STATE_READY ||
           state == PC_TASK_STATE_SUCCEEDED ||
           state == PC_TASK_STATE_FAILED;
}

static void cancelTaskForSessionChange(void)
{
    gStartAwaiting = false;
    gTaskTracking = false;
    gSnapshot.hasTask = false;
    gSnapshot.taskActive = false;
    gNextRequestId = 1U;
}

static void consumeStatus(const PCTaskStatus *status)
{
    bool sessionChanged =
        gSnapshot.serviceKnown && status->sessionId != gSnapshot.sessionId;

    if (sessionChanged)
        cancelTaskForSessionChange();

    gSnapshot.serviceKnown = true;
    gSnapshot.sessionId = status->sessionId;
    gSnapshot.serviceState = status->state;
    gSnapshot.serviceReady = serviceCanStart(status->state) &&
                             !gStartAwaiting && !gTaskTracking;

    if (gSnapshot.hasTask &&
        status->sessionId == gSnapshot.sessionId &&
        status->requestId == gSnapshot.taskRequestId &&
        status->mode == gSnapshot.taskMode) {
        gSnapshot.taskState = status->state;
        gSnapshot.taskReason = status->reason;
        gSnapshot.elapsedMs = status->elapsedMs;
        gSnapshot.stableMs = status->stableMs;
        gSnapshot.controlMs = status->controlMs;

        if (status->state == PC_TASK_STATE_ACCEPTED ||
            status->state == PC_TASK_STATE_RUNNING) {
            gStartAwaiting = false;
            gTaskTracking = true;
            gSnapshot.taskActive = true;
            gSnapshot.serviceReady = false;
        } else if (isTerminalState(status->state)) {
            gStartAwaiting = false;
            gTaskTracking = false;
            gSnapshot.taskActive = false;
            gSnapshot.serviceReady = serviceCanStart(status->state);
        }
    }
    gChanged = true;
}

void Task_Control_Init(uint32_t nowMs)
{
    gSnapshot.serviceKnown = false;
    gSnapshot.serviceReady = false;
    gSnapshot.serviceState = PC_TASK_STATE_BOOTING;
    gSnapshot.sessionId = 0U;
    gSnapshot.hasTask = false;
    gSnapshot.taskActive = false;
    gSnapshot.taskMode = PC_TASK_MODE_SERVICE;
    gSnapshot.taskState = PC_TASK_STATE_BOOTING;
    gSnapshot.taskReason = PC_TASK_REASON_NONE;
    gSnapshot.taskRequestId = 0U;
    gSnapshot.elapsedMs = 0U;
    gSnapshot.stableMs = 0U;
    gSnapshot.controlMs = PC_TASK_CONTROL_MS_UNKNOWN;
    gNextRequestId = 1U;
    gSequence = 0U;
    gStartAwaiting = false;
    gTaskTracking = false;
    gLastStartMs = nowMs;
    gLastQueryMs = nowMs - TASK_SERVICE_QUERY_MS;
    gChanged = true;
}

bool Task_Control_Start(uint8_t mode, uint32_t nowMs)
{
    uint32_t requestId;

    if (mode < PC_TASK_MODE_DIAGONAL ||
        mode > PC_TASK_MODE_INFINITY ||
        !gSnapshot.serviceKnown || !gSnapshot.serviceReady ||
        gSnapshot.sessionId == 0U || gStartAwaiting || gTaskTracking)
        return false;

    requestId = gNextRequestId++;
    if (gNextRequestId == 0U)
        gNextRequestId = 1U;
    if (!PC_Link_SendTaskStart(
            nextSequence(), mode, requestId, gSnapshot.sessionId))
        return false;

    gSnapshot.hasTask = true;
    gSnapshot.taskActive = true;
    gSnapshot.taskMode = mode;
    gSnapshot.taskState = TASK_CONTROL_STATE_SENDING;
    gSnapshot.taskReason = PC_TASK_REASON_NONE;
    gSnapshot.taskRequestId = requestId;
    gSnapshot.elapsedMs = 0U;
    gSnapshot.stableMs = 0U;
    gSnapshot.controlMs = PC_TASK_CONTROL_MS_UNKNOWN;
    gSnapshot.serviceReady = false;
    gStartAwaiting = true;
    gTaskTracking = false;
    gLastStartMs = nowMs;
    gLastQueryMs = nowMs;
    gChanged = true;
    return true;
}

bool Task_Control_Poll(uint32_t nowMs)
{
    PCTaskStatus status;
    bool changed;

    while (PC_Link_TakeTaskStatus(&status))
        consumeStatus(&status);

    if (gStartAwaiting &&
        (uint32_t) (nowMs - gLastStartMs) >= TASK_START_RETRY_MS) {
        PC_Link_SendTaskStart(
            nextSequence(), gSnapshot.taskMode,
            gSnapshot.taskRequestId, gSnapshot.sessionId);
        gLastStartMs = nowMs;
    } else if (gTaskTracking &&
               (uint32_t) (nowMs - gLastQueryMs) >= TASK_ACTIVE_QUERY_MS) {
        PC_Link_SendTaskQuery(
            nextSequence(), gSnapshot.taskRequestId, gSnapshot.sessionId);
        gLastQueryMs = nowMs;
    } else if (!gStartAwaiting && !gTaskTracking &&
               (uint32_t) (nowMs - gLastQueryMs) >=
                   TASK_SERVICE_QUERY_MS) {
        PC_Link_SendTaskQuery(nextSequence(), 0U, 0U);
        gLastQueryMs = nowMs;
    }

    changed = gChanged;
    gChanged = false;
    return changed;
}

void Task_Control_GetSnapshot(TaskControlSnapshot *snapshot)
{
    *snapshot = gSnapshot;
}
