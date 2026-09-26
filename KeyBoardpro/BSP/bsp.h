#ifndef BSP_H
#define BSP_H

/******************系统头文件***************/
#include <ti/devices/msp/msp.h>
#include <ti/driverlib/driverlib.h>
#include <ti/driverlib/m0p/dl_core.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include "math.h"
#include "ti_msp_dl_config.h"
/*******************************************/
#define u8 unsigned char
#define u32 unsigned int
/******************用户自定义头文件*********/
#include "delay.h"
#include "spi0_oled.h"
#include "MatrixKey.h"
/*******************************************/

#endif
