#ifndef TASK_CONTROL_H
#define TASK_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

#define TASK_CONTROL_STATE_SENDING (0x80U)

typedef struct {
    bool serviceKnown;
    bool serviceReady;
    uint8_t serviceState;
    uint32_t sessionId;
    bool hasTask;
    bool taskActive;
    uint8_t taskMode;
    uint8_t taskState;
    uint8_t taskReason;
    uint32_t taskRequestId;
    uint32_t elapsedMs;
    uint32_t stableMs;
    uint32_t controlMs;
} TaskControlSnapshot;

void Task_Control_Init(uint32_t nowMs);
bool Task_Control_Poll(uint32_t nowMs);
bool Task_Control_Start(uint8_t mode, uint32_t nowMs);
void Task_Control_GetSnapshot(TaskControlSnapshot *snapshot);

#endif
