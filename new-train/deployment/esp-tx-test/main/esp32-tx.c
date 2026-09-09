#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_check.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"


#define PACKET_DATA_LENGTH 36U
#define PACKET_TOTAL_LENGTH 38U
#define SEND_INTERVAL_MS 1000U
#define TX_UART UART_NUM_1
#define TX_GPIO GPIO_NUM_17
#define TX_BAUD_RATE 115200


typedef struct __attribute__((packed)) {
    uint8_t header[2];
    uint8_t protocol_version;
    uint16_t packet_length;
    uint32_t cycle_id;
    uint32_t timestamp_ms;
    float temp_c;
    float ph;
    uint16_t tds_mgl;
    float ec_uscm;
    float do_mgl;
    uint8_t do_status;
    uint8_t ec_status;
    uint8_t ph_status;
    uint8_t tail[2];
} water_quality_data_t;

typedef struct __attribute__((packed)) {
    water_quality_data_t data;
    uint16_t crc;
} water_quality_packet_t;


_Static_assert(sizeof(float) == 4, "Protocol requires 32-bit float");
_Static_assert(sizeof(water_quality_data_t) == PACKET_DATA_LENGTH,
               "Water quality data must be 36 bytes");
_Static_assert(sizeof(water_quality_packet_t) == PACKET_TOTAL_LENGTH,
               "Complete packet must be 38 bytes");
_Static_assert(offsetof(water_quality_data_t, temp_c) == 13,
               "Temperature offset must be 13");
_Static_assert(offsetof(water_quality_data_t, ph) == 17,
               "pH offset must be 17");
_Static_assert(offsetof(water_quality_data_t, tds_mgl) == 21,
               "TDS offset must be 21");
_Static_assert(offsetof(water_quality_data_t, ec_uscm) == 23,
               "EC offset must be 23");
_Static_assert(offsetof(water_quality_data_t, do_mgl) == 27,
               "DO offset must be 27");


static uint16_t crc16_modbus(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFF;

    for (size_t index = 0; index < length; ++index) {
        crc ^= data[index];
        for (uint8_t bit = 0; bit < 8; ++bit) {
            crc = (crc & 1U) ? (uint16_t)((crc >> 1U) ^ 0xA001U)
                             : (uint16_t)(crc >> 1U);
        }
    }
    return crc;
}


static float random_reading(float base_value)
{
    const float variation = ((float)esp_random() / UINT32_MAX) * 0.10F - 0.05F;
    return base_value * (1.0F + variation);
}


static void build_packet(water_quality_packet_t *packet, uint32_t cycle_id)
{
    memset(packet, 0, sizeof(*packet));

    packet->data.header[0] = 0xAA;
    packet->data.header[1] = 0x55;
    packet->data.protocol_version = 0x02;
    packet->data.packet_length = PACKET_DATA_LENGTH;
    packet->data.cycle_id = cycle_id;
    packet->data.timestamp_ms = (uint32_t)(esp_timer_get_time() / 1000ULL);

    packet->data.temp_c = random_reading(25.0F);
    packet->data.ph = random_reading(8.10F);
    packet->data.tds_mgl = (uint16_t)random_reading(350.0F);
    packet->data.ec_uscm = random_reading(520.5F);
    packet->data.do_mgl = random_reading(6.75F);
    packet->data.do_status = 0x00;
    packet->data.ec_status = 0x00;
    packet->data.ph_status = 0x00;
    packet->data.tail[0] = 0x0D;
    packet->data.tail[1] = 0x0A;

    packet->crc = crc16_modbus((const uint8_t *)&packet->data,
                               sizeof(packet->data));
}


void app_main(void)
{
    const uart_config_t uart_config = {
        .baud_rate = TX_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(TX_UART, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(TX_UART, TX_GPIO, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(TX_UART, 256, 256, 0, NULL, 0));

    uint32_t cycle_id = 1;
    while (true) {
        water_quality_packet_t packet;
        build_packet(&packet, cycle_id++);

        const int written = uart_write_bytes(TX_UART, &packet, sizeof(packet));
        if (written != (int)sizeof(packet)) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        ESP_ERROR_CHECK(uart_wait_tx_done(TX_UART, pdMS_TO_TICKS(100)));
        vTaskDelay(pdMS_TO_TICKS(SEND_INTERVAL_MS));
    }
}