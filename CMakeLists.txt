cmake_minimum_required(VERSION 3.20)
project(WhiteRectFitter LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── 可执行文件 ────────────────────────────────────────────────────
option(CONSOLE_BUILD "Build as console app for debugging" OFF)
if(CONSOLE_BUILD)
    add_executable(WhiteRectFitter src/main.cpp)
    target_compile_definitions(WhiteRectFitter PRIVATE CONSOLE_BUILD)
else()
    add_executable(WhiteRectFitter WIN32 src/main.cpp)
endif()

# ── 资源编译 ─────────────────────────────────────────────────────
configure_file(${CMAKE_SOURCE_DIR}/src/wrf.rc.in
               ${CMAKE_BINARY_DIR}/wrf.rc @ONLY)
target_sources(WhiteRectFitter PRIVATE
    ${CMAKE_BINARY_DIR}/wrf.rc
    ${CMAKE_SOURCE_DIR}/src/resource.h
)
target_include_directories(WhiteRectFitter PRIVATE ${CMAKE_SOURCE_DIR}/src)

# ── 编译选项 ─────────────────────────────────────────────────────
target_compile_definitions(WhiteRectFitter PRIVATE
    _UNICODE
    UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    $<$<CONFIG:Release>:NDEBUG>
)

target_compile_options(WhiteRectFitter PRIVATE
    $<$<CXX_COMPILER_ID:MSVC>:
        /W4 /WX- /MP
        $<$<CONFIG:Release>:/O2>
    >
    $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:
        -Wall -Wextra
        $<$<CONFIG:Release>:-O3>
    >
)

# ── 链接库 ───────────────────────────────────────────────────────
# winmm：MCI 音频
target_link_libraries(WhiteRectFitter PRIVATE winmm)

# ── 可选：OpenCV（启用 --preprocess 模式）────────────────────────
option(WITH_OPENCV "Enable --preprocess mode (requires OpenCV 4.x)" ON)

if(WITH_OPENCV)
    if(NOT OpenCV_DIR)
        set(OpenCV_DIR "D:/OpenCV/BuildStatic" CACHE PATH "OpenCV CMake 配置目录")
    endif()
    find_package(OpenCV QUIET)
    if(OpenCV_FOUND)
        target_compile_definitions(WhiteRectFitter PRIVATE WITH_OPENCV)
        target_include_directories(WhiteRectFitter PRIVATE ${OpenCV_INCLUDE_DIRS})
        target_link_libraries(WhiteRectFitter PRIVATE ${OpenCV_LIBS})
        message(STATUS "OpenCV ${OpenCV_VERSION} found — --preprocess 模式已启用")
    else()
        message(WARNING "OpenCV 未找到 — 仅 --play 模式可用（--preprocess 请用 Python）")
    endif()
endif()

# ── Release 优化 ─────────────────────────────────────────────────
if(MSVC)
    # 静态链接 CRT（匹配 OpenCV 静态构建）
    set_property(TARGET WhiteRectFitter PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    set_target_properties(WhiteRectFitter PROPERTIES
        LINK_FLAGS_RELEASE "/OPT:REF /OPT:ICF"
    )
endif()

# ── 安装 ─────────────────────────────────────────────────────────
install(TARGETS WhiteRectFitter RUNTIME DESTINATION bin)
