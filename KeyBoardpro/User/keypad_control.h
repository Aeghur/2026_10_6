#ifndef KEYPAD_CONTROL_H
#define KEYPAD_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

#include "task_control.h"

void Keypad_Control_Init(
    uint8_t outputMode, uint16_t phaseCentiDegrees);
bool Keypad_Control_Poll(
    uint8_t *outputMode, uint16_t *phaseCentiDegrees);
bool Keypad_Control_HandleKey(
    uint8_t key, uint8_t *outputMode, uint16_t *phaseCentiDegrees);
void Keypad_Control_RefreshDisplay(
    uint8_t outputMode, uint16_t phaseCentiDegrees);
uint32_t Keypad_Control_Millis(void);
bool Keypad_Control_TakeTaskRequest(uint8_t *mode);
void Keypad_Control_UpdateTask(const TaskControlSnapshot *snapshot);

#endif
