# 参考资源

本工程的CORDIC向量模式实现参考：

```text
E:/eishero2q/学长资料/Cordic_Atan2.v
author: binbin
```

工程内的`rtl/cordic_atan2.v`已增加同步复位、有效信号流水线、模块重命名和
接口说明。原始`Cordic.v`未直接使用；正交载波和输出正弦改用双口ROM，减少
EP4CE6上的寄存器和组合逻辑占用。

`LMS_*`、`IIR_Designer.v`和`LFSR_Generator.v`与当前单正弦DPLL主链无关，
因此没有复制进工程。

