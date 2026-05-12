#include <core.hpp>

#include <SFML/Graphics.hpp>

#include <cmath>
#include <memory>
#include <optional>
#include <string>

namespace {
constexpr unsigned WindowWidth = 1000;
constexpr unsigned WindowHeight = 700;

constexpr int GridWidth = 16;
constexpr int GridHeight = 16;

constexpr float TileWidth = 64.0f;
constexpr float TileHeight = 32.0f;
constexpr float OriginX = WindowWidth * 0.5f;
constexpr float OriginY = 80.0f;

struct AppState {
    sf::View view {};
    sf::Vector2i selectedCell {4, 4};
    bool isPanning = false;
    sf::Vector2i lastPanPosition {0, 0};
};

unique_ptr<sf::RenderWindow> global_window {};
sf::VertexArray global_grid {sf::Lines};
sf::VertexArray global_outline {sf::LineStrip};
sf::VertexArray global_lines {sf::Lines};
AppState global_state {};
sf::Event global_event {};
bool has_global_event = false;
bool is_initialized = false;

CellPtr make_error(const string& message) {
    return make_error_cell(message);
}

shared_ptr<VmCell> expect_vm(CellPtr current_vm, const char* who, size_t expected_arity, const vector<CellPtr>& arguments) {
    shared_ptr<VmCell> vm = expect_vm_cell(move(current_vm));
    if (!vm) {
        return nullptr;
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), expected_arity, who);
    if (arity_error) {
        return nullptr;
    }

    return vm;
}

CellPtr expect_integer_argument(const vector<CellPtr>& arguments, size_t index, const char* who) {
    if (index >= arguments.size()) {
        return make_error_cell(string(who) + " is missing an integer argument");
    }

    return expect_int_cell(arguments[index], who);
}

optional<int64_t> argument_int(const vector<CellPtr>& arguments, size_t index, const char* who, CellPtr& error) {
    CellPtr value = expect_integer_argument(arguments, index, who);
    if (is_signal_cell(value)) {
        error = value;
        return nullopt;
    }

    return static_cast<const IntCell&>(*value).value;
}

shared_ptr<ScopeCell> make_point_cell(int64_t x, int64_t y) {
    shared_ptr<ScopeCell> point = make_shared<ScopeCell>();
    point->set("x", make_shared<IntCell>(x));
    point->set("y", make_shared<IntCell>(y));
    return point;
}

shared_ptr<ScopeCell> make_state_cell() {
    shared_ptr<ScopeCell> state = make_shared<ScopeCell>();
    state->set("selected_x", make_shared<IntCell>(global_state.selectedCell.x));
    state->set("selected_y", make_shared<IntCell>(global_state.selectedCell.y));
    state->set("is_panning", make_shared<IntCell>(global_state.isPanning ? 1 : 0));
    state->set("last_pan_x", make_shared<IntCell>(global_state.lastPanPosition.x));
    state->set("last_pan_y", make_shared<IntCell>(global_state.lastPanPosition.y));
    return state;
}

sf::Mouse::Button decode_mouse_button(int64_t button) {
    switch (button) {
    case 0:
        return sf::Mouse::Left;
    case 1:
        return sf::Mouse::Right;
    case 2:
        return sf::Mouse::Middle;
    default:
        return sf::Mouse::ButtonCount;
    }
}

void ensure_initialized() {
    if (is_initialized) {
        return;
    }

    global_window = make_unique<sf::RenderWindow>(
        sf::VideoMode(WindowWidth, WindowHeight),
        "Static Isometric Grid");
    global_window->setFramerateLimit(60);
    global_state = AppState {global_window->getDefaultView()};
    global_grid = sf::VertexArray(sf::Lines);
    global_outline = sf::VertexArray(sf::LineStrip, 5);
    global_lines = sf::VertexArray(sf::Lines);
    has_global_event = false;
    is_initialized = true;
}

sf::Vector2f isoToScreen(int x, int y) {
    return {
        OriginX + static_cast<float>(x - y) * (TileWidth * 0.5f),
        OriginY + static_cast<float>(x + y) * (TileHeight * 0.5f)
    };
}

