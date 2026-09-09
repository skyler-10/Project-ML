# ESP32-S3 UART发送端

## 接线

- GPIO17（TX）连接 USB-TTL 转换器的 RX
- GND 连接 USB-TTL 转换器的 GND
- 串口参数：115200、8N1

## 编译

在 VS Code 中单独打开本目录 `esp-tx-test`，然后通过 ESP-IDF 扩展选择 `esp32s3` 目标并构建。命令行方式如下：

```powershell
idf.py set-target esp32s3
idf.py build
idf.py flash
```

源码位于 `main/esp32-tx.c`，通过 UART1 的 GPIO17 每秒发送一个38字节数据包。