/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* CSI receiver that publishes to an MQTT broker

   Derived from the esp-csi "csi_recv" example, which streamed CSI packets to
   the UART as CSV lines. Here the very same CSV line is published to an MQTT
   broker instead, so the receiver only needs a Wi-Fi association with the
   hotspot and no USB cable.

   The broker is addressed by hostname because a hotspot hands out a new IP
   on every reconnect. Names ending in ".local" are resolved over mDNS,
   anything else through the normal DNS resolver, and the name is re-resolved
   on each reconnect so a changed IP is picked up automatically.

   All credentials live in the project's .env file, which tools/gen_config.py
   turns into main/mqtt_config.h at build time.
*/

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <inttypes.h>
#include <time.h>
#include <sys/time.h>

#include "nvs_flash.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_sntp.h"
#include "esp_csi_gain_ctrl.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "mdns.h"
#include "mqtt_client.h"

#include "lwip/netdb.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

#include "mqtt_config.h"    /* generated from .env -- see tools/gen_config.py */

/* ------------------------------------------------------------------------- */
/* Compile-time tuning, carried over from the csi_recv example                */
/* ------------------------------------------------------------------------- */

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE               WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_5G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_2G_PROTOCOL             WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL             WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH               WIFI_BW_HT40
#endif

/* The ESP-NOW phymode is deliberately not pinned here. The csi_recv example
   hardcodes HT40 because it also forces the channel and so can force the
   width; this firmware follows the hotspot, which means the width is whatever
   the AP negotiated. wifi_esp_now_init() derives the phymode from that
   instead. MCS0-LGI is an HT rate, so it is valid at either width. */
#define CONFIG_ESP_NOW_RATE                 WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_FORCE_GAIN                   0

/* The C5/C6 branch above sets per-band bandwidths instead of the single
   CONFIG_WIFI_BANDWIDTH, but this firmware only ever uses 2.4 GHz, so a
   HT40 default keeps esp_wifi_set_bandwidth() compiling on every target. */
#ifndef CONFIG_WIFI_BANDWIDTH
#define CONFIG_WIFI_BANDWIDTH               WIFI_BW_HT40
#endif

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF                      0
#endif

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL                 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

/* CSI packets are loss-tolerant and arrive ~100x/s, so QoS 0 is deliberate:
   QoS 1 retransmissions would queue up behind a slow link and add latency to
   data that is already stale by the time it arrives. */
#define CSI_PUBLISH_QOS                     0
#define STATUS_PUBLISH_QOS                  1

/* The status topic carries low-rate JSON, so it always fits comfortably. */
#define STATUS_JSON_MAX                     640
#define BROKER_URI_MAX                      96
#define IP_STR_MAX                          16      /* "255.255.255.255" + NUL */
#define PUBLISHER_POLL_MS                   200     /* queue drain / housekeeping tick */

static const char *TAG = "csi_recv_mqtt";

/* ------------------------------------------------------------------------- */
/* Shared state                                                              */
/* ------------------------------------------------------------------------- */

typedef struct {
    char *buf;      /* heap-allocated CSV line */
    int   len;
} csi_msg_t;

static QueueHandle_t            s_csi_q;
static EventGroupHandle_t       s_wifi_eg;
static esp_mqtt_client_handle_t s_mqtt;
static char                     s_client_id[32];
static char                     s_lwt_msg[64];
static uint8_t                  s_sender_mac[6];

static char  s_broker_ip[IP_STR_MAX];   /* last successful resolution */
static char  s_broker_uri[BROKER_URI_MAX];
static char  s_sta_ip[IP_STR_MAX];
static volatile bool s_broker_conn;
static volatile bool s_mdns_started;
static volatile bool s_uri_dirty;       /* set on disconnect, handled by the publisher task */
static volatile int  s_channel;         /* channel the STA actually landed on */
static volatile int  s_last_rssi;
static volatile bool s_ntp_synced;      /* wall clock set by NTP yet? */
static bool          s_sntp_started;    /* esp_sntp_init() called exactly once */