optional<sf::Vector2i> screenToCell(const sf::Vector2f& position) {
    const float normalizedX = (position.x - OriginX) / (TileWidth * 0.5f);
    const float normalizedY = (position.y - OriginY) / (TileHeight * 0.5f);

    const float gridX = 0.5f * (normalizedX + normalizedY);
    const float gridY = 0.5f * (normalizedY - normalizedX);

    const int cellX = static_cast<int>(floor(gridX));
    const int cellY = static_cast<int>(floor(gridY));

    if (cellX < 0 || cellX >= GridWidth || cellY < 0 || cellY >= GridHeight) {
        return nullopt;
    }

    return sf::Vector2i(cellX, cellY);
}

void appendLine(
    sf::VertexArray& vertices,
    const sf::Vector2f& start,
    const sf::Vector2f& end,
    const sf::Color& color) {
    vertices.append(sf::Vertex(start, color));
    vertices.append(sf::Vertex(end, color));
}

sf::VertexArray makeIsoGrid() {
    sf::VertexArray lines(sf::Lines);
    const sf::Color gridColor(90, 110, 130);

    for (int y = 0; y <= GridHeight; ++y) {
        appendLine(lines, isoToScreen(0, y), isoToScreen(GridWidth, y), gridColor);
    }

    for (int x = 0; x <= GridWidth; ++x) {
        appendLine(lines, isoToScreen(x, 0), isoToScreen(x, GridHeight), gridColor);
    }

    return lines;
}

sf::VertexArray makeTileOutline(int x, int y) {
    sf::VertexArray outline(sf::LineStrip, 5);

    const sf::Vector2f top = isoToScreen(x, y);
    const sf::Vector2f right = isoToScreen(x + 1, y);
    const sf::Vector2f bottom = isoToScreen(x + 1, y + 1);
    const sf::Vector2f left = isoToScreen(x, y + 1);
    const sf::Color color(180, 210, 240);

    outline[0] = sf::Vertex(top, color);
    outline[1] = sf::Vertex(right, color);
    outline[2] = sf::Vertex(bottom, color);
    outline[3] = sf::Vertex(left, color);
    outline[4] = sf::Vertex(top, color);

    return outline;
}

sf::Vector2f mapPixelToWorld(
    sf::RenderWindow& window,
    const sf::Vector2i& pixelPosition,
    const AppState& state) {
    return window.mapPixelToCoords(pixelPosition, state.view);
}

void handleResize(const sf::Event::SizeEvent& sizeEvent, AppState& state) {
    state.view.setSize(static_cast<float>(sizeEvent.width), static_cast<float>(sizeEvent.height));
}

void handleMousePress(
    const sf::Event::MouseButtonEvent& mouseEvent,
    sf::RenderWindow& window,
    AppState& state) {
    if (mouseEvent.button == sf::Mouse::Right) {
        state.isPanning = true;
        state.lastPanPosition = {mouseEvent.x, mouseEvent.y};
        return;
    }

    if (mouseEvent.button != sf::Mouse::Left) {
        return;
    }

    const sf::Vector2f worldPosition = mapPixelToWorld(window, {mouseEvent.x, mouseEvent.y}, state);
    optional<sf::Vector2i> hitCell = screenToCell(worldPosition);
    if (hitCell) {
        state.selectedCell = *hitCell;
    }
}

void handleMouseRelease(const sf::Event::MouseButtonEvent& mouseEvent, AppState& state) {
    if (mouseEvent.button == sf::Mouse::Right) {
        state.isPanning = false;
    }
}

void handleMouseMove(
    const sf::Event::MouseMoveEvent& moveEvent,
    sf::RenderWindow& window,
    AppState& state) {
    if (!state.isPanning) {
        return;
    }

    const sf::Vector2i currentPosition(moveEvent.x, moveEvent.y);
    const sf::Vector2f previousWorld = mapPixelToWorld(window, state.lastPanPosition, state);
    const sf::Vector2f currentWorld = mapPixelToWorld(window, currentPosition, state);

    state.view.move(previousWorld - currentWorld);
    state.lastPanPosition = currentPosition;
}

