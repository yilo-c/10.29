# 设备桥原生辅助：只编一个极小的 so，不引入任何第三方依赖。
cmake_minimum_required(VERSION 3.18)
project(siyi_bridge C)

add_library(siyi_bridge SHARED bridge.c)
find_library(log-lib log)
target_link_libraries(siyi_bridge ${log-lib})
