计算复核包

inputs保留本稿采用的冻结输入与输出。48组年度、七区域、18,819组合、36组与9组检验均包含在内。

使用Python 3运行：python reconstruct_audit.py
该脚本仅依赖Python标准库，从model_spec.json独立重算并与冻结输出比较，不调用生产模型。核对结果写入results目录。calculate_robustness.py保留为原生产脚本及哈希记录，不作为本包的运行入口。

数值一致性检验不等同于参数经验校准；具体推广率、需求比例、设备参照功率和未来乘数的证据属性见主稿及修改说明。