void handleEvent(sf::RenderWindow& window, const sf::Event& event, AppState& state) {
    switch (event.type) {
    case sf::Event::Closed:
        window.close();
        break;
    case sf::Event::Resized:
        handleResize(event.size, state);
        break;
    case sf::Event::MouseButtonPressed:
        handleMousePress(event.mouseButton, window, state);
        break;
    case sf::Event::MouseButtonReleased:
        handleMouseRelease(event.mouseButton, state);
        break;
    case sf::Event::MouseMoved:
        handleMouseMove(event.mouseMove, window, state);
        break;
    default:
        break;
    }
}

void drawFrame(sf::RenderWindow& window, const sf::VertexArray& grid, const AppState& state) {
    window.clear(sf::Color(20, 24, 30));
    window.setView(state.view);
    window.draw(grid);
    window.draw(makeTileOutline(state.selectedCell.x, state.selectedCell.y));
    window.display();
}

CellPtr builtin_sfml_initialize(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.initialize", 1, arguments);
    if (!vm) {
        return make_error("sfml.initialize requires a VM");
    }

    ensure_initialized();
    global_grid = makeIsoGrid();
    global_outline = makeTileOutline(global_state.selectedCell.x, global_state.selectedCell.y);
    return make_state_cell();
}

CellPtr builtin_sfml_iso_to_screen(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.iso-to-screen", 3, arguments);
    if (!vm) {
        return make_error("sfml.iso-to-screen requires a VM");
    }

    CellPtr error = nullptr;
    optional<int64_t> x = argument_int(arguments, 1, "sfml.iso-to-screen", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 2, "sfml.iso-to-screen", error);
    if (!y) {
        return error;
    }

    const sf::Vector2f point = isoToScreen(static_cast<int>(*x), static_cast<int>(*y));
    return make_point_cell(llround(point.x), llround(point.y));
}

CellPtr builtin_sfml_screen_to_cell(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.screen-to-cell", 3, arguments);
    if (!vm) {
        return make_error("sfml.screen-to-cell requires a VM");
    }

    CellPtr error = nullptr;
    optional<int64_t> x = argument_int(arguments, 1, "sfml.screen-to-cell", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 2, "sfml.screen-to-cell", error);
    if (!y) {
        return error;
    }

    optional<sf::Vector2i> cell = screenToCell(sf::Vector2f(static_cast<float>(*x), static_cast<float>(*y)));
    if (!cell) {
        return make_shared<NilCell>();
    }

    return make_point_cell(cell->x, cell->y);
}

CellPtr builtin_sfml_append_line(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.append-line", 8, arguments);
    if (!vm) {
        return make_error("sfml.append-line requires a VM");
    }

    CellPtr error = nullptr;
    optional<int64_t> x1 = argument_int(arguments, 1, "sfml.append-line", error);
    if (!x1) {
        return error;
    }
    optional<int64_t> y1 = argument_int(arguments, 2, "sfml.append-line", error);
    if (!y1) {
        return error;
    }
    optional<int64_t> x2 = argument_int(arguments, 3, "sfml.append-line", error);
    if (!x2) {
        return error;
    }
    optional<int64_t> y2 = argument_int(arguments, 4, "sfml.append-line", error);
    if (!y2) {
        return error;
    }
    optional<int64_t> r = argument_int(arguments, 5, "sfml.append-line", error);
    if (!r) {
        return error;
    }
    optional<int64_t> g = argument_int(arguments, 6, "sfml.append-line", error);
    if (!g) {
        return error;
    }
    optional<int64_t> b = argument_int(arguments, 7, "sfml.append-line", error);
    if (!b) {
        return error;
    }

    appendLine(
        global_lines,
        sf::Vector2f(static_cast<float>(*x1), static_cast<float>(*y1)),
        sf::Vector2f(static_cast<float>(*x2), static_cast<float>(*y2)),
        sf::Color(static_cast<sf::Uint8>(*r), static_cast<sf::Uint8>(*g), static_cast<sf::Uint8>(*b)));
    return make_shared<IntCell>(static_cast<int64_t>(global_lines.getVertexCount()));
}

