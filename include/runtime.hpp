#pragma once

#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

namespace helix {

using Scalar = std::variant<std::nullptr_t, bool, std::int64_t, double, std::string>;

struct Value;
struct Frame;
struct Signal;
struct Program;
struct VM;

using List = std::vector<Value>;
using Object = std::unordered_map<std::string, Value>;

struct Value {
  using Storage = std::variant<Scalar, List, Object>;

  Storage storage {};

  Value() = default;
  explicit Value(Storage data);
};

struct Signal {
  enum class Kind {
    none,
    error,
    returning,
  };

  Kind kind {Kind::none};
  std::optional<Value> payload {};
  std::string message {};

  Signal() = default;
  Signal(Kind signal_kind, std::optional<Value> signal_payload, std::string signal_message = {});
};

struct Frame {
  std::string name {};
  Object locals {};
};

struct Program {
  Object globals {};
  Object nodes {};
};

struct VM {
  enum class Status {
    ready,
    running,
    finished,
    failed,
  };

  std::vector<Frame> frames {};
  std::optional<Value> result {};
  Signal signal {};
  Status status {Status::ready};
};

}  // namespace helix
