# ---------------------------------------------------------------------------
# Turn the project's .env into main/mqtt_config.h at CMake configure time, so
# `idf.py build` always compiles against the current credentials and the
# secrets themselves never enter the source tree.
# ---------------------------------------------------------------------------

set(ENV_FILE   "${CMAKE_CURRENT_LIST_DIR}/../.env")
set(GEN_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/../tools/gen_config.py")
set(GEN_HEADER "${CMAKE_CURRENT_LIST_DIR}/mqtt_config.h")

# Editing .env (or the generator) re-runs CMake, which regenerates the header.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${ENV_FILE}" "${GEN_SCRIPT}")

if(NOT EXISTS "${ENV_FILE}")
    message(FATAL_ERROR
        "\n  ${ENV_FILE} not found.\n"
        "  Create it from the template and fill in your credentials:\n\n"
        "      cp .env.example .env\n")
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

idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS "."
                       REQUIRES nvs_flash esp_wifi esp_netif esp_event
                                esp_timer mqtt mdns lwip esp_csi_gain_ctrl)