static uint32_t s_seen;         /* frames accepted from the sender MAC */
static uint32_t s_published;    /* successfully handed to esp-mqtt */
static uint32_t s_pub_failed;   /* publish rejected (outbox full / not connected) */
static uint32_t s_dropped;      /* malloc failed or queue would not accept */
static uint32_t s_queue_overrun;/* oldest packet evicted to make room */
static uint32_t s_queue_peak;

#define WIFI_GOT_IP_BIT     BIT0

/* ------------------------------------------------------------------------- */
/* Small helpers                                                             */
/* ------------------------------------------------------------------------- */

/* Apply the LOG_LEVEL from .env. Called early in app_main, so the handful of
   boot messages before this point still appear at the default level. */
static void apply_log_level(void)
{
    struct { const char *name; esp_log_level_t level; } map[] = {
        { "NONE",    ESP_LOG_NONE    },
        { "ERROR",   ESP_LOG_ERROR   },
        { "WARN",    ESP_LOG_WARN    },
        { "INFO",    ESP_LOG_INFO    },
        { "DEBUG",   ESP_LOG_DEBUG   },
        { "VERBOSE", ESP_LOG_VERBOSE },
    };
    for (size_t i = 0; i < sizeof(map) / sizeof(map[0]); i++) {
        if (!strcmp(CFG_LOG_LEVEL, map[i].name)) {
            esp_log_level_set("*", map[i].level);
            return;
        }
    }
    ESP_LOGW(TAG, "unknown LOG_LEVEL \"%s\", staying at INFO", CFG_LOG_LEVEL);
}

static void parse_sender_mac(void)
{
    unsigned b[6];
    if (sscanf(CFG_CSI_SENDER_MAC, "%x:%x:%x:%x:%x:%x",
               &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6) {
        /* gen_config.py already validated the format, so this is unreachable
           unless mqtt_config.h was edited by hand. */
        ESP_LOGE(TAG, "bad CSI_SENDER_MAC \"%s\"", CFG_CSI_SENDER_MAC);
        abort();
    }
    for (int i = 0; i < 6; i++) {
        s_sender_mac[i] = (uint8_t)b[i];
    }
}

static void init_client_id(void)
{
    if (CFG_MQTT_CLIENT_ID[0] != '\0') {
        strlcpy(s_client_id, CFG_MQTT_CLIENT_ID, sizeof(s_client_id));
        return;
    }
    uint8_t mac[6] = { 0 };
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(s_client_id, sizeof(s_client_id), "csi-recv-%02x%02x%02x",
             mac[3], mac[4], mac[5]);
}

static bool is_ipv4_literal(const char *s)
{
    struct in_addr dummy;
    return inet_aton(s, &dummy) != 0;
}

static bool host_is_mdns_name(const char *host)
{
    size_t n = strlen(host);
    return n > 6 && !strcasecmp(host + n - 6, ".local");
}

/* Append to a buffer being built with snprintf. Returns the new offset, or -1
   once the buffer would overflow, so every later call becomes a no-op. */
static int csv_append(char *buf, size_t cap, int off, const char *fmt, ...)
{
    if (off < 0) {
        return -1;
    }
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf + off, cap - (size_t)off, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= cap - (size_t)off) {
        return -1;
    }
    return off + n;
}

/* ------------------------------------------------------------------------- */
/* NTP: wall clock behind the CSV timestamp_real column                      */
/* ------------------------------------------------------------------------- */

/* Runs on the SNTP task, so it only records the outcome and logs it; the flag
   is what the status JSON and any consumer actually consult. */
static void sntp_sync_cb(struct timeval *tv)
{
    s_ntp_synced = (sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED);
    if (s_ntp_synced) {
        ESP_LOGI(TAG, "NTP synced: %lld.%06lld", (long long)tv->tv_sec,
                 (long long)tv->tv_usec);
    }
}

/* One-shot: SNTP keeps polling on its own once started, including across
   reconnects, so a second IP event must not start it again. */
static void sntp_start_if_needed(void)
{
    if (s_sntp_started) {
        return;
    }
    s_sntp_started = true;
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, CFG_NTP_SERVER);
    sntp_set_time_sync_notification_cb(sntp_sync_cb);
    esp_sntp_init();
    ESP_LOGI(TAG, "NTP: syncing with %s", CFG_NTP_SERVER);
}

