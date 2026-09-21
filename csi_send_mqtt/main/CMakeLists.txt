# ---------------------------------------------------------------------------
# Generate main/wifi_config.h from csi_recv_mqtt's .env at CMake configure time.
#
# The hotspot credentials are shared with the receiver rather than duplicated:
# one network, one place to edit the password, and one place to fix
# CSI_SENDER_MAC when the sender reports its own MAC at boot.
#
# Every key in .env appears in the generated header, but unused ones are just
# macros nothing references -- they cost nothing in the binary, so the broker
# password is not compiled into this sender.
# ---------------------------------------------------------------------------

set(ENV_FILE   "${CMAKE_CURRENT_LIST_DIR}/../../csi_recv_mqtt/.env")
set(GEN_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/../../csi_recv_mqtt/tools/gen_config.py")
set(GEN_HEADER "${CMAKE_CURRENT_LIST_DIR}/wifi_config.h")

# Editing .env (or the generator) re-runs CMake, which regenerates the header.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${ENV_FILE}" "${GEN_SCRIPT}")

if(NOT EXISTS "${ENV_FILE}")
    message(FATAL_ERROR
        "\n  ${ENV_FILE} not found.\n"
        "  This project shares csi_recv_mqtt's credentials. Create them first:\n\n"
        "      cd ../csi_recv_mqtt && cp .env.example .env\n")
endif()

# ESP-IDF has already located a Python 3 interpreter; fall back just in case.
if(DEFINED Python3_EXECUTABLE)
    set(_gen_python "${Python3_EXECUTABLE}")
elseif(DEFINED PYTHON)
    set(_gen_python "${PYTHON}")
else()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(_gen_python "${Python3_EXECUTABLE}")
endif()

execute_process(
    COMMAND "${_gen_python}" "${GEN_SCRIPT}" "${ENV_FILE}" "${GEN_HEADER}"
    RESULT_VARIABLE _gen_rc
    ERROR_VARIABLE  _gen_err)

if(NOT _gen_rc EQUAL 0)
    message(FATAL_ERROR "\n${_gen_err}")
endif()

idf_component_register(SRCS "app_main.c"
                       INCLUDE_DIRS "."
                       REQUIRES nvs_flash esp_wifi esp_netif esp_event)
