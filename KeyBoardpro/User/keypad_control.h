#ifndef KEYPAD_CONTROL_H
#define KEYPAD_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

void Keypad_Control_Init(
    uint8_t outputMode, uint16_t phaseCentiDegrees);
bool Keypad_Control_Poll(
    uint8_t *outputMode, uint16_t *phaseCentiDegrees);
bool Keypad_Control_HandleKey(
    uint8_t key, uint8_t *outputMode, uint16_t *phaseCentiDegrees);
void Keypad_Control_RefreshDisplay(
    uint8_t outputMode, uint16_t phaseCentiDegrees);

#endif
