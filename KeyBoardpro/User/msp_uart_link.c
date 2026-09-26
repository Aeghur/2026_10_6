#include "msp_uart_link.h"

static const DL_UART_Main_ClockConfig gPcClock = {
    .clockSel = DL_UART_MAIN_CLOCK_BUSCLK,
    .divideRatio = DL_UART_MAIN_CLOCK_DIVIDE_RATIO_1
};

static const DL_UART_Main_Config gPcConfig = {
    .mode = DL_UART_MAIN_MODE_NORMAL,
    .direction = DL_UART_MAIN_DIRECTION_TX_RX,
    .flowControl = DL_UART_MAIN_FLOW_CONTROL_NONE,
    .parity = DL_UART_MAIN_PARITY_NONE,
    .wordLength = DL_UART_MAIN_WORD_LENGTH_8_BITS,
    .stopBits = DL_UART_MAIN_STOP_BITS_ONE
};

static const DL_UART_Main_ClockConfig gFpgaClock = {
    .clockSel = DL_UART_MAIN_CLOCK_BUSCLK,
    .divideRatio = DL_UART_MAIN_CLOCK_DIVIDE_RATIO_1
};

static const DL_UART_Main_Config gFpgaConfig = {
    .mode = DL_UART_MAIN_MODE_NORMAL,
    .direction = DL_UART_MAIN_DIRECTION_TX_RX,
    .flowControl = DL_UART_MAIN_FLOW_CONTROL_NONE,
    .parity = DL_UART_MAIN_PARITY_NONE,
    .wordLength = DL_UART_MAIN_WORD_LENGTH_8_BITS,
    .stopBits = DL_UART_MAIN_STOP_BITS_ONE
};

void MSP_UART_Link_Init(void)
{
    DL_UART_Main_reset(PC_UART_INST);
    DL_UART_Main_reset(FPGA_UART_INST);
    DL_UART_Main_enablePower(PC_UART_INST);
    DL_UART_Main_enablePower(FPGA_UART_INST);
    delay_cycles(POWER_STARTUP_DELAY);

    DL_GPIO_initPeripheralOutputFunction(
        IOMUX_PINCM17, IOMUX_PINCM17_PF_UART1_TX);
    DL_GPIO_initPeripheralInputFunction(
        IOMUX_PINCM18, IOMUX_PINCM18_PF_UART1_RX);
    DL_GPIO_initPeripheralOutputFunction(
        IOMUX_PINCM27, IOMUX_PINCM27_PF_UART4_TX);
    DL_GPIO_initPeripheralInputFunction(
        IOMUX_PINCM28, IOMUX_PINCM28_PF_UART4_RX);

    DL_UART_Main_setClockConfig(PC_UART_INST, &gPcClock);
    DL_UART_Main_init(PC_UART_INST, &gPcConfig);
    DL_UART_Main_setOversampling(
        PC_UART_INST, DL_UART_OVERSAMPLING_RATE_16X);
    DL_UART_Main_setBaudRateDivisor(PC_UART_INST, 2U, 32U);
    DL_UART_Main_enableFIFOs(PC_UART_INST);
    DL_UART_Main_setRXFIFOThreshold(
        PC_UART_INST, DL_UART_RX_FIFO_LEVEL_ONE_ENTRY);
    DL_UART_Main_setTXFIFOThreshold(
        PC_UART_INST, DL_UART_TX_FIFO_LEVEL_ONE_ENTRY);
    DL_UART_Main_enableInterrupt(
        PC_UART_INST, DL_UART_MAIN_INTERRUPT_RX);
    DL_UART_Main_enable(PC_UART_INST);

    DL_UART_Main_setClockConfig(FPGA_UART_INST, &gFpgaClock);
    DL_UART_Main_init(FPGA_UART_INST, &gFpgaConfig);
    DL_UART_Main_setOversampling(
        FPGA_UART_INST, DL_UART_OVERSAMPLING_RATE_16X);
    DL_UART_Main_setBaudRateDivisor(FPGA_UART_INST, 2U, 32U);
    DL_UART_Main_enableFIFOs(FPGA_UART_INST);
    DL_UART_Main_setRXFIFOThreshold(
        FPGA_UART_INST, DL_UART_RX_FIFO_LEVEL_ONE_ENTRY);
    DL_UART_Main_setTXFIFOThreshold(
        FPGA_UART_INST, DL_UART_TX_FIFO_LEVEL_ONE_ENTRY);
    DL_UART_Main_enable(FPGA_UART_INST);
}
