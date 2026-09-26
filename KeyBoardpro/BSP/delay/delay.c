#include "ti_msp_dl_config.h"
#include "delay.h"

#define MCLK 80000000

void delay_us(uint32_t count)
{
	delay_cycles(MCLK / 1000000 * count);
}

void delay_ms(uint32_t count)
{
	delay_us(1000 * count);
}

void delay_s(uint32_t count)
{
	delay_ms(1000 * count);
}