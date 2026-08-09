#include <stdio.h>
#include <string.h>
#include "esp_mac.h"
#include "esp_log.h"

static const char *TAG = "MAC_ID";

/**
 * @brief Get and print the MAC address of the microcontroller.
 *
 * This function reads the base MAC address (WiFi Station) and prints it
 * in human-readable format (XX:XX:XX:XX:XX:XX).
 */
void app_main(void)
{
    uint8_t mac[6] = {0};

    // Read the base MAC address (WiFi Station interface)
    esp_err_t ret = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to read MAC address: %s", esp_err_to_name(ret));
        return;
    }

    // Print MAC address in hex format
    ESP_LOGI(TAG, "===== ESP32 MAC Address =====");
    ESP_LOGI(TAG, "WiFi Station MAC: %02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    // Also print other available MAC addresses
    // WiFi SoftAP MAC
    uint8_t ap_mac[6] = {0};
    if (esp_read_mac(ap_mac, ESP_MAC_WIFI_SOFTAP) == ESP_OK)
    {
        ESP_LOGI(TAG, "WiFi SoftAP MAC:  %02X:%02X:%02X:%02X:%02X:%02X",
                 ap_mac[0], ap_mac[1], ap_mac[2], ap_mac[3], ap_mac[4], ap_mac[5]);
    }

    // Bluetooth MAC
    uint8_t bt_mac[6] = {0};
    if (esp_read_mac(bt_mac, ESP_MAC_BT) == ESP_OK)
    {
        ESP_LOGI(TAG, "Bluetooth MAC:    %02X:%02X:%02X:%02X:%02X:%02X",
                 bt_mac[0], bt_mac[1], bt_mac[2], bt_mac[3], bt_mac[4], bt_mac[5]);
    }

    // Ethernet MAC (if available)
    uint8_t eth_mac[6] = {0};
    if (esp_read_mac(eth_mac, ESP_MAC_ETH) == ESP_OK)
    {
        ESP_LOGI(TAG, "Ethernet MAC:     %02X:%02X:%02X:%02X:%02X:%02X",
                 eth_mac[0], eth_mac[1], eth_mac[2], eth_mac[3], eth_mac[4], eth_mac[5]);
    }

    ESP_LOGI(TAG, "==============================");
}