/* ------------------------------------------------------------------------- */
/* Wi-Fi: join the hotspot                                                   */
/* ------------------------------------------------------------------------- */

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
        esp_ip4addr_ntoa(&e->ip_info.ip, s_sta_ip, sizeof(s_sta_ip));

        uint8_t primary = 0;
        wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
        if (esp_wifi_get_channel(&primary, &second) == ESP_OK) {
            s_channel = primary;
        }

        ESP_LOGI(TAG, "Wi-Fi up: ip=%s channel=%d", s_sta_ip, s_channel);

        /* The clock starts here: SNTP needs an interface with an address. */
        sntp_start_if_needed();

        /* A fixed-channel sender (csi_send) can sit on a channel the AP did not
           give us; a station cannot pick its own channel, so a mismatch means
           the receiver is deaf and the only symptom is silence. Say so loudly.
           CSI_CHANNEL=0 switches this off for csi_send_mqtt, which follows the
           AP exactly as we do -- leaving nothing to compare against. */
        if (CFG_CSI_CHANNEL != 0 && s_channel != CFG_CSI_CHANNEL) {
            ESP_LOGW(TAG, "----------------------------------------------------");
            ESP_LOGW(TAG, "Channel mismatch: hotspot put us on %d, but the CSI", s_channel);
            ESP_LOGW(TAG, "sender transmits on %d. No CSI will be captured.", CFG_CSI_CHANNEL);
            ESP_LOGW(TAG, "Fix: put the hotspot on channel %d, or change the", CFG_CSI_CHANNEL);
            ESP_LOGW(TAG, "sender's channel to %d and set CSI_CHANNEL=%d in .env.",
                     s_channel, s_channel);
            ESP_LOGW(TAG, "----------------------------------------------------");
        }

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

#if CONFIG_IDF_TARGET_ESP32C5 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE));
#endif
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, CONFIG_WIFI_BANDWIDTH));

    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* Power save would park the radio between beacons and butcher CSI. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    /* NOTE: unlike the serial csi_recv example, the channel is deliberately
       NOT set here. A station follows its AP's channel, and forcing one would
       only desynchronise us from the AP we are associated with. */
    ESP_LOGI(TAG, "connecting to \"%s\"...", CFG_WIFI_SSID);
}

/* Block until the association completes and DHCP hands us an address. */
static esp_err_t wifi_wait_for_ip(TickType_t timeout)
{
    EventBits_t bits = xEventGroupWaitBits(s_wifi_eg, WIFI_GOT_IP_BIT,
                                           pdFALSE, pdFALSE, timeout);
    return (bits & WIFI_GOT_IP_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}

/* ------------------------------------------------------------------------- */
/* Broker hostname resolution                                                */
/* ------------------------------------------------------------------------- */

/* The mDNS component does not hook into lwIP's resolver, so a ".local" name
   handed straight to esp-mqtt would never resolve. Names are therefore turned
   into an address here and esp-mqtt is given a literal IP. */
static esp_err_t resolve_host(const char *host, char *out, size_t out_len)
{
    if (is_ipv4_literal(host)) {
        strlcpy(out, host, out_len);     /* already an address, nothing to do */
        return ESP_OK;
    }

    if (host_is_mdns_name(host)) {
        if (!s_mdns_started) {
            ESP_LOGE(TAG, "\"%s\" needs mDNS, which failed to start", host);
        } else {
            /* mdns_query_a() takes the bare hostname; the ".local" suffix is
               the domain mDNS appends itself. */
            char name[64];
            size_t n = strlen(host) - 6;
            if (n >= sizeof(name)) {
                n = sizeof(name) - 1;
            }
            memcpy(name, host, n);
            name[n] = '\0';

            esp_ip4_addr_t addr = { 0 };
            esp_err_t err = mdns_query_a(name, CFG_MDNS_TIMEOUT_MS, &addr);
            if (err == ESP_OK) {
                esp_ip4addr_ntoa(&addr, out, (int)out_len);
                return ESP_OK;
            }
            ESP_LOGW(TAG, "mDNS lookup of \"%s\" failed (%s), trying DNS",
                     host, esp_err_to_name(err));
        }
    }

    struct addrinfo hints = {
        .ai_family   = AF_INET,
        .ai_socktype = SOCK_STREAM,
    };
    struct addrinfo *res = NULL;
    int rc = getaddrinfo(host, NULL, &hints, &res);
    if (rc == 0 && res != NULL && res->ai_addr != NULL) {
        struct sockaddr_in *sa = (struct sockaddr_in *)res->ai_addr;
        inet_ntoa_r(sa->sin_addr, out, out_len);
        freeaddrinfo(res);
        return ESP_OK;
    }
    if (res != NULL) {
        freeaddrinfo(res);
    }
    ESP_LOGW(TAG, "could not resolve \"%s\" (getaddrinfo rc=%d)", host, rc);
    return ESP_FAIL;
}

static void mdns_start_if_needed(void)
{
    if (!host_is_mdns_name(CFG_MQTT_BROKER_HOST) || s_mdns_started) {
        return;
    }
    esp_err_t err = mdns_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mdns_init failed: %s", esp_err_to_name(err));
        return;
    }
    mdns_hostname_set(CFG_MDNS_HOSTNAME);
    mdns_instance_name_set("CSI Receiver");
    s_mdns_started = true;
    ESP_LOGI(TAG, "mDNS up, this device is \"%s.local\"", CFG_MDNS_HOSTNAME);
}