CellPtr builtin_sfml_make_iso_grid(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.make-iso-grid", 1, arguments);
    if (!vm) {
        return make_error("sfml.make-iso-grid requires a VM");
    }

    global_grid = makeIsoGrid();
    return make_shared<IntCell>(static_cast<int64_t>(global_grid.getVertexCount()));
}

CellPtr builtin_sfml_make_tile_outline(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.make-tile-outline", 3, arguments);
    if (!vm) {
        return make_error("sfml.make-tile-outline requires a VM");
    }

    CellPtr error = nullptr;
    optional<int64_t> x = argument_int(arguments, 1, "sfml.make-tile-outline", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 2, "sfml.make-tile-outline", error);
    if (!y) {
        return error;
    }

    global_outline = makeTileOutline(static_cast<int>(*x), static_cast<int>(*y));
    return make_shared<IntCell>(static_cast<int64_t>(global_outline.getVertexCount()));
}

CellPtr builtin_sfml_map_pixel_to_world(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.map-pixel-to-world", 3, arguments);
    if (!vm) {
        return make_error("sfml.map-pixel-to-world requires a VM");
    }

    ensure_initialized();
    CellPtr error = nullptr;
    optional<int64_t> x = argument_int(arguments, 1, "sfml.map-pixel-to-world", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 2, "sfml.map-pixel-to-world", error);
    if (!y) {
        return error;
    }

    const sf::Vector2f point = mapPixelToWorld(*global_window, sf::Vector2i(static_cast<int>(*x), static_cast<int>(*y)), global_state);
    return make_point_cell(llround(point.x), llround(point.y));
}

CellPtr builtin_sfml_handle_resize(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.handle-resize", 3, arguments);
    if (!vm) {
        return make_error("sfml.handle-resize requires a VM");
    }

    ensure_initialized();
    CellPtr error = nullptr;
    optional<int64_t> width = argument_int(arguments, 1, "sfml.handle-resize", error);
    if (!width) {
        return error;
    }
    optional<int64_t> height = argument_int(arguments, 2, "sfml.handle-resize", error);
    if (!height) {
        return error;
    }

    sf::Event::SizeEvent event {};
    event.width = static_cast<unsigned>(*width);
    event.height = static_cast<unsigned>(*height);
    handleResize(event, global_state);
    return make_state_cell();
}

CellPtr builtin_sfml_handle_mouse_press(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.handle-mouse-press", 4, arguments);
    if (!vm) {
        return make_error("sfml.handle-mouse-press requires a VM");
    }

    ensure_initialized();
    CellPtr error = nullptr;
    optional<int64_t> button = argument_int(arguments, 1, "sfml.handle-mouse-press", error);
    if (!button) {
        return error;
    }
    optional<int64_t> x = argument_int(arguments, 2, "sfml.handle-mouse-press", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 3, "sfml.handle-mouse-press", error);
    if (!y) {
        return error;
    }

    sf::Mouse::Button mouse_button = decode_mouse_button(*button);
    if (mouse_button == sf::Mouse::ButtonCount) {
        return make_error("sfml.handle-mouse-press received an unknown mouse button");
    }

    sf::Event::MouseButtonEvent event {};
    event.button = mouse_button;
    event.x = static_cast<int>(*x);
    event.y = static_cast<int>(*y);
    handleMousePress(event, *global_window, global_state);
    return make_state_cell();
}

CellPtr builtin_sfml_handle_mouse_release(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.handle-mouse-release", 4, arguments);
    if (!vm) {
        return make_error("sfml.handle-mouse-release requires a VM");
    }

    ensure_initialized();
    CellPtr error = nullptr;
    optional<int64_t> button = argument_int(arguments, 1, "sfml.handle-mouse-release", error);
    if (!button) {
        return error;
    }
    optional<int64_t> x = argument_int(arguments, 2, "sfml.handle-mouse-release", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 3, "sfml.handle-mouse-release", error);
    if (!y) {
        return error;
    }

    sf::Mouse::Button mouse_button = decode_mouse_button(*button);
    if (mouse_button == sf::Mouse::ButtonCount) {
        return make_error("sfml.handle-mouse-release received an unknown mouse button");
    }

    sf::Event::MouseButtonEvent event {};
    event.button = mouse_button;
    event.x = static_cast<int>(*x);
    event.y = static_cast<int>(*y);
    handleMouseRelease(event, global_state);
    return make_state_cell();
}

