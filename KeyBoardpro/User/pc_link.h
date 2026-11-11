#ifndef PC_LINK_H
#define PC_LINK_H

#include "measurement_config.h"

#include <stdbool.h>
#include <stdint.h>

#define PC_TASK_PROTOCOL_VERSION (1U)

#define PC_TASK_MODE_SERVICE     (0U)
#define PC_TASK_MODE_DIAGONAL    (1U)
#define PC_TASK_MODE_ELLIPSE     (2U)
#define PC_TASK_MODE_INFINITY    (3U)

#define PC_TASK_STATE_BOOTING    (0U)
#define PC_TASK_STATE_READY      (1U)
#define PC_TASK_STATE_ACCEPTED   (2U)
#define PC_TASK_STATE_RUNNING    (3U)
#define PC_TASK_STATE_SUCCEEDED  (4U)
#define PC_TASK_STATE_FAILED     (5U)
#define PC_TASK_STATE_BUSY       (6U)
#define PC_TASK_STATE_REJECTED   (7U)
#define PC_TASK_STATE_FAULT      (8U)

#define PC_TASK_REASON_NONE          (0U)
#define PC_TASK_REASON_FORMAT        (1U)
#define PC_TASK_REASON_SESSION       (2U)
#define PC_TASK_REASON_BUSY          (3U)
#define PC_TASK_REASON_TIMEOUT       (4U)
#define PC_TASK_REASON_FREQUENCY     (5U)
#define PC_TASK_REASON_HARDWARE      (6U)
#define PC_TASK_REASON_VERIFY_OUTPUT (7U)
#define PC_TASK_REASON_CAPACITY      (8U)
#define PC_TASK_REASON_ID_CONFLICT   (9U)
#define PC_TASK_REASON_NOT_READY     (10U)
#define PC_TASK_REASON_NOT_FOUND     (11U)

#define PC_TASK_CONTROL_MS_UNKNOWN (0xFFFFFFFFUL)

typedef struct {
    uint8_t sequence;
    uint8_t version;
    uint8_t mode;
    uint32_t requestId;
    uint8_t state;
    uint8_t reason;
    uint32_t elapsedMs;
    uint32_t stableMs;
    uint32_t controlMs;
    uint32_t sessionId;
} PCTaskStatus;

void PC_Link_Init(void);
bool PC_Link_TakePhaseRequest(uint16_t *phaseCentiDegrees);
bool PC_Link_TakeModeRequest(uint8_t *mode);
bool PC_Link_TakeTargetRequest(
    uint8_t *sequence, uint32_t *frequencyMilliHz,
    uint16_t *phaseCentiDegrees, uint8_t *amplitudeCode,
    uint8_t *waveform, uint16_t *decayMicroseconds);
bool PC_Link_TakeDoubleRequest(uint8_t *sequence);
bool PC_Link_TakeTaskStatus(PCTaskStatus *status);
bool PC_Link_SendTaskStart(
    uint8_t sequence, uint8_t mode, uint32_t requestId,
    uint32_t sessionId);
bool PC_Link_SendTaskQuery(
    uint8_t sequence, uint32_t requestId, uint32_t sessionId);
void PC_Link_SendTargetAck(
    uint8_t sequence, uint32_t frequencyMilliHz,
    uint16_t phaseCentiDegrees, uint8_t amplitudeCode, uint8_t mode,
    uint16_t decayMicroseconds);
void PC_Link_SendError(uint8_t sequence, uint16_t errorCode);
void PC_Link_SendDoubleAck(
    uint8_t sequence, uint32_t appliedSampleCounter,
    uint32_t oldPhaseStep, uint32_t newPhaseStep, uint32_t commitPhase);

#endif
