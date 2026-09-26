#include "ti_msp_dl_config.h"

#include "fft_analyzer.h"
#include "fpga_capture.h"
#include "measurement_config.h"
#include "msp_uart_link.h"
#include "pc_link.h"

#include <stdint.h>

static uint16_t gSamples[MEASUREMENT_FFT_SIZE];

int main(void)
{
    MeasurementPeak peaks[MEASUREMENT_PEAK_COUNT];
    FPGA_CaptureStatus status;
    uint16_t flags;
    uint8_t sequence;

    SYSCFG_DL_init();
    MSP_UART_Link_Init();
    FFT_Analyzer_Init();
    PC_Link_Init();

    while (1) {
        if (!PC_Link_TakeCaptureRequest(&sequence)) {
            __WFI();
            continue;
        }

        status = FPGA_Capture_Run(sequence, gSamples, &flags);
        if (status != FPGA_CAPTURE_OK) {
            PC_Link_SendError(sequence, (uint16_t) status);
            continue;
        }

        FFT_Analyzer_Process(gSamples, peaks);
        PC_Link_SendResult(
            sequence, flags, peaks, gSamples,
            FFT_Analyzer_GetSpectrum());
    }
}
