#include "ti_msp_dl_config.h"

#include "dpll_fpga_link.h"
#include "msp_uart_link.h"
#include "pc_dpll_link.h"

#include <stdbool.h>
#include <stdint.h>

#define DEFAULT_PHASE_CDEG       (0U)
#define DEFAULT_CALIBRATION_WORD (0U)
#define DEFAULT_AMPLITUDE_CODE   (2143U)

int main(void)
{
    DPLL_FPGA_Status status;
    uint16_t targetPhase = DEFAULT_PHASE_CDEG;
    uint16_t requestedPhase;
    uint8_t pcSequence;
    uint8_t fpgaSequence = 0U;

    SYSCFG_DL_init();
    MSP_UART_Link_Init();
    PC_DPLL_Link_Init();

    fpgaSequence++;
    DPLL_FPGA_SendConfig(
        fpgaSequence, targetPhase, DEFAULT_CALIBRATION_WORD,
        DEFAULT_AMPLITUDE_CODE, true);

    while (1) {
        if (PC_DPLL_Link_TakePhaseRequest(&requestedPhase)) {
            targetPhase = requestedPhase;
            fpgaSequence++;
            DPLL_FPGA_SendConfig(
                fpgaSequence, targetPhase, DEFAULT_CALIBRATION_WORD,
                DEFAULT_AMPLITUDE_CODE, true);
        }

        if (PC_DPLL_Link_TakeStatusRequest(&pcSequence)) {
            DPLL_FPGA_Result result;
            fpgaSequence++;
            result = DPLL_FPGA_ReadStatus(fpgaSequence, &status);
            if (result == DPLL_FPGA_OK)
                PC_DPLL_Link_SendStatus(pcSequence, &status);
            else
                PC_DPLL_Link_SendError(pcSequence, (uint16_t) result);
        }

        if (PC_DPLL_Link_TakeRawStatusRequest(&pcSequence)) {
            DPLL_FPGA_Result result;
            fpgaSequence++;
            result = DPLL_FPGA_ReadStatus(fpgaSequence, &status);
            if (result == DPLL_FPGA_OK)
                PC_DPLL_Link_SendRawStatus(pcSequence, &status);
            else
                PC_DPLL_Link_SendError(pcSequence, (uint16_t) result);
        }
    }
}