/* Re-resolve the broker and point esp-mqtt at the result. Safe to call at any
   time; the client only picks the new URI up on its next connect attempt. */
static void refresh_broker_uri(void)
{
    /* mDNS can only be started once the interface exists, and this is the one
       place that needs it, so bring it up lazily here. Idempotent. */
    mdns_start_if_needed();

    char ip[IP_STR_MAX];
    if (resolve_host(CFG_MQTT_BROKER_HOST, ip, sizeof(ip)) != ESP_OK) {
        return;     /* keep the previous URI and let esp-mqtt keep retrying */
    }

    if (strcmp(ip, s_broker_ip) != 0) {
        if (s_broker_ip[0] != '\0') {
            ESP_LOGI(TAG, "broker \"%s\" moved: %s -> %s",
                     CFG_MQTT_BROKER_HOST, s_broker_ip, ip);
        } else {
            ESP_LOGI(TAG, "broker \"%s\" resolved to %s",
                     CFG_MQTT_BROKER_HOST, ip);
        }
        strlcpy(s_broker_ip, ip, sizeof(s_broker_ip));
        snprintf(s_broker_uri, sizeof(s_broker_uri), "mqtt://%s:%d",
                 s_broker_ip, CFG_MQTT_BROKER_PORT);
        if (s_mqtt != NULL) {
            esp_mqtt_client_set_uri(s_mqtt, s_broker_uri);
        }
    }
}

/* ------------------------------------------------------------------------- */
/* MQTT: status + data publishing                                            */
/* ------------------------------------------------------------------------- */

/* Format matches the UART output of the serial example, plus the trailing
   timestamp_real column this firmware stamps with its NTP-synced wall clock
   (UNIX epoch seconds with microseconds, e.g. 1785103746.011223). Kept next
   to the payload builder below so the two cannot drift. */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_CSV_HEADER \
    "type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel," \
    "local_timestamp,sig_len,rx_state,len,first_word,data,timestamp_real"
#else
#define CSI_CSV_HEADER \
    "type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding," \
    "aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel," \
    "secondary_channel,local_timestamp,ant,sig_len,rx_state,len," \
    "first_word,data,timestamp_real"
#endif

static void publish_status(const char *json, int len, int retain)
{
    if (s_mqtt == NULL) {
        return;
    }
    int id = esp_mqtt_client_publish(s_mqtt, CFG_MQTT_TOPIC_STATUS, json, len,
                                     STATUS_PUBLISH_QOS, retain);
    if (id < 0) {
        ESP_LOGW(TAG, "status publish failed");
    }
}

/* Retained, so a subscriber connecting later immediately learns this device
   is alive rather than waiting for the next heartbeat. */
