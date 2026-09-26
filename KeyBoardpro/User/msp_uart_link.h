#ifndef MSP_UART_LINK_H
#define MSP_UART_LINK_H

#include "ti_msp_dl_config.h"

#define PC_UART_INST                 UART1
#define PC_UART_INST_INT_IRQN        UART1_INT_IRQn
#define FPGA_UART_INST               UART4

void MSP_UART_Link_Init(void);

#endif
