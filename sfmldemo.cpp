#include <SFML/Graphics.hpp>

#include <array>
#include <cmath>
#include <optional>

namespace {
constexpr unsigned WindowWidth = 1000;
constexpr unsigned WindowHeight = 700;

constexpr int GridWidth = 16;
constexpr int GridHeight = 16;

constexpr float TileWidth = 64.0f;
constexpr float TileHeight = 32.0f;
constexpr float OriginX = WindowWidth * 0.5f;
constexpr float OriginY = 80.0f;

sf::Vector2f isoToScreen(int x, int y) {
    return {
        OriginX + (x - y) * (TileWidth * 0.5f),
        OriginY + (x + y) * (TileHeight * 0.5f)
    };
}

std::optional<sf::Vector2i> screenToCell(const sf::Vector2f& position) {
    const float normalizedX = (position.x - OriginX) / (TileWidth * 0.5f);
    const float normalizedY = (position.y - OriginY) / (TileHeight * 0.5f);

    const float gridX = 0.5f * (normalizedX + normalizedY);
    const float gridY = 0.5f * (normalizedY - normalizedX);

    const int cellX = static_cast<int>(std::floor(gridX));
    const int cellY = static_cast<int>(std::floor(gridY));

    if (cellX < 0 || cellX >= GridWidth || cellY < 0 || cellY >= GridHeight) {
        return std::nullopt;
    }

    return sf::Vector2i(cellX, cellY);
}

sf::VertexArray makeIsoGrid() {
    sf::VertexArray lines(sf::Lines);

    const sf::Color gridColor(90, 110, 130);

    for (int y = 0; y <= GridHeight; ++y) {
        const sf::Vector2f a = isoToScreen(0, y);
        const sf::Vector2f b = isoToScreen(GridWidth, y);

        lines.append(sf::Vertex(a, gridColor));
        lines.append(sf::Vertex(b, gridColor));
    }

    for (int x = 0; x <= GridWidth; ++x) {
        const sf::Vector2f a = isoToScreen(x, 0);
        const sf::Vector2f b = isoToScreen(x, GridHeight);

        lines.append(sf::Vertex(a, gridColor));
        lines.append(sf::Vertex(b, gridColor));
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

struct AppState {
    sf::View view;
    sf::Vector2i selectedCell{4, 4};
    bool isPanning = false;
    sf::Vector2i lastPanPosition{0, 0};
};

void handleResize(const sf::Event::SizeEvent& sizeEvent, AppState& state) {
    state.view.setSize(
        static_cast<float>(sizeEvent.width),
        static_cast<float>(sizeEvent.height)
    );
}

void handleMousePress(
    const sf::Event::MouseButtonEvent& mouseEvent,
    sf::RenderWindow& window,
    AppState& state
) {
    if (mouseEvent.button == sf::Mouse::Right) {
        state.isPanning = true;
        state.lastPanPosition = {mouseEvent.x, mouseEvent.y};
        return;
    }

    if (mouseEvent.button != sf::Mouse::Left) {
        return;
    }

    const sf::Vector2f worldPosition = window.mapPixelToCoords(
        {mouseEvent.x, mouseEvent.y},
        state.view
    );

    if (const std::optional<sf::Vector2i> hitCell = screenToCell(worldPosition)) {
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
    AppState& state
) {
    if (!state.isPanning) {
        return;
    }

    const sf::Vector2i currentPosition(moveEvent.x, moveEvent.y);
    const sf::Vector2f previousWorld = window.mapPixelToCoords(
        state.lastPanPosition,
        state.view
    );
    const sf::Vector2f currentWorld = window.mapPixelToCoords(currentPosition, state.view);

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

void drawFrame(
    sf::RenderWindow& window,
    const sf::VertexArray& grid,
    const AppState& state
) {
    window.clear(sf::Color(20, 24, 30));
    window.setView(state.view);
    window.draw(grid);
    window.draw(makeTileOutline(state.selectedCell.x, state.selectedCell.y));
    window.display();
}
}

int main() {
    sf::RenderWindow window(
        sf::VideoMode(WindowWidth, WindowHeight),
        "Static Isometric Grid"
    );

    window.setFramerateLimit(60);

    const sf::VertexArray grid = makeIsoGrid();
    AppState state{window.getDefaultView()};

    while (window.isOpen()) {
        sf::Event event{};

        while (window.pollEvent(event)) {
            handleEvent(window, event, state);
        }

        drawFrame(window, grid, state);
    }

    return 0;
}