static void publish_online(void)
{
    char json[STATUS_JSON_MAX];
    int n = snprintf(json, sizeof(json),
                     "{\"state\":\"online\",\"client\":\"%s\",\"ip\":\"%s\","
                     "\"broker\":\"%s\",\"channel\":%d,\"rssi\":%d,"
                     "\"ntp_synced\":%s,"
                     "\"uptime_s\":%" PRIu64 ",\"csv_header\":\"%s\"}",
                     s_client_id, s_sta_ip, s_broker_uri, s_channel, s_last_rssi,
                     s_ntp_synced ? "true" : "false",
                     (uint64_t)(esp_timer_get_time() / 1000000), CSI_CSV_HEADER);
    if (n > 0 && n < (int)sizeof(json)) {
        publish_status(json, n, 1);
    }
}

static void publish_heartbeat(void)
{
    char json[STATUS_JSON_MAX];
    int n = snprintf(json, sizeof(json),
                     "{\"state\":\"online\",\"client\":\"%s\","
                     "\"ntp_synced\":%s,"
                     "\"uptime_s\":%" PRIu64 ",\"seen\":%" PRIu32 ","
                     "\"published\":%" PRIu32 ",\"publish_failed\":%" PRIu32 ","
                     "\"dropped\":%" PRIu32 ",\"queue_overrun\":%" PRIu32 ","
                     "\"queue_peak\":%" PRIu32 ",\"free_heap\":%" PRIu32 ","
                     "\"rssi\":%d}",
                     s_client_id,
                     s_ntp_synced ? "true" : "false",
                     (uint64_t)(esp_timer_get_time() / 1000000),
                     s_seen, s_published, s_pub_failed, s_dropped,
                     s_queue_overrun, s_queue_peak,
                     (uint32_t)esp_get_free_heap_size(), s_last_rssi);
    if (n > 0 && n < (int)sizeof(json)) {
        publish_status(json, n, 1);
    }
}

static void mqtt_event_handler(void *args, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t e = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        s_broker_conn = true;
        ESP_LOGI(TAG, "connected to broker at %s", s_broker_uri);
        /* Safe from here: esp-mqtt's api lock is a recursive mutex. */
        publish_online();
        break;

    case MQTT_EVENT_DISCONNECTED:
        s_broker_conn = false;
        ESP_LOGW(TAG, "broker connection lost");
        /* Re-resolution blocks on mDNS, so it is handed to the publisher task
           instead of stalling the MQTT event loop. */
        s_uri_dirty = true;
        break;

    case MQTT_EVENT_ERROR:
        if (e != NULL && e->error_handle != NULL) {
            const esp_mqtt_error_codes_t *h = e->error_handle;
            if (h->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                ESP_LOGE(TAG, "transport error: esp-tls 0x%x, sock errno %d, %s",
                         h->esp_tls_last_esp_err, h->esp_transport_sock_errno,
                         strerror(h->esp_transport_sock_errno));
            } else if (h->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
                ESP_LOGE(TAG, "broker refused connection (return code 0x%x) -- "
                              "check MQTT_USERNAME/MQTT_PASSWORD in .env",
                         h->connect_return_code);
            } else {
                ESP_LOGE(TAG, "mqtt error, type %d", h->error_type);
            }
        }
        break;

    default:
        break;
    }
}

static void mqtt_start(void)
{
    /* The last will is what tells a watcher that this device died rather than
       merely went quiet, so it is published retained by the broker. */
    snprintf(s_lwt_msg, sizeof(s_lwt_msg),
             "{\"state\":\"offline\",\"client\":\"%s\"}", s_client_id);

    esp_mqtt_client_config_t cfg = {
        .broker.address.uri                  = s_broker_uri,
        .credentials.username                = CFG_MQTT_USERNAME,
        .credentials.client_id               = s_client_id,
        .credentials.authentication.password = CFG_MQTT_PASSWORD,
        .session.keepalive                   = CFG_MQTT_KEEPALIVE_S,
        .session.last_will = {
            .topic  = CFG_MQTT_TOPIC_STATUS,
            .msg    = s_lwt_msg,
            .msg_len = (int)strlen(s_lwt_msg),
            .qos    = STATUS_PUBLISH_QOS,
            .retain = 1,
        },
        .network.reconnect_timeout_ms        = 5000,
        .network.timeout_ms                  = 10000,
        .buffer.out_size                     = 4096,
    };

    s_mqtt = esp_mqtt_client_init(&cfg);
    ESP_ERROR_CHECK(s_mqtt ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(
        s_mqtt, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL));
    ESP_ERROR_CHECK(esp_mqtt_client_start(s_mqtt));
    ESP_LOGI(TAG, "connecting to MQTT broker %s", s_broker_uri);
}