CellPtr builtin_sfml_handle_mouse_move(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.handle-mouse-move", 3, arguments);
    if (!vm) {
        return make_error("sfml.handle-mouse-move requires a VM");
    }

    ensure_initialized();
    CellPtr error = nullptr;
    optional<int64_t> x = argument_int(arguments, 1, "sfml.handle-mouse-move", error);
    if (!x) {
        return error;
    }
    optional<int64_t> y = argument_int(arguments, 2, "sfml.handle-mouse-move", error);
    if (!y) {
        return error;
    }

    sf::Event::MouseMoveEvent event {};
    event.x = static_cast<int>(*x);
    event.y = static_cast<int>(*y);
    handleMouseMove(event, *global_window, global_state);
    return make_state_cell();
}

CellPtr builtin_sfml_poll_event(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.poll-event", 1, arguments);
    if (!vm) {
        return make_error("sfml.poll-event requires a VM");
    }

    ensure_initialized();
    has_global_event = global_window->pollEvent(global_event);
    return make_shared<IntCell>(has_global_event ? 1 : 0);
}

CellPtr builtin_sfml_handle_event(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.handle-event", 1, arguments);
    if (!vm) {
        return make_error("sfml.handle-event requires a VM");
    }

    ensure_initialized();
    if (!has_global_event) {
        return make_error("sfml.handle-event requires a previously polled event");
    }

    handleEvent(*global_window, global_event, global_state);
    global_outline = makeTileOutline(global_state.selectedCell.x, global_state.selectedCell.y);
    return make_state_cell();
}

CellPtr builtin_sfml_draw_frame(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.draw-frame", 1, arguments);
    if (!vm) {
        return make_error("sfml.draw-frame requires a VM");
    }

    ensure_initialized();
    if (global_grid.getVertexCount() == 0) {
        global_grid = makeIsoGrid();
    }
    drawFrame(*global_window, global_grid, global_state);
    return make_state_cell();
}

CellPtr builtin_sfml_is_open(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> vm = expect_vm(move(current_vm), "sfml.is-open", 1, arguments);
    if (!vm) {
        return make_error("sfml.is-open requires a VM");
    }

    ensure_initialized();
    return make_shared<IntCell>(global_window->isOpen() ? 1 : 0);
}

void install_builtin(const shared_ptr<ScopeCell>& module, const string& name, FunCell::Implementation implementation) {
    module->set(name, make_shared<FunCell>(move(implementation)));
}
}

void install_sfml_namespace(const shared_ptr<ScopeCell>& zygote) {
    shared_ptr<ScopeCell> module = make_shared<ScopeCell>();
    install_builtin(module, "initialize", builtin_sfml_initialize);
    install_builtin(module, "iso-to-screen", builtin_sfml_iso_to_screen);
    install_builtin(module, "screen-to-cell", builtin_sfml_screen_to_cell);
    install_builtin(module, "append-line", builtin_sfml_append_line);
    install_builtin(module, "make-iso-grid", builtin_sfml_make_iso_grid);
    install_builtin(module, "make-tile-outline", builtin_sfml_make_tile_outline);
    install_builtin(module, "map-pixel-to-world", builtin_sfml_map_pixel_to_world);
    install_builtin(module, "handle-resize", builtin_sfml_handle_resize);
    install_builtin(module, "handle-mouse-press", builtin_sfml_handle_mouse_press);
    install_builtin(module, "handle-mouse-release", builtin_sfml_handle_mouse_release);
    install_builtin(module, "handle-mouse-move", builtin_sfml_handle_mouse_move);
    install_builtin(module, "poll-event", builtin_sfml_poll_event);
    install_builtin(module, "handle-event", builtin_sfml_handle_event);
    install_builtin(module, "draw-frame", builtin_sfml_draw_frame);
    install_builtin(module, "is-open", builtin_sfml_is_open);
    zygote->set("sfml", module);
}
