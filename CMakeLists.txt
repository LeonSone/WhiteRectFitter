cmake_minimum_required(VERSION 3.20)
project(WhiteRectFitter LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── 可执行文件 ────────────────────────────────────────────────────
option(CONSOLE_BUILD "Build as console app for debugging" OFF)
if(CONSOLE_BUILD)
    add_executable(wrf src/main.cpp)
    target_compile_definitions(wrf PRIVATE CONSOLE_BUILD)
else()
    add_executable(wrf WIN32 src/main.cpp)
endif()

# ── 编译选项 ─────────────────────────────────────────────────────
target_compile_definitions(wrf PRIVATE
    _UNICODE
    UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    $<$<CONFIG:Release>:NDEBUG>
)

target_compile_options(wrf PRIVATE
    $<$<CXX_COMPILER_ID:MSVC>:
        /W4 /WX- /MP
        $<$<CONFIG:Release>:/O2 /GL>
    >
    $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:
        -Wall -Wextra
        $<$<CONFIG:Release>:-O3>
    >
)

# ── 链接库 ───────────────────────────────────────────────────────
# winmm：MCI 音频
target_link_libraries(wrf PRIVATE winmm)

# ── 可选：OpenCV（启用 --preprocess 模式）────────────────────────
#
# 方式 A：vcpkg（推荐）
#   cmake -B build -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake
#   将自动找到 OpenCV，无需手动设置。
#
# 方式 B：手动指定
#   rmdir /s /q build清除旧构建
#   cmake -B build -DOpenCV_DIR="D:/OpenCV/Build"
#   cmake --build build --config Release
#   build/Release/wrf.exe
#
# 不需要 --preprocess 时（只用 Python 预处理），注释掉下面的 find_package。

option(WITH_OPENCV "Enable --preprocess mode (requires OpenCV 4.x)" ON)

if(WITH_OPENCV)
    find_package(OpenCV QUIET)
    if(OpenCV_FOUND)
        target_compile_definitions(wrf PRIVATE WITH_OPENCV)
        target_include_directories(wrf PRIVATE ${OpenCV_INCLUDE_DIRS})
        target_link_libraries(wrf PRIVATE ${OpenCV_LIBS})
        message(STATUS "OpenCV ${OpenCV_VERSION} found — --preprocess 模式已启用")
    else()
        message(WARNING "OpenCV 未找到 — 仅 --play 模式可用（--preprocess 请用 Python）")
    endif()
endif()

# ── Release 优化（仿 Cargo.toml [profile.release]）───────────────
if(MSVC)
    set_target_properties(wrf PROPERTIES
        LINK_FLAGS_RELEASE "/LTCG /OPT:REF /OPT:ICF"
    )
endif()

# ── 安装 ─────────────────────────────────────────────────────────
install(TARGETS wrf RUNTIME DESTINATION bin)