/* ------------------------------------------------------------------------- */
/* Publisher task: drains formatted packets and republishes status           */
/* ------------------------------------------------------------------------- */

static void publisher_task(void *arg)
{
    int64_t last_status_us = esp_timer_get_time();
    int64_t last_broker_try_us = 0;
    csi_msg_t msg;

    for (;;) {
        /* The timeout doubles as the housekeeping tick, so one task handles
           both the queue and the heartbeat. */
        if (xQueueReceive(s_csi_q, &msg, pdMS_TO_TICKS(PUBLISHER_POLL_MS)) == pdTRUE) {
            if (s_broker_conn) {
                int id = esp_mqtt_client_publish(s_mqtt, CFG_MQTT_TOPIC_DATA,
                                                 msg.buf, msg.len,
                                                 CSI_PUBLISH_QOS, 0);
                if (id < 0) {
                    s_pub_failed++;
                } else {
                    s_published++;
                }
            } else {
                s_pub_failed++;     /* deliberately not buffered: stale CSI is worthless */
            }
            free(msg.buf);
        }

        /* A receiver deployed before its hotspot exists should still come up
           on its own, so keep retrying the lookup until there is a broker to
           talk to. */
        if (s_mqtt == NULL) {
            int64_t now = esp_timer_get_time();
            if (now - last_broker_try_us >= 5 * 1000000) {
                last_broker_try_us = now;
                refresh_broker_uri();
                if (s_broker_uri[0] != '\0') {
                    mqtt_start();
                }
            }
        } else if (s_uri_dirty) {
            s_uri_dirty = false;
            refresh_broker_uri();
        }

        UBaseType_t depth = uxQueueMessagesWaiting(s_csi_q);
        if (depth > s_queue_peak) {
            s_queue_peak = depth;
        }

        if (CFG_STATUS_INTERVAL_S > 0 && s_broker_conn) {
            int64_t now = esp_timer_get_time();
            if (now - last_status_us >= (int64_t)CFG_STATUS_INTERVAL_S * 1000000) {
                last_status_us = now;
                publish_heartbeat();
            }
        }
    }
}

/* ------------------------------------------------------------------------- */
/* CSI capture                                                               */
/* ------------------------------------------------------------------------- */

static void csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

    if (memcmp(info->mac, s_sender_mac, 6)) {
        return;     /* some other transmitter */
    }

    /* Wall clock at reception, feeding the trailing timestamp_real column.
       Before the first NTP sync this reads ~0.000000; consumers can tell real
       time apart via the ntp_synced flag in the status topic. */
    struct timeval tv;
    gettimeofday(&tv, NULL);

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;
    static int s_count = 0;
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;
#if CONFIG_GAIN_CONTROL
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (s_count < 100) {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    } else if (s_count == 100) {
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
#endif
    }
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
    /* Debug, not info: at ~100 packets/s this would swamp the UART. */
    ESP_LOGD(TAG, "compensate_gain %f, agc_gain %d, fft_gain %d",
             compensate_gain, agc_gain, fft_gain);
#else
    (void)agc_gain;
    (void)fft_gain;
