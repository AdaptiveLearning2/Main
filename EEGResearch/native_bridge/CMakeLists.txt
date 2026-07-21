cmake_minimum_required(VERSION 3.21)
project(muse_native_bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(ENABLE_LIBMUSE "Enable libMuse SDK integration" OFF)

add_executable(muse_native_bridge
    src/main.cpp
    src/bridge_tcp_server.cpp
    src/muse_bridge_service.cpp
)

target_include_directories(muse_native_bridge PRIVATE src)

if(MSVC)
    target_compile_options(muse_native_bridge PRIVATE /W4 /EHsc)
endif()

target_link_libraries(muse_native_bridge PRIVATE Ws2_32)

if(ENABLE_LIBMUSE)
    set(LIBMUSE_SDK_DIR "" CACHE PATH "Path to libmuse_windows_8.0.5 directory")
    if(NOT LIBMUSE_SDK_DIR)
        message(FATAL_ERROR "ENABLE_LIBMUSE=ON requires LIBMUSE_SDK_DIR")
    endif()
    target_compile_definitions(muse_native_bridge PRIVATE ENABLE_LIBMUSE=1)
    target_include_directories(muse_native_bridge PRIVATE "${LIBMUSE_SDK_DIR}/examples/include")

    # C++/WinRT (winrt::Windows::Devices::Radios::Radio) is used to detect
    # whether the Windows Bluetooth radio itself is powered on, the same
    # check GettingData32/GettingData perform via Radio::GetRadiosAsync().
    # WindowsApp.lib is the standard link target for consuming WinRT APIs
    # from a plain Win32/console executable (no UWP packaging required).
    target_link_libraries(muse_native_bridge PRIVATE WindowsApp)

    # The prebuilt libmuse-wrt.lib was compiled against C++/WinRT 2.0.240405.15
    # (see examples/GettingData32/packages.config). C++/WinRT embeds
    # #pragma detect_mismatch guards on its version, so compiling against the
    # newer in-box headers that ship with the Windows SDK produces
    # "LNK2038: mismatch detected for 'C++/WinRT version'" at link time.
    # Generate a version-matched projection with the SDK's own vendored
    # cppwinrt.exe and put it ahead of the system include path.
    set(_cppwinrt_exe "${LIBMUSE_SDK_DIR}/examples/packages/Microsoft.Windows.CppWinRT.2.0.240405.15/bin/cppwinrt.exe")
    set(_cppwinrt_out "${CMAKE_BINARY_DIR}/cppwinrt_generated")
    if(NOT EXISTS "${_cppwinrt_exe}")
        message(FATAL_ERROR "Missing vendored cppwinrt.exe: ${_cppwinrt_exe}")
    endif()
    if(NOT EXISTS "${_cppwinrt_out}/winrt/base.h")
        message(STATUS "Generating version-matched C++/WinRT projection (2.0.240405.15)...")
        execute_process(
            COMMAND "${_cppwinrt_exe}" -input local -output "${_cppwinrt_out}"
            RESULT_VARIABLE _cppwinrt_result
            OUTPUT_QUIET
        )
        if(NOT _cppwinrt_result EQUAL 0)
            message(FATAL_ERROR "cppwinrt.exe projection failed (${_cppwinrt_result})")
        endif()
    endif()
    # BEFORE so these win over the newer in-box SDK winrt/ headers.
    target_include_directories(muse_native_bridge BEFORE PRIVATE "${_cppwinrt_out}")
    message(STATUS "C++/WinRT projection: ${_cppwinrt_out}")

    # libMuse ships import libraries under examples/lib/{debug,release}/{Win32,x64}
    # Match the common VS sample layout used by GettingData32.
    if(CMAKE_GENERATOR_PLATFORM STREQUAL "Win32")
        set(_libmuse_arch "Win32")
    else()
        # Default VS generator platform is x64 for this project.
        set(_libmuse_arch "x64")
    endif()

    set(_libmuse_debug_lib "${LIBMUSE_SDK_DIR}/examples/lib/debug/${_libmuse_arch}/libmuse-wrt.lib")
    set(_libmuse_release_lib "${LIBMUSE_SDK_DIR}/examples/lib/release/${_libmuse_arch}/libmuse-wrt.lib")

    if(NOT EXISTS "${_libmuse_debug_lib}")
        message(FATAL_ERROR "Missing libMuse import library (Debug): ${_libmuse_debug_lib}")
    endif()
    if(NOT EXISTS "${_libmuse_release_lib}")
        message(FATAL_ERROR "Missing libMuse import library (Release): ${_libmuse_release_lib}")
    endif()

    target_link_libraries(
        muse_native_bridge PRIVATE
        "$<$<CONFIG:Debug>:${_libmuse_debug_lib}>"
        "$<$<NOT:$<CONFIG:Debug>>:${_libmuse_release_lib}>"
    )

    message(STATUS "libMuse link (Debug): ${_libmuse_debug_lib}")
    message(STATUS "libMuse link (non-Debug): ${_libmuse_release_lib}")
endif()
