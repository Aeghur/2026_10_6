#include "ti_msp_dl_config.h"
#include "delay.h"
#include "MatrixKey.h"

// 4x4 矩阵键盘消抖实现
// 调用约定：外部以固定周期（建议 5~20ms）调用 MatrixKey()。
// 返回值：仅在某个按键从未按下->稳定按下 的瞬间返回该键字符，其余时间返回 0。

static void Openoneline(GPIO_Regs* gpio,uint32_t* pins,uint32_t num)
{
	uint32_t i;
	for(i=1;i<=4;i++)
	{
		if(i==num) DL_GPIO_clearPins(KeyBoard_PORT,pins[i-1]);
		else DL_GPIO_setPins(KeyBoard_PORT,pins[i-1]);
	}
}

unsigned char MatrixKey()
{
	// 键盘映射，行主序（row 0..3, col 0..3）
	static const unsigned char keymap[16] = {
		'1','2','3','A',
		'4','5','6','B',
		'7','8','9','C',
		'*','0','#','D'
	};

	// 每键的消抖计数和稳定状态
	#define DEBOUNCE_MAX 4
	static uint8_t cnt[16] = {0};
	static uint8_t stable[16] = {0}; // 0: 松开, 1: 按下

	uint32_t h_pins[4] = {KeyBoard_H1_PIN, KeyBoard_H2_PIN, KeyBoard_H3_PIN, KeyBoard_H4_PIN};
	unsigned char event_key = 0;

	// 扫描所有按键，更新计数器
	for (uint8_t row = 0; row < 4; ++row) {
		Openoneline(KeyBoard_PORT, h_pins, row+1);
		// 读取 4 列
		uint8_t col_state[4];
		col_state[0] = (DL_GPIO_readPins(KeyBoard_PORT, KeyBoard_V1_PIN) == 0) ? 1 : 0;
		col_state[1] = (DL_GPIO_readPins(KeyBoard_PORT, KeyBoard_V2_PIN) == 0) ? 1 : 0;
		col_state[2] = (DL_GPIO_readPins(KeyBoard_PORT, KeyBoard_V3_PIN) == 0) ? 1 : 0;
		col_state[3] = (DL_GPIO_readPins(KeyBoard_PORT, KeyBoard_V4_PIN) == 0) ? 1 : 0;

		for (uint8_t col = 0; col < 4; ++col) {
			uint8_t idx = row*4 + col;
			if (col_state[col]) {
				if (cnt[idx] < DEBOUNCE_MAX) cnt[idx]++;
			} else {
				if (cnt[idx] > 0) cnt[idx]--;
			}

			// 达到消抖阈值且此前处于松开状态 -> 触发按下事件（只返回第一个检测到的事件）
			if (cnt[idx] == DEBOUNCE_MAX && stable[idx] == 0) {
				stable[idx] = 1;
				if (event_key == 0) event_key = keymap[idx];
			}

			// 完全松开
			if (cnt[idx] == 0 && stable[idx] == 1) {
				stable[idx] = 0;
			}
		}
	}

	return event_key; // 0 表示无新事件
}