#endif

    s_seen++;
    s_last_rssi = rx_ctrl->rssi;

    /* Worst case per sample is "-32768," -- a sign, five digits and a comma.
       The fixed part covers the ~24 metadata columns, one of which (the
       timestamp) is a full uint32, plus the trailing timestamp_real wall
       clock (~22 chars). Overflow is detected rather than silently
       truncating, so a too-small buffer would drop packets, not corrupt
       them. */
    const size_t cap = 320 + (size_t)info->len * 9;
    char *buf = malloc(cap);
    if (buf == NULL) {
        s_dropped++;
        return;
    }

    uint32_t rx_id = info->payload ? *(uint32_t *)(info->payload + 15) : 0;
    int off = 0;

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
    off = csv_append(buf, cap, off, "CSI_DATA,%" PRIu32 "," MACSTR
                     ",%d,%d,%d,%d,%d,%d,%d,%d,%d",
                     rx_id, MAC2STR(info->mac), rx_ctrl->rssi, rx_ctrl->rate,
                     rx_ctrl->noise_floor, fft_gain, agc_gain, rx_ctrl->channel,
                     rx_ctrl->timestamp, rx_ctrl->sig_len, rx_ctrl->rx_state);
#else
    off = csv_append(buf, cap, off, "CSI_DATA,%" PRIu32 "," MACSTR
                     ",%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d",
                     rx_id, MAC2STR(info->mac), rx_ctrl->rssi, rx_ctrl->rate,
                     rx_ctrl->sig_mode, rx_ctrl->mcs, rx_ctrl->cwb,
                     rx_ctrl->smoothing, rx_ctrl->not_sounding, rx_ctrl->aggregation,
                     rx_ctrl->stbc, rx_ctrl->fec_coding, rx_ctrl->sgi,
                     rx_ctrl->noise_floor, rx_ctrl->ampdu_cnt, rx_ctrl->channel,
                     rx_ctrl->secondary_channel, rx_ctrl->timestamp, rx_ctrl->ant,
                     rx_ctrl->sig_len, rx_ctrl->rx_state);
#endif

#if (CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61) && CSI_FORCE_LLTF
    off = csv_append(buf, cap, off, ",%d,%d,\"[%d",
                     (info->len - 2) / 2, info->first_word_invalid,
                     (int16_t)(compensate_gain * (int16_t)(((((uint16_t)info->buf[1]) << 8) |
                                                            info->buf[0]) << 4) >> 4));
    for (int i = 2; i < (info->len - 2) && off >= 0; i += 2) {
        int16_t csi = (int16_t)(((((uint16_t)info->buf[i + 1]) << 8) |
                                 info->buf[i]) << 4) >> 4;
        off = csv_append(buf, cap, off, ",%d", (int16_t)(compensate_gain * csi));
    }
#else
    off = csv_append(buf, cap, off, ",%d,%d,\"[%d",
                     info->len, info->first_word_invalid,
                     (int16_t)(compensate_gain * info->buf[0]));
    for (int i = 1; i < info->len && off >= 0; i++) {
        off = csv_append(buf, cap, off, ",%d",
                         (int16_t)(compensate_gain * info->buf[i]));
    }
#endif

    if (off < 0) {
        s_dropped++;
        free(buf);
        return;
    }
    off = csv_append(buf, cap, off, "]\"");
    if (off < 0) {
        s_dropped++;
        free(buf);
        return;
    }

    /* The trailing wall-clock column, captured above at reception time. */
    off = csv_append(buf, cap, off, ",%lld.%06lld",
                     (long long)tv.tv_sec, (long long)tv.tv_usec);
    if (off < 0) {
        s_dropped++;
        free(buf);
        return;
    }

    /* Hand off to the publisher task. This callback runs on the Wi-Fi task, so
       it must never block: a full queue evicts the oldest packet, which keeps
       the freshest CSI flowing and keeps capture independent of the broker. */
    csi_msg_t msg = { .buf = buf, .len = off };
    if (xQueueSend(s_csi_q, &msg, 0) != pdTRUE) {
        csi_msg_t evicted;
        if (xQueueReceive(s_csi_q, &evicted, 0) == pdTRUE) {
            free(evicted.buf);
            s_queue_overrun++;
        }
        if (xQueueSend(s_csi_q, &msg, 0) != pdTRUE) {
            free(buf);
            s_dropped++;
        }
    }

    s_count++;
}

