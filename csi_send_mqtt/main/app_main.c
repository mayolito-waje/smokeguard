/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* CSI sender that follows the hotspot instead of pinning a channel.

   This is csi_send with one assumption removed: that the radio has a fixed
   channel and width. csi_send has to pin both at compile time because it never
   associates with anything, so whenever the hotspot lands on a different
   channel the sender has to be rebuilt and reflashed to match -- and until it
   is, the receiver is deaf and the only symptom is silence.

   This variant joins the same hotspot the receiver is on. An access point gives
   every associated station the same channel and width, so both ends agree by
   construction: nothing to keep in sync, and nothing to reflash when the
   hotspot moves.

   The price is that it needs the hotspot credentials, which it reads from
   csi_recv_mqtt's .env at configure time -- see main/CMakeLists.txt, so the
   SSID, password and sender MAC live in exactly one place. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "nvs_flash.h"

#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_now.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

/* Generated from the shared .env at configure time -- never edited by hand. */
#include "wifi_config.h"

/* The rate is still pinned: MCS0-LGI is an HT rate, and the phymode it is
   paired with is derived at runtime from what the AP negotiated. */
#define CONFIG_ESP_NOW_RATE             WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_SEND_FREQUENCY               100

static const char *TAG = "csi_send_mqtt";

#define WIFI_GOT_IP_BIT BIT0

static EventGroupHandle_t s_wifi_eg;

/* Once association is possible it can also be lost, and a dropped link would
   otherwise log a send error at CONFIG_SEND_FREQUENCY and bury the monitor.
   One line per failure run instead. */
static bool s_send_error_logged;

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();

    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = event_data;
        ESP_LOGW(TAG, "Wi-Fi disconnected (reason %d), reconnecting", d->reason);
        xEventGroupClearBits(s_wifi_eg, WIFI_GOT_IP_BIT);
        /* No delay here: this runs on the event loop task and esp_wifi_connect
           is non-blocking, so the driver's own backoff paces the retries. */
        esp_wifi_connect();

    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = event_data;
        char ip[16];
        esp_ip4addr_ntoa(&e->ip_info.ip, ip, sizeof(ip));
        ESP_LOGI(TAG, "Wi-Fi up: ip=%s", ip);
        xEventGroupSetBits(s_wifi_eg, WIFI_GOT_IP_BIT);
    }
}

static void wifi_init_sta(void)
{
    s_wifi_eg = xEventGroupCreate();
    if (s_wifi_eg == NULL) {
        ESP_LOGE(TAG, "no memory for the Wi-Fi event group");
        abort();
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL, NULL));

    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, CFG_WIFI_SSID, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, CFG_WIFI_PASSWORD, sizeof(wc.sta.password));
    /* Accept whatever the hotspot offers (WPA2, WPA3, even open) rather than
       pinning a minimum auth mode and failing association on a mismatch. */
    wc.sta.threshold.authmode = WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;
    wc.sta.pmf_cfg.required = false;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* Power save would park the radio between beacons and cost us frames --
       the same reason the receiver sets it. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    /* NOTE: neither the channel nor the width is set here, unlike csi_send.
       Those two calls are what have to be kept in sync with the hotspot by
       hand there; here the AP dictates both, and forcing either would only
       fight the association this design depends on. */
    ESP_LOGI(TAG, "connecting to \"%s\"...", CFG_WIFI_SSID);
}

/* Block until the association completes and DHCP hands us an address. */
static esp_err_t wifi_wait_for_ip(TickType_t timeout)
{
    EventBits_t bits = xEventGroupWaitBits(s_wifi_eg, WIFI_GOT_IP_BIT,
                                           pdFALSE, pdFALSE, timeout);
    return (bits & WIFI_GOT_IP_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}

static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    /* Ask for a rate the radio can actually carry: an HT40 rate on a 20 MHz
       interface is refused outright (ESP_ERR_ESPNOW_ARG, "invalid chanel info,
       need change second channel to 40"), and WIFI_SECOND_CHAN_NONE *is* the
       HT20 case. Deriving it here is the step csi_send cannot take, since it
       has to name the width at compile time. */
    uint8_t primary = 0;
    wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
    bool ht40 = (esp_wifi_get_channel(&primary, &second) == ESP_OK &&
                 second != WIFI_SECOND_CHAN_NONE);

    esp_now_rate_config_t rate_config = {
        .phymode = ht40 ? WIFI_PHY_MODE_HT40 : WIFI_PHY_MODE_HT20,
        .rate    = CONFIG_ESP_NOW_RATE,
        .ersu    = false,
        .dcm     = false,
    };

    /* Non-fatal: the receiver's CSI callback reads the L-LTF, which every rate
       carries, so a rejected rate costs nothing but the explicit MCS choice.
       Aborting would turn that into a boot loop that transmits nothing. */
    esp_err_t err = esp_now_set_peer_rate_config(peer.peer_addr, &rate_config);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "ESP-NOW rate config rejected (%s); using its default rate",
                 esp_err_to_name(err));
    }

    ESP_LOGI(TAG, "ESP-NOW ready: %s on channel %u", ht40 ? "HT40" : "HT20", primary);
}

void app_main(void)
{
    /**
     * @brief Initialize NVS
     */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /**
     * @brief Initialize Wi-Fi and wait for the association
     */
    wifi_init_sta();

    /* The peer below follows "the current channel", and before the association
       that is still the driver's default, so early frames would go out where
       nobody is listening. Wait for the link -- but do not refuse to start
       without one: the peer tracks the channel whenever the association does
       complete, so a hotspot that appears late needs no reboot. */
    if (wifi_wait_for_ip(pdMS_TO_TICKS(30000)) != ESP_OK) {
        ESP_LOGW(TAG, "no address after 30s; starting anyway -- frames are lost "
                      "until \"%s\" is reachable", CFG_WIFI_SSID);
    }

    /**
     * @brief Initialize ESP-NOW
     *        ESP-NOW protocol see: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html
     */
    esp_now_peer_info_t peer = {
        /* Channel 0 means "whatever channel the interface is on", which is the
           hotspot's channel once associated. The receiver is on the same
           hotspot, so neither end needs to be told a channel number. */
        .channel   = 0,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    wifi_esp_now_init(peer);

    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    ESP_LOGI(TAG, "================ CSI SEND ================");
    ESP_LOGI(TAG, "send_frequency: %d, mac: " MACSTR,
             CONFIG_SEND_FREQUENCY, MAC2STR(mac));
    ESP_LOGI(TAG, "This MAC must match CSI_SENDER_MAC in csi_recv_mqtt/.env, "
                  "or the receiver drops every frame as \"some other transmitter\".");

    for (uint32_t count = 0; ; ++count) {
        esp_err_t err = esp_now_send(peer.peer_addr, (const uint8_t *)&count, sizeof(count));
        if (err != ESP_OK) {
            if (!s_send_error_logged) {
                s_send_error_logged = true;
                ESP_LOGW(TAG, "ESP-NOW send failed <%s>; suppressing repeats until it recovers",
                         esp_err_to_name(err));
            }
        } else if (s_send_error_logged) {
            s_send_error_logged = false;
            ESP_LOGI(TAG, "ESP-NOW sending again");
        }

        usleep(1000 * 1000 / CONFIG_SEND_FREQUENCY);
    }
}
