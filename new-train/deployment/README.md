# 水质模型部署程序

该目录实现固定 38 字节上行协议的 CRC 校验、五特征解包、随机森林预测、SQLite 永久存储和 Tkinter 实时显示。

## 协议

- 偏移 0-35：原始数据，长度字段为 36（`0x0024`）
- 偏移 36-37：CRC-16/Modbus，小端序
- CRC 参数：初始值 `0xFFFF`，多项式 `0xA001`，覆盖偏移 0-35
- 多字节字段均按小端序解析

示例 36 字节数据的 CRC 为 `0xBE71`，线路字节为 `71 BE`。

## 使用

安装依赖：

```powershell
python -m pip install -r new-train\deployment\requirements.txt
```

训练五特征模型：

```powershell
python new-train\deployment\train_model.py
```

启动实时程序：

```powershell
python new-train\deployment\app.py --port COM3 --baud 115200
```

串口默认为 8N1。程序会列出电脑当前可用串口，可在下拉框中选择或点击“刷新串口”重新扫描。状态栏显示有效包数、按 `cycle_id` 跳号计算的丢包数、CRC或格式错误造成的无效帧数及最后接收时间。“变化趋势”按钮会打开二级窗口，显示当前会话温度、pH、TDS、EC 和DO的实时变化曲线及Normal类别上下限；支持最近30、50、100点切换，鼠标悬停曲线点可查看采样序号和具体数值。

程序首次启动时会自动创建 `deployment/water_quality.db`，不会导入原有CSV。每次启动创建一个独立采集会话，串口断开重连仍属于当前会话；退出时只记录会话结束时间，不删除历史数据。实时表格最多显示当前会话最新500条，数据库中的记录永久保留。

“历史查询”窗口支持按采集会话、起止日期和水质等级组合筛选。界面最多预览最新5000条，点击“导出CSV”会将当前筛选条件命中的全部记录导出为CSV。CSV不再自动写入，也不再用于主存储。

可通过参数指定其他数据库文件，数据库文件仍建议放在 `new-train` 目录内：

```powershell
python new-train\deployment\app.py --db new-train\deployment\water_quality.db
```

## ESP32-S3 发送端

`esp-tx-test` 是完整的 ESP-IDF 工程，通过 ESP32-S3 的 UART1 每秒发送一帧。发送引脚为 GPIO17，串口参数为 115200、8N1。

电脑接收 GPIO17 数据需要 USB-TTL 转换器，交叉接线如下：

- ESP32-S3 GPIO17（TX）连接 USB-TTL RX
- ESP32-S3 GND 连接 USB-TTL GND
- 不要将 ESP32-S3 GPIO17 直接连接电脑 USB 数据线

在 VS Code 中单独打开 `deployment/esp-tx-test` 文件夹，再执行：

```powershell
idf.py set-target esp32s3
idf.py build
idf.py flash
```

USB-TTL 连接电脑后，在 Windows 设备管理器中查看它对应的 COM 口，然后在 `app.py` 界面中选择该端口和 115200 波特率。示例代码当前发送固定测量值；接入传感器后，只需替换 `build_packet()` 中五项赋值。

## 工程文件

- `protocol.py`：CRC、固定字段解析及流式拆包
- `runtime.py`：模型加载、推理、SQLite 会话存储、历史查询和CSV导出
- `app.py`：串口后台线程和 Tkinter 界面
- `esp-tx-test/`：ESP32-S3 UART1/GPIO17 发送端工程
- `train_model.py`：五特征模型复现与导出
- `water_quality_rf_5_features.joblib`：部署模型

运行测试：

```powershell
python -m unittest discover -s new-train\deployment -p "test_*.py" -v
```