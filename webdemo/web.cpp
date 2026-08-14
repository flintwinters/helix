#include <core.hpp>
#include <ryml_interface.hpp>

#include <arpa/inet.h>
#include <cerrno>
#include <charconv>
#include <cctype>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <memory>
#include <string>
#include <string_view>

namespace {

constexpr size_t HeaderLimit = 32 * 1024;
constexpr size_t BodyLimit = 1024 * 1024;
constexpr size_t ReadChunkSize = 16 * 1024;
constexpr int ListenBacklog = 16;
constexpr const char* YamlContentType = "application/yaml; charset=utf-8";

enum class RequestReadResult {
    success,
    bad_request,
    payload_too_large,
};

struct HttpRequest {
    string method {};
    string path {};
    string content_type {};
    bool has_content_type {false};
    string body {};
};

struct HttpResponse {
    int status {200};
    string content_type {YamlContentType};
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

CellPtr error(const string& message) { return make_error_cell(message); }

string lowercase(string value) {
    for (char& character : value) {
        character = static_cast<char>(tolower(static_cast<unsigned char>(character)));
    }
    return value;
}

string trim(string_view value) {
    size_t begin = 0;
    while (begin < value.size() && isspace(static_cast<unsigned char>(value[begin]))) {
        ++begin;
    }
    size_t end = value.size();
    while (end > begin && isspace(static_cast<unsigned char>(value[end - 1]))) {
        --end;
    }
    return string(value.substr(begin, end - begin));
}

bool has_line_break(const string& value) {
    return value.find('\r') != string::npos || value.find('\n') != string::npos;
}

bool is_yaml_content_type(const string& content_type) {
    const size_t parameter = content_type.find(';');
    return lowercase(trim(string_view(content_type).substr(0, parameter))) == "application/yaml";
}

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

bool read_more(int descriptor, string& buffer) {
    char chunk[ReadChunkSize];
    while (true) {
        const ssize_t received = recv(descriptor, chunk, sizeof(chunk), 0);
        if (received > 0) {
            buffer.append(chunk, static_cast<size_t>(received));
            return true;
        }
        if (received < 0 && errno == EINTR) {
            continue;
        }
        return false;
    }
}

bool parse_request_line(string_view line, HttpRequest& request) {
    const size_t first_space = line.find(' ');
    const size_t second_space = line.find(' ', first_space == string_view::npos ? first_space : first_space + 1);
    if (first_space == string_view::npos || second_space == string_view::npos) {
        return false;
    }

    request.method = string(line.substr(0, first_space));
    request.path = string(line.substr(first_space + 1, second_space - first_space - 1));
    const string_view version = line.substr(second_space + 1);
    return !request.method.empty() && !request.path.empty() && version.starts_with("HTTP/");
}

RequestReadResult parse_content_length(const string& value, size_t& content_length) {
    if (value.empty()) {
        return RequestReadResult::bad_request;
    }
    uint64_t parsed = 0;
    const char* begin = value.data();
    const char* end = begin + value.size();
    const auto result = from_chars(begin, end, parsed);
    if (result.ec != errc {} || result.ptr != end) {
        return RequestReadResult::bad_request;
    }
    if (parsed > BodyLimit) {
        return RequestReadResult::payload_too_large;
    }
    content_length = static_cast<size_t>(parsed);
    return RequestReadResult::success;
}

RequestReadResult parse_headers(
    string_view header_text,
    HttpRequest& request,
    size_t& content_length) {
    const size_t request_line_end = header_text.find("\r\n");
    if (request_line_end == string_view::npos
        || !parse_request_line(header_text.substr(0, request_line_end), request)) {
        return RequestReadResult::bad_request;
    }

    bool has_content_length = false;
    size_t offset = request_line_end + 2;
    while (offset < header_text.size()) {
        const size_t line_end = header_text.find("\r\n", offset);
        const string_view line = header_text.substr(offset, line_end - offset);
        const size_t colon = line.find(':');
        if (colon == string_view::npos) {
            return RequestReadResult::bad_request;
        }

        const string name = lowercase(trim(line.substr(0, colon)));
        const string value = trim(line.substr(colon + 1));
        if (name.empty() || has_line_break(value)) {
            return RequestReadResult::bad_request;
        }
        if (name == "transfer-encoding") {
            return RequestReadResult::bad_request;
        }
        if (name == "content-type") {
            request.content_type = value;
            request.has_content_type = true;
        }
        if (name == "content-length") {
            size_t parsed_length = 0;
            const RequestReadResult length_result = parse_content_length(value, parsed_length);
            if (length_result != RequestReadResult::success) {
                return length_result;
            }
            if (has_content_length && parsed_length != content_length) {
                return RequestReadResult::bad_request;
            }
            content_length = parsed_length;
            has_content_length = true;
        }

        if (line_end == string_view::npos) {
            break;
        }
        offset = line_end + 2;
    }
    return RequestReadResult::success;
}

RequestReadResult read_request(int descriptor, HttpRequest& request) {
    string buffer;
    buffer.reserve(ReadChunkSize);
    size_t header_end = string::npos;
    while ((header_end = buffer.find("\r\n\r\n")) == string::npos) {
        if (buffer.size() > HeaderLimit) {
            return RequestReadResult::payload_too_large;
        }
        if (!read_more(descriptor, buffer)) {
            return RequestReadResult::bad_request;
        }
    }
    if (header_end > HeaderLimit) {
        return RequestReadResult::payload_too_large;
    }

    size_t content_length = 0;
    const RequestReadResult header_result = parse_headers(
        string_view(buffer).substr(0, header_end),
        request,
        content_length);
    if (header_result != RequestReadResult::success) {
        return header_result;
    }

    const size_t body_begin = header_end + 4;
    while (buffer.size() - body_begin < content_length) {
        if (!read_more(descriptor, buffer)) {
            return RequestReadResult::bad_request;
        }
    }
    request.body = buffer.substr(body_begin, content_length);
    return RequestReadResult::success;
}

CellPtr request_message_from(const HttpRequest& request) {
    shared_ptr<ScopeCell> message = make_shared<ScopeCell>();
    message->set("method", make_shared<StrCell>(request.method));
    message->set("path", make_shared<StrCell>(request.path));
    message->set(
        "content_type",
        request.has_content_type
            ? static_pointer_cast<Cell>(make_shared<StrCell>(request.content_type))
            : static_pointer_cast<Cell>(make_shared<NilCell>()));

    if (request.body.empty()) {
        message->set("body", make_shared<NilCell>());
    } else if (request.has_content_type && is_yaml_content_type(request.content_type)) {
        message->set("body", parse_yaml_cell(request.body));
    } else {
        message->set("body", make_shared<StrCell>(request.body));
    }
    return message;
}

const char* reason_phrase(int status) {
    switch (status) {
    case 200: return "OK";
    case 201: return "Created";
    case 202: return "Accepted";
    case 204: return "No Content";
    case 400: return "Bad Request";
    case 404: return "Not Found";
    case 413: return "Payload Too Large";
    case 500: return "Internal Server Error";
    default: return "Response";
    }
}

string render_response(const HttpResponse& response) {
    return "HTTP/1.1 " + to_string(response.status) + " " + reason_phrase(response.status) + "\r\n"
        "Content-Type: " + response.content_type + "\r\n"
        "Content-Length: " + to_string(response.body.size()) + "\r\n"
        "Connection: close\r\n\r\n" + response.body;
}

string error_response(int status, const string& body) {
    return render_response({status, "text/plain; charset=utf-8", body});
}

CellPtr response_from(CellPtr response_cell, HttpResponse& response) {
    shared_ptr<MapCell> response_map = expect_map_cell(response_cell);
    if (!response_map) {
        return error("message handler must return a response mapping");
    }

    if (CellPtr status_cell = map_field_cell(response_map, "status")) {
        if (status_cell->type != Cell::Type::integer) {
            return error("message handler response status must be an integer");
        }
        const int64_t status = static_cast<const IntCell&>(*status_cell).value;
        if (status < 200 || status > 599) {
            return error("message handler response status must be between 200 and 599");
        }
        response.status = static_cast<int>(status);
    }

    if (CellPtr content_type_cell = map_field_cell(response_map, "content_type")) {
        if (content_type_cell->type != Cell::Type::string) {
            return error("message handler response content_type must be a string");
        }
        response.content_type = static_cast<const StrCell&>(*content_type_cell).value;
        if (response.content_type.empty() || has_line_break(response.content_type)) {
            return error("message handler response content_type is invalid");
        }
    }

    CellPtr body = map_field_cell(response_map, "body");
    if (!body) {
        return error("message handler response requires a body");
    }
    if (is_yaml_content_type(response.content_type)) {
        response.body = emit_round_trip_yaml_from_cell(body);
        return nullptr;
    }
    if (body->type != Cell::Type::string) {
        return error("non-YAML message handler response bodies must be strings");
    }
    response.body = static_cast<const StrCell&>(*body).value;
    return nullptr;
}

CellPtr builtin_serve(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<VmCell> root_cell = expect_vm_cell(current_vm);
    if (!root_cell) {
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
        HttpRequest request;
        const RequestReadResult read_result = read_request(client.get(), request);
        ++served;
        if (read_result != RequestReadResult::success) {
            const int status = read_result == RequestReadResult::payload_too_large ? 413 : 400;
            if (!write_all(client.get(), error_response(status, reason_phrase(status)))) {
                return error("web.serve could not write an error response");
            }
            continue;
        }

        CellPtr message;
        try {
            message = request_message_from(request);
        } catch (const exception&) {
            if (!write_all(client.get(), error_response(400, "Invalid YAML body"))) {
                return error("web.serve could not write an error response");
            }
            continue;
        }

        CellPtr handler_result = invoke_message_handler(arguments[2], move(message), root_cell);
        if (is_signal_cell(handler_result)) {
            write_all(client.get(), error_response(500, "Message handler failed"));
            return handler_result;
        }

        HttpResponse response;
        CellPtr response_error = response_from(move(handler_result), response);
        if (response_error) {
            write_all(client.get(), error_response(500, "Invalid message handler response"));
            return response_error;
        }
        if (!write_all(client.get(), render_response(response))) {
            return error("web.serve could not write a response");
        }
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
