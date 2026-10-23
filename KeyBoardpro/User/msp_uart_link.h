#ifndef MSP_UART_LINK_H
#define MSP_UART_LINK_H

#include "ti_msp_dl_config.h"

#define PC_UART_INST                 UART1
#define PC_UART_INST_INT_IRQN        UART1_INT_IRQn
#define FPGA_UART_INST               UART4

/*
 * UART1 host connector selection:
 *   1: PB21/PB22, connected to the Radxa ROCK 4D UART4-M1 header.
 *   0: PB4/PB5, the original USB-TTL/PC test connector.
 * The wire protocol and 1 Mbaud UART settings are identical in both cases.
 */
#define HOST_UART_USE_RADXA_PINS     (1U)

void MSP_UART_Link_Init(void);

#endif
