#include "task_control.h"

#include "pc_link.h"

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    uint8_t sequence;
    uint8_t mode;
    uint32_t requestId;
    uint32_t sessionId;
} StartCall;

typedef struct {
    uint8_t sequence;
    uint32_t requestId;
    uint32_t sessionId;
} QueryCall;

static StartCall gStarts[8];
static QueryCall gQueries[8];
static unsigned int gStartCount;
static unsigned int gQueryCount;
static PCTaskStatus gStatuses[8];
static unsigned int gStatusHead;
static unsigned int gStatusTail;

bool PC_Link_SendTaskStart(
    uint8_t sequence, uint8_t mode, uint32_t requestId,
    uint32_t sessionId)
{
    StartCall *call = &gStarts[gStartCount++];
    call->sequence = sequence;
    call->mode = mode;
    call->requestId = requestId;
    call->sessionId = sessionId;
    return true;
}

bool PC_Link_SendTaskQuery(
    uint8_t sequence, uint32_t requestId, uint32_t sessionId)
{
    QueryCall *call = &gQueries[gQueryCount++];
    call->sequence = sequence;
    call->requestId = requestId;
    call->sessionId = sessionId;
    return true;
}

bool PC_Link_TakeTaskStatus(PCTaskStatus *status)
{
    if (gStatusTail == gStatusHead)
        return false;
    *status = gStatuses[gStatusTail++];
    return true;
}

static void pushStatus(
    uint8_t mode, uint32_t requestId, uint8_t state,
    uint8_t reason, uint32_t sessionId)
{
    PCTaskStatus *status = &gStatuses[gStatusHead++];
    memset(status, 0, sizeof(*status));
    status->version = PC_TASK_PROTOCOL_VERSION;
    status->mode = mode;
    status->requestId = requestId;
    status->state = state;
    status->reason = reason;
    status->controlMs = PC_TASK_CONTROL_MS_UNKNOWN;
    status->sessionId = sessionId;
}

int main(void)
{
    TaskControlSnapshot snapshot;

    Task_Control_Init(0U);
    assert(Task_Control_Poll(0U));
    assert(gQueryCount == 1U);
    assert(gQueries[0].requestId == 0U);
    assert(gQueries[0].sessionId == 0U);

    pushStatus(
        PC_TASK_MODE_SERVICE, 0U, PC_TASK_STATE_READY,
        PC_TASK_REASON_NONE, 0x12345678UL);
    assert(Task_Control_Poll(10U));
    Task_Control_GetSnapshot(&snapshot);
    assert(snapshot.serviceReady);
    assert(snapshot.sessionId == 0x12345678UL);

    assert(Task_Control_Start(PC_TASK_MODE_INFINITY, 20U));
    assert(gStartCount == 1U);
    assert(gStarts[0].mode == PC_TASK_MODE_INFINITY);
    assert(gStarts[0].requestId == 1U);
    assert(gStarts[0].sessionId == 0x12345678UL);
    Task_Control_GetSnapshot(&snapshot);
    assert(snapshot.taskState == TASK_CONTROL_STATE_SENDING);

    Task_Control_Poll(319U);
    assert(gStartCount == 1U);
    Task_Control_Poll(320U);
    assert(gStartCount == 2U);
    assert(gStarts[1].requestId == gStarts[0].requestId);
    assert(gStarts[1].mode == gStarts[0].mode);
    assert(gStarts[1].sessionId == gStarts[0].sessionId);

    pushStatus(
        PC_TASK_MODE_INFINITY, 1U, PC_TASK_STATE_ACCEPTED,
        PC_TASK_REASON_NONE, 0x12345678UL);
    assert(Task_Control_Poll(321U));
    Task_Control_GetSnapshot(&snapshot);
    assert(snapshot.taskActive);
    assert(snapshot.taskState == PC_TASK_STATE_ACCEPTED);

    Task_Control_Poll(519U);
    assert(gQueryCount == 1U);
    Task_Control_Poll(520U);
    assert(gQueryCount == 2U);
    assert(gQueries[1].requestId == 1U);
    assert(gQueries[1].sessionId == 0x12345678UL);

    pushStatus(
        PC_TASK_MODE_INFINITY, 1U, PC_TASK_STATE_RUNNING,
        PC_TASK_REASON_NONE, 0x12345678UL);
    assert(Task_Control_Poll(600U));
    Task_Control_GetSnapshot(&snapshot);
    assert(snapshot.taskState == PC_TASK_STATE_RUNNING);

    pushStatus(
        PC_TASK_MODE_INFINITY, 1U, PC_TASK_STATE_SUCCEEDED,
        PC_TASK_REASON_NONE, 0x12345678UL);
    assert(Task_Control_Poll(1000U));
    Task_Control_GetSnapshot(&snapshot);
    assert(!snapshot.taskActive);
    assert(snapshot.serviceReady);

    assert(Task_Control_Start(PC_TASK_MODE_DIAGONAL, 1001U));
    assert(gStarts[gStartCount - 1U].requestId == 2U);
    pushStatus(
        PC_TASK_MODE_SERVICE, 0U, PC_TASK_STATE_READY,
        PC_TASK_REASON_NONE, 0x87654321UL);
    assert(Task_Control_Poll(1100U));
    Task_Control_GetSnapshot(&snapshot);
    assert(!snapshot.hasTask);
    assert(snapshot.sessionId == 0x87654321UL);

    Task_Control_Poll(1750U);
    assert(gQueries[gQueryCount - 1U].requestId == 1U);
    Task_Control_Poll(1751U);
    assert(gQueries[gQueryCount - 1U].requestId == 0U);
    assert(gQueries[gQueryCount - 1U].sessionId == 0U);
    return 0;
}
