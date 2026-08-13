#include <core.hpp>

#include <arpa/inet.h>
#include <cerrno>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <memory>
#include <string>

namespace {

constexpr size_t RequestBufferSize = 16 * 1024;
constexpr int ListenBacklog = 16;

struct Endpoint {
    string path {};
    string body {};
};

class FileDescriptor {
public:
    explicit FileDescriptor(int descriptor = -1) : descriptor_(descriptor) {}
    FileDescriptor(const FileDescriptor&) = delete;
    FileDescriptor& operator=(const FileDescriptor&) = delete;

    ~FileDescriptor() {
        if (descriptor_ >= 0) {
            close(descriptor_);
        }
    }

    int get() const { return descriptor_; }

private:
    int descriptor_;
};

CellPtr error(const char* message) { return make_error_cell(message); }

bool write_all(int descriptor, const string& response) {
    size_t offset = 0;
    while (offset < response.size()) {
        const ssize_t written = send(descriptor, response.data() + offset, response.size() - offset, MSG_NOSIGNAL);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return false;
        }
        offset += static_cast<size_t>(written);
    }
    return true;
}

string response_for(int status, const string& reason, const string& body) {
    return "HTTP/1.1 " + to_string(status) + " " + reason + "\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Length: " + to_string(body.size()) + "\r\n"
        "Connection: close\r\n\r\n" + body;
}

CellPtr endpoints_from(const CellPtr& endpoints_cell, vector<Endpoint>& endpoints) {
    if (!endpoints_cell || endpoints_cell->type != Cell::Type::vec) {
        return error("web.serve expects an endpoint vector");
    }
    const shared_ptr<VecCell> endpoint_cells = static_pointer_cast<VecCell>(endpoints_cell);

    endpoints.reserve(endpoint_cells->value.size());
    for (const CellPtr& endpoint_cell : endpoint_cells->value) {
        const shared_ptr<ScopeCell> endpoint = expect_scope_cell(endpoint_cell);
        const StrCell* path = endpoint ? map_field_string(endpoint, "path") : nullptr;
        const StrCell* body = endpoint ? map_field_string(endpoint, "body") : nullptr;
        if (!path || path->value.empty() || path->value.front() != '/' || !body) {
            return error("each web endpoint requires a slash-prefixed string path and string body");
        }
        for (const Endpoint& existing : endpoints) {
            if (existing.path == path->value) {
                return error("web.serve endpoint paths must be unique");
            }
        }
        endpoints.push_back({path->value, body->value});
    }

    return nullptr;
}

string request_path_from(int descriptor) {
    char request[RequestBufferSize];
    const ssize_t received = recv(descriptor, request, sizeof(request), 0);
    if (received <= 0) {
        return {};
    }

    const string request_text(request, static_cast<size_t>(received));
    const size_t first_space = request_text.find(' ');
    const size_t second_space = request_text.find(' ', first_space + 1);
    if (first_space == string::npos || second_space == string::npos) {
        return {};
    }
    return request_text.substr(first_space + 1, second_space - first_space - 1);
}

const Endpoint* endpoint_for(const vector<Endpoint>& endpoints, const string& path) {
    for (const Endpoint& endpoint : endpoints) {
        if (endpoint.path == path) {
            return &endpoint;
        }
    }
    return nullptr;
}

CellPtr builtin_serve(const vector<CellPtr>& arguments, CellPtr current_vm) {
    if (!expect_vm_cell(move(current_vm))) {
        return error("web.serve requires a map VM");
    }
    if (CellPtr arity_error = expect_form_arity(arguments.size(), 3, "web.serve")) {
        return arity_error;
    }

    const shared_ptr<ScopeCell> config = expect_scope_cell(arguments[1]);
    if (!config) {
        return error("web.serve expects an inline server configuration mapping");
    }

    const IntCell* port_cell = map_field_int(config, "port");
    const IntCell* max_requests_cell = map_field_int(config, "max_requests");
    if (!port_cell || port_cell->value < 1 || port_cell->value > 65535) {
        return error("web.serve configuration requires port between 1 and 65535");
    }
    const int64_t max_requests = max_requests_cell ? max_requests_cell->value : 0;
    if (max_requests < 0) {
        return error("web.serve max_requests must be zero or positive");
    }

    vector<Endpoint> endpoints;
    if (CellPtr endpoint_error = endpoints_from(arguments[2], endpoints)) {
        return endpoint_error;
    }

    FileDescriptor listener(socket(AF_INET, SOCK_STREAM, 0));
    if (listener.get() < 0) {
        return error("web.serve could not create a socket");
    }
    const int reuse_address = 1;
    if (setsockopt(listener.get(), SOL_SOCKET, SO_REUSEADDR, &reuse_address, sizeof(reuse_address)) < 0) {
        return error("web.serve could not configure its socket");
    }

    sockaddr_in address {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(static_cast<uint16_t>(port_cell->value));
    if (bind(listener.get(), reinterpret_cast<const sockaddr*>(&address), sizeof(address)) < 0) {
        return error("web.serve could not bind 127.0.0.1:port");
    }
    if (listen(listener.get(), ListenBacklog) < 0) {
        return error("web.serve could not listen for connections");
    }

    int64_t served = 0;
    while (max_requests == 0 || served < max_requests) {
        const int accepted = accept(listener.get(), nullptr, nullptr);
        if (accepted < 0) {
            if (errno == EINTR) {
                continue;
            }
            return error("web.serve could not accept a connection");
        }

        FileDescriptor client(accepted);
        const string path = request_path_from(client.get());
        const Endpoint* endpoint = endpoint_for(endpoints, path);
        const string response = endpoint
            ? response_for(200, "OK", endpoint->body)
            : response_for(404, "Not Found", "Not found");
        if (!write_all(client.get(), response)) {
            return error("web.serve could not write a response");
        }
        ++served;
    }

    return make_shared<IntCell>(served);
}

void install_builtin(const shared_ptr<ScopeCell>& module, const string& name, FunCell::Implementation implementation) {
    module->set(name, make_shared<FunCell>(move(implementation)));
}

} // namespace

extern "C" CellPtr helix_install_module(const shared_ptr<ScopeCell>& module) {
    install_builtin(module, "serve", builtin_serve);
    return nullptr;
}