static void wifi_csi_init(void)
{
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable                   = true,
        .acquire_csi_legacy       = false,
        .acquire_csi_force_lltf   = CSI_FORCE_LLTF,
        .acquire_csi_ht20         = true,
        .acquire_csi_ht40         = true,
        .acquire_csi_vht          = false,
        .acquire_csi_su           = false,
        .acquire_csi_mu           = false,
        .acquire_csi_dcm          = false,
        .acquire_csi_beamformed   = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg            = 0,
        .dump_ack_en              = false,
        .reserved                 = false
    };
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable                 = true,
        .acquire_csi_legacy     = false,
        .acquire_csi_ht20       = true,
        .acquire_csi_ht40       = true,
        .acquire_csi_su         = true,
        .acquire_csi_mu         = true,
        .acquire_csi_dcm        = true,
        .acquire_csi_beamformed = true,
        .acquire_csi_he_stbc    = 2,
        .val_scale_cfg          = false,
        .dump_ack_en            = false,
        .reserved               = false
    };
#else
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = false,
        .stbc_htltf2_en    = false,
        .ltf_merge_en      = true,
        .channel_filter_en = false,
        .manu_scale        = false,
        .shift             = false,
    };
#endif
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void wifi_esp_now_init(void)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));

    esp_now_peer_info_t peer = {
        /* Channel 0 means "whatever channel the interface is on", which is the
           hotspot's channel once associated -- a fixed channel here would
           fight the AP association. */
        .channel   = 0,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    /* The rate config only describes how *we* transmit to that peer, and the
       radio has to already be that wide: asking for HT40 while the hotspot has
       us on 20 MHz is refused outright (ESP_ERR_ESPNOW_ARG, "invalid chanel
       info, need change second channel to 40"). A station picks neither its
       channel nor its width -- the AP dictates both -- so ask for what was
       actually negotiated. WIFI_SECOND_CHAN_NONE *is* the HT20 case. */
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

    /* A rejected rate costs us nothing but the explicit MCS choice: ESP-NOW
       keeps its own default and CSI still arrives. Never abort the boot over
       an optimisation -- that is exactly what turned this into a reboot loop. */
    esp_err_t err = esp_now_set_peer_rate_config(peer.peer_addr, &rate_config);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "ESP-NOW rate config rejected (%s); using its default rate",
                 esp_err_to_name(err));
    }
    ESP_LOGI(TAG, "ESP-NOW peer ready, %s on channel %u", ht40 ? "HT40" : "HT20", primary);
}

/* ------------------------------------------------------------------------- */
/* Entry point                                                               */
/* ------------------------------------------------------------------------- */

void app_main(void)
{
    apply_log_level();

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    parse_sender_mac();
    init_client_id();

    s_csi_q = xQueueCreate(CFG_CSI_QUEUE_DEPTH, sizeof(csi_msg_t));
    ESP_ERROR_CHECK(s_csi_q ? ESP_OK : ESP_ERR_NO_MEM);

    wifi_init_sta();

    /* Anything that talks to the network waits for the association first. */
    if (wifi_wait_for_ip(pdMS_TO_TICKS(60000)) != ESP_OK) {
        ESP_LOGW(TAG, "no IP after 60s; the broker link will come up on its own "
                      "once the hotspot is reachable");
    }

    /* The publisher task owns broker resolution and MQTT startup. Keeping it in
       one place is what lets it retry: if the hotspot or the broker's name is
       not up yet, it keeps trying instead of giving up at boot. */
    BaseType_t task_ok = xTaskCreate(publisher_task, "mqtt_pub", 4096, NULL, 5, NULL);
    ESP_ERROR_CHECK(task_ok == pdPASS ? ESP_OK : ESP_FAIL);

    wifi_esp_now_init();
    wifi_csi_init();

    ESP_LOGI(TAG, "================ CSI -> MQTT ================");
    ESP_LOGI(TAG, "client:   %s", s_client_id);
    ESP_LOGI(TAG, "sender:   " MACSTR, MAC2STR(s_sender_mac));
    ESP_LOGI(TAG, "data:     %s", CFG_MQTT_TOPIC_DATA);
    ESP_LOGI(TAG, "status:   %s", CFG_MQTT_TOPIC_STATUS);
    ESP_LOGI(TAG, "waiting for CSI frames...");
}
