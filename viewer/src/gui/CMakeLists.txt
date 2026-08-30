# Included from the viewer root after it has found the installed Qt major and
# defined icirv_core. Keeping the widgets in a library lets QtTest link the
# actual MainWindow instead of rebuilding a private copy of the GUI sources.

# This is the only part that needs Qt. The root project includes it only when
# ICIRV_BUILD_GUI is enabled, so the CLI can still be configured and linked
# statically on a machine without Qt.
add_library(icirv_gui STATIC
    engine_tree_model.cpp
    main_window.cpp
    ${CMAKE_SOURCE_DIR}/include/icirv/gui/engine_tree_model.hpp
    ${CMAKE_SOURCE_DIR}/include/icirv/gui/main_window.hpp
)
target_link_libraries(icirv_gui PUBLIC icirv_core Qt${ICIRV_QT_MAJOR}::Widgets)

# The headers are listed explicitly because they sit under include/ rather than
# beside their .cpp. AUTOMOC finds a Q_OBJECT header on its own only when it
# shares a directory and a basename with a source in the target; anywhere else
# it has to be named here or moc never runs on it.
add_executable(icirv-gui
    gui_main.cpp
)
target_link_libraries(icirv-gui PRIVATE icirv_gui)